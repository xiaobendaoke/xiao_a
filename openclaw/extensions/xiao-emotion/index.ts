import { promises as fs } from "node:fs";
import { execFile } from "node:child_process";
import { homedir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { emptyPluginConfigSchema } from "openclaw/plugin-sdk";

type MoodEntry = {
  value: number;
  updatedAt: number;
};

type EmotionStore = {
  moods: Record<string, MoodEntry>;
  profiles: Record<string, Record<string, string>>;
};

type QuietHoursConfig = {
  enabled: boolean;
  startHour: number;
  endHour: number;
  timezone: string;
};

type LegacyMemorySnapshot = {
  loadedAt: number;
  profile: Record<string, string>;
  insights: string[];
  recentChats: string[];
};

const DEFAULT_STORE: EmotionStore = {
  moods: {},
  profiles: {},
};

const SESSION_TO_USER_KEY = new Map<string, string>();
const USER_ALIAS_MAP = parseUserAliasMap(
  (process.env.XIAO_USER_ALIAS_MAP || process.env.XIAO_EMOTION_ALIAS_MAP || "").trim(),
);
const LEGACY_DB_PATH = (process.env.XIAO_LEGACY_DB_PATH || "/root/xiao_a/data.db").trim();
const LEGACY_CACHE = new Map<string, LegacyMemorySnapshot>();
const LEGACY_CACHE_TTL_MS = 10 * 60 * 1000;
const execFileAsync = promisify(execFile);

let storeCache: EmotionStore | null = null;
let writeQueue: Promise<void> = Promise.resolve();

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function resolveStateDir(): string {
  const explicit = (process.env.OPENCLAW_STATE_DIR || "").trim();
  if (explicit) {
    return explicit;
  }
  return path.join(homedir(), ".openclaw");
}

function resolveStoreFile(): string {
  return path.join(resolveStateDir(), "xiao-emotion", "state.json");
}

async function ensureStoreLoaded(): Promise<EmotionStore> {
  if (storeCache) {
    return storeCache;
  }

  const file = resolveStoreFile();
  try {
    const raw = await fs.readFile(file, "utf8");
    const parsed = JSON.parse(raw) as Partial<EmotionStore>;
    storeCache = {
      moods: parsed.moods && typeof parsed.moods === "object" ? parsed.moods : {},
      profiles: parsed.profiles && typeof parsed.profiles === "object" ? parsed.profiles : {},
    };
    return storeCache;
  } catch {
    storeCache = { ...DEFAULT_STORE };
    return storeCache;
  }
}

async function persistStore(): Promise<void> {
  const file = resolveStoreFile();
  const dir = path.dirname(file);
  writeQueue = writeQueue.then(async () => {
    await fs.mkdir(dir, { recursive: true });
    await fs.writeFile(file, JSON.stringify(storeCache ?? DEFAULT_STORE, null, 2), "utf8");
  });
  await writeQueue;
}

function inferRecipientId(text: string): string | null {
  const raw = (text || "").trim();
  if (!raw) {
    return null;
  }

  const qqScoped = raw.match(/\bqqbot:(?:c2c|group):([A-Za-z0-9._:-]{6,128})\b/i);
  if (qqScoped?.[1]) {
    return qqScoped[1];
  }

  const longHex = raw.match(/\b[A-Fa-f0-9]{24,64}\b/);
  if (longHex?.[0]) {
    return longHex[0];
  }

  const longDigits = raw.match(/\b\d{7,20}\b/);
  if (longDigits?.[0]) {
    return longDigits[0];
  }

  const parenthesized = raw.match(/\(([A-Za-z0-9._:-]{8,128})\)/);
  if (parenthesized?.[1]) {
    return parenthesized[1];
  }

  return null;
}

function resolveUserKeyFromPrompt(prompt: string, sessionKey?: string): string {
  const inferred = inferRecipientId(prompt);
  if (inferred) {
    return normalizeUserKey(`qqbot:${inferred}`);
  }
  if (sessionKey) {
    return `session:${sessionKey}`;
  }
  return "session:unknown";
}

function resolveUserKeyFromOutbound(ctx: { channelId: string; conversationId?: string }, to: string): string {
  const channel = (ctx.channelId || "unknown").trim();
  const conversation = (ctx.conversationId || "").trim();
  const target = (to || "").trim();
  if (target) {
    return normalizeUserKey(`${channel}:${target}`);
  }
  if (conversation) {
    return normalizeUserKey(`${channel}:${conversation}`);
  }
  return `${channel}:unknown`;
}

function normalizeUserKey(raw: string): string {
  const text = (raw || "").trim();
  if (!text) {
    return "session:unknown";
  }

  const qqWrapped = text.match(/^qqbot:qqbot:(?:c2c|group):([A-Za-z0-9._:-]{6,128})$/i);
  if (qqWrapped?.[1]) {
    return `qqbot:${normalizeQqIdentity(qqWrapped[1])}`;
  }

  const qqScoped = text.match(/^qqbot:(?:c2c|group):([A-Za-z0-9._:-]{6,128})$/i);
  if (qqScoped?.[1]) {
    return `qqbot:${normalizeQqIdentity(qqScoped[1])}`;
  }

  if (/^qqbot:/i.test(text)) {
    const id = text.slice("qqbot:".length);
    return `qqbot:${normalizeQqIdentity(id)}`;
  }

  if (/^[A-Za-z0-9._:-]{6,128}$/.test(text) && !text.includes(":")) {
    return `qqbot:${normalizeQqIdentity(text)}`;
  }

  return text;
}

function normalizeQqIdentity(raw: string): string {
  const text = (raw || "").trim();
  if (/^[A-Fa-f0-9]{24,64}$/.test(text)) {
    return text.toUpperCase();
  }
  return text;
}

function parseUserAliasMap(raw: string): Map<string, string> {
  const parsed = new Map<string, string>();
  if (!raw) {
    return parsed;
  }

  for (const token of raw.split(/[\n,;]+/)) {
    const part = token.trim();
    if (!part || !part.includes("=")) {
      continue;
    }
    const [leftRaw, rightRaw] = part.split("=", 2);
    const left = normalizeUserKey(leftRaw || "");
    const right = normalizeUserKey(rightRaw || "");
    if (!left || !right || left === right) {
      continue;
    }
    parsed.set(left, right);
  }
  return parsed;
}

function applyUserAlias(userKey: string): string {
  let current = normalizeUserKey(userKey);
  const visited = new Set<string>();

  while (USER_ALIAS_MAP.has(current) && !visited.has(current)) {
    visited.add(current);
    current = USER_ALIAS_MAP.get(current) || current;
  }

  return current;
}

function extractLegacyUserId(userKey: string): string | null {
  const normalized = normalizeUserKey(userKey);
  if (!normalized.startsWith("qqbot:")) {
    return null;
  }
  const id = normalized.slice("qqbot:".length).trim();
  return id || null;
}

function sqlEscapeLiteral(value: string): string {
  return value.replace(/'/g, "''");
}

function parseTwoColumnRows(stdout: string): Array<[string, string]> {
  return stdout
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0)
    .map((line) => {
      const [first, ...rest] = line.split("\t");
      return [first || "", rest.join("\t") || ""] as [string, string];
    });
}

async function runSqliteQuery(sql: string): Promise<string> {
  try {
    const result = await execFileAsync(
      "sqlite3",
      ["-noheader", "-separator", "\t", LEGACY_DB_PATH, sql],
      { timeout: 2000, maxBuffer: 2 * 1024 * 1024 },
    );
    return typeof result.stdout === "string" ? result.stdout : "";
  } catch {
    return "";
  }
}

async function loadLegacyMemory(userKey: string): Promise<LegacyMemorySnapshot | null> {
  if (!LEGACY_DB_PATH) {
    return null;
  }

  const legacyUserId = extractLegacyUserId(userKey);
  if (!legacyUserId) {
    return null;
  }

  const now = Date.now();
  const cached = LEGACY_CACHE.get(legacyUserId);
  if (cached && now - cached.loadedAt <= LEGACY_CACHE_TTL_MS) {
    return cached;
  }

  try {
    await fs.access(LEGACY_DB_PATH);
  } catch {
    return null;
  }

  const escapedUser = sqlEscapeLiteral(legacyUserId);
  const profileSql = `select key, value from user_profile where user_id='${escapedUser}' order by key limit 30;`;
  const insightsSql =
    "select insight_type, replace(replace(content, char(10), ' '), char(13), ' ') as content " +
    `from user_insights where user_id='${escapedUser}' ` +
    "order by confidence desc, last_updated_ts desc limit 8;";
  const chatSql =
    "select role, replace(replace(substr(content, 1, 120), char(10), ' '), char(13), ' ') " +
    `from chat_history where user_id='${escapedUser}' ` +
    "order by id desc limit 6;";

  const [profileOut, insightsOut, chatOut] = await Promise.all([
    runSqliteQuery(profileSql),
    runSqliteQuery(insightsSql),
    runSqliteQuery(chatSql),
  ]);

  const profile: Record<string, string> = {};
  for (const [k, v] of parseTwoColumnRows(profileOut)) {
    const key = k.trim();
    const value = v.trim();
    if (key && value) {
      profile[key] = value;
    }
  }

  const insights = parseTwoColumnRows(insightsOut)
    .map(([kind, text]) => `${kind.trim()}:${text.trim()}`)
    .filter((line) => line !== ":" && line.length > 2)
    .slice(0, 8);

  const recentChats = parseTwoColumnRows(chatOut)
    .reverse()
    .map(([role, text]) => `${role.trim()}:${text.trim()}`)
    .filter((line) => line !== ":" && line.length > 2)
    .slice(0, 6);

  if (Object.keys(profile).length === 0 && insights.length === 0 && recentChats.length === 0) {
    return null;
  }

  const snapshot: LegacyMemorySnapshot = {
    loadedAt: now,
    profile,
    insights,
    recentChats,
  };
  LEGACY_CACHE.set(legacyUserId, snapshot);
  return snapshot;
}

async function getMoodValue(userKey: string): Promise<number> {
  const store = await ensureStoreLoaded();
  const now = Date.now();

  const entry = store.moods[userKey] ?? { value: 0, updatedAt: now };
  if (!store.moods[userKey]) {
    store.moods[userKey] = entry;
    await persistStore();
    return entry.value;
  }

  const elapsedMinutes = Math.floor(Math.max(0, now - entry.updatedAt) / 60000);
  if (elapsedMinutes > 0 && entry.value !== 0) {
    if (entry.value > 0) {
      entry.value = Math.max(0, entry.value - elapsedMinutes);
    } else {
      entry.value = Math.min(0, entry.value + elapsedMinutes);
    }
    entry.updatedAt = now;
    await persistStore();
  }
  return entry.value;
}

async function adjustMood(userKey: string, delta: number): Promise<number> {
  const store = await ensureStoreLoaded();
  const now = Date.now();
  const current = await getMoodValue(userKey);
  const boundedDelta = clamp(Math.trunc(delta), -5, 5);
  const next = clamp(current + boundedDelta, -100, 100);

  store.moods[userKey] = {
    value: next,
    updatedAt: now,
  };
  await persistStore();
  return next;
}

function moodDescription(value: number): string {
  if (value >= 80) return "非常开心、亲密感强";
  if (value >= 30) return "积极、温柔";
  if (value >= -10) return "平静、放松";
  if (value >= -50) return "有点烦、稍微疏离";
  return "情绪低落、防御感较强";
}

function moodInstruction(value: number): string {
  if (value < -20) {
    return "硬性要求：回复更短、更克制，不要过度撒娇，但保持礼貌。";
  }
  return "";
}

function formatProfile(profile: Record<string, string> | undefined): string {
  if (!profile || Object.keys(profile).length === 0) {
    return "none";
  }
  return Object.entries(profile)
    .slice(0, 20)
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
}

function parseTagsAndClean(rawText: string): {
  cleanText: string;
  moodChange: number | null;
  profileUpdates: Array<{ key: string; value: string }>;
} {
  const raw = rawText || "";

  const moodValues: number[] = [];
  for (const match of raw.matchAll(/\[MOOD_CHANGE[:：]\s*(-?\d+)\s*\]/gi)) {
    const n = Number.parseInt(match[1] || "", 10);
    if (Number.isFinite(n)) {
      moodValues.push(n);
    }
  }

  const profileUpdates: Array<{ key: string; value: string }> = [];
  for (const match of raw.matchAll(/\[UPDATE_PROFILE[:：]\s*([^\]=:：]+?)\s*[=：:]\s*([^\]]+?)\s*\]/gi)) {
    const key = (match[1] || "").trim();
    const value = (match[2] || "").trim();
    if (key && value) {
      profileUpdates.push({ key, value });
    }
  }

  let cleaned = raw;
  cleaned = cleaned.replace(/\[MOOD_CHANGE[:：]\s*-?\d+\s*\]/gi, "");
  cleaned = cleaned.replace(/\[UPDATE_PROFILE[:：]\s*([^\]=:：]+?)\s*[=：:]\s*([^\]]+?)\s*\]/gi, "");
  cleaned = cleaned.replace(/\[[^\]]+\]/g, "");
  cleaned = cleaned
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter((line) => line.length > 0)
    .join("\n")
    .trim();

  return {
    cleanText: cleaned,
    moodChange: moodValues.length > 0 ? moodValues[moodValues.length - 1] : null,
    profileUpdates,
  };
}

async function applyProfileUpdates(userKey: string, updates: Array<{ key: string; value: string }>): Promise<void> {
  if (updates.length === 0) {
    return;
  }
  const store = await ensureStoreLoaded();
  const profile = store.profiles[userKey] ?? {};
  for (const item of updates) {
    profile[item.key] = item.value;
  }
  store.profiles[userKey] = profile;
  await persistStore();
}

function resolveQuietHours(pluginConfig: unknown): QuietHoursConfig {
  const defaultCfg: QuietHoursConfig = {
    enabled: true,
    startHour: 1,
    endHour: 6,
    timezone: "Asia/Shanghai",
  };

  if (!pluginConfig || typeof pluginConfig !== "object") {
    return defaultCfg;
  }

  const rawQuiet = (pluginConfig as Record<string, unknown>).quietHours;
  if (!rawQuiet || typeof rawQuiet !== "object") {
    return defaultCfg;
  }

  const q = rawQuiet as Record<string, unknown>;
  return {
    enabled: typeof q.enabled === "boolean" ? q.enabled : defaultCfg.enabled,
    startHour:
      typeof q.startHour === "number" && Number.isFinite(q.startHour)
        ? clamp(Math.floor(q.startHour), 0, 23)
        : defaultCfg.startHour,
    endHour:
      typeof q.endHour === "number" && Number.isFinite(q.endHour)
        ? clamp(Math.floor(q.endHour), 0, 23)
        : defaultCfg.endHour,
    timezone:
      typeof q.timezone === "string" && q.timezone.trim() ? q.timezone.trim() : defaultCfg.timezone,
  };
}

function isWithinQuietHours(cfg: QuietHoursConfig): boolean {
  if (!cfg.enabled) {
    return false;
  }

  const now = new Date();
  const hourText = new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    hour12: false,
    timeZone: cfg.timezone,
  }).format(now);
  const hour = Number.parseInt(hourText, 10);
  if (!Number.isFinite(hour)) {
    return false;
  }

  if (cfg.startHour === cfg.endHour) {
    return false;
  }
  if (cfg.startHour < cfg.endHour) {
    return hour >= cfg.startHour && hour < cfg.endHour;
  }
  return hour >= cfg.startHour || hour < cfg.endHour;
}

const xiaoEmotionPlugin = {
  id: "xiao-emotion",
  name: "Xiao Emotion",
  description: "Mood and profile hooks with tag-based persistence",
  configSchema: emptyPluginConfigSchema(),
  register(api: OpenClawPluginApi) {
    // 解析插件级别的静默防扰配置（如夜间免打扰时间段）
    const quietHours = resolveQuietHours(api.pluginConfig);

    // 挂载 Agent 启动前置钩子：在 LLM 生成回复前，注入情绪、记忆与画像上下文
    api.on("before_agent_start", async (event, ctx) => {
      // 通过上下文反解析真实用户标识，并应用当前别名映射获取统一身份
      const resolvedKey = resolveUserKeyFromPrompt(event.prompt || "", ctx.sessionKey);
      const userKey = applyUserAlias(resolvedKey);
      if (ctx.sessionKey) {
        SESSION_TO_USER_KEY.set(ctx.sessionKey, userKey);
      }

      // 获取当前用户的情绪分值、画像与旧版遗留数据进行组合
      const mood = await getMoodValue(userKey);
      const store = await ensureStoreLoaded();
      const profile = store.profiles[userKey];
      const legacy = await loadLegacyMemory(userKey);
      const mergedProfile = {
        ...(legacy?.profile || {}),
        ...(profile || {}),
      };

      // 判断是否处于安静免打扰时段
      const quietMode = isWithinQuietHours(quietHours);

      // 构建要插入给 LLM 的 system prompt 上下文内容
      const lines: string[] = [];
      lines.push("XIAO_EMOTION_CONTEXT");
      lines.push(`user_key=${userKey}`);
      if (resolvedKey !== userKey) {
        lines.push(`user_key_alias_from=${resolvedKey}`);
      }
      lines.push(`mood_value=${mood}`);
      lines.push(`mood_desc=${moodDescription(mood)}`);
      lines.push(`profile=${formatProfile(mergedProfile)}`);
      if (legacy?.insights.length) {
        lines.push(`legacy_insights=${legacy.insights.join(" | ")}`);
      }
      if (legacy?.recentChats.length) {
        lines.push("legacy_recent_chat=");
        for (const line of legacy.recentChats) {
          lines.push(line);
        }
      }

      // 注入严格的基调设定和标签交互协议
      lines.push("请维持小a语气：自然、口语化、避免客服腔。");
      lines.push("需要更新状态时，在末尾追加标签：");
      lines.push("[MOOD_CHANGE:x] 其中 x 范围 -3..3");
      lines.push("[UPDATE_PROFILE:key=value]");

      // 根据当前情绪强制修改基调
      const forced = moodInstruction(mood);
      if (forced) {
        lines.push(forced);
      }

      // 安静时段下追加更短更轻的回复要求
      if (quietMode) {
        lines.push("当前在安静时段：回复更短、更轻声。");
      }

      return {
        prependContext: lines.join("\n"),
      };
    });

    // 挂载消息发送钩子：拦截并解析 LLM 返回数据中可能潜藏的状态变更标签
    api.on("message_sending", async (event, ctx) => {
      const content = typeof event.content === "string" ? event.content : "";
      if (!content) {
        return;
      }

      // 回溯当前所属的用户映射
      const keyFromSession = SESSION_TO_USER_KEY.get((ctx as { sessionKey?: string }).sessionKey || "");
      const userKey =
        keyFromSession ||
        applyUserAlias(resolveUserKeyFromOutbound({ channelId: ctx.channelId, conversationId: ctx.conversationId }, event.to));

      // 提取回复正文内容，并将标签（如果存在）执行处理后剥离出原始回复文本
      const parsed = parseTagsAndClean(content);

      // 如果发生情绪更新，同步调整状态
      if (parsed.moodChange !== null) {
        await adjustMood(userKey, clamp(parsed.moodChange, -3, 3));
      }

      // 如果发生用户画像资料更新，同步更新信息块
      if (parsed.profileUpdates.length > 0) {
        await applyProfileUpdates(userKey, parsed.profileUpdates);
      }

      // 若有截取掉标签，则需要向渠道返还清洗干净的正文，避免泄漏调试标记
      if (parsed.cleanText !== content) {
        return {
          content: parsed.cleanText || "...",
        };
      }
      return;
    });

    // 注册 /mood 命令，用于管理与查看情绪/画像状态
    api.registerCommand({
      name: "mood",
      description: "Show or adjust mood state. Usage: /mood [status|set N|add N|profile]",
      acceptsArgs: true,
      handler: async (ctx) => {
        const actor =
          (ctx.from && String(ctx.from).trim()) ||
          (ctx.senderId && String(ctx.senderId).trim()) ||
          "default";
        const userKey = applyUserAlias(`${ctx.channel}:${actor}`);
        const args = (ctx.args || "").trim();

        // 缺省参数或传递 status，展示当前情绪看板
        if (!args || args === "status") {
          const mood = await getMoodValue(userKey);
          return {
            text: `mood=${mood}\ndesc=${moodDescription(mood)}\nkey=${userKey}`,
          };
        }

        // 调用 profile 以查阅固化的资料片段
        if (args === "profile") {
          const store = await ensureStoreLoaded();
          const profile = store.profiles[userKey] || {};
          const text = Object.keys(profile).length
            ? Object.entries(profile)
              .map(([k, v]) => `${k}=${v}`)
              .join("\n")
            : "profile is empty";
          return { text };
        }

        // 强行设值操作（如 set -10）
        const setMatch = args.match(/^set\s+(-?\d+)$/i);
        if (setMatch?.[1]) {
          const store = await ensureStoreLoaded();
          const next = clamp(Number.parseInt(setMatch[1], 10), -100, 100);
          store.moods[userKey] = {
            value: next,
            updatedAt: Date.now(),
          };
          await persistStore();
          return { text: `mood set to ${next}` };
        }

        // 差值变更（如 add 5）
        const addMatch = args.match(/^add\s+(-?\d+)$/i);
        if (addMatch?.[1]) {
          const delta = clamp(Number.parseInt(addMatch[1], 10), -10, 10);
          const next = await adjustMood(userKey, delta);
          return { text: `mood changed by ${delta}, now=${next}` };
        }

        return {
          text: "usage: /mood [status|set N|add N|profile]",
        };
      },
    });
  },
};

export default xiaoEmotionPlugin;
