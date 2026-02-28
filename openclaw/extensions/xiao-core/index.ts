import { execFile } from "node:child_process";
import { readFileSync, statSync, existsSync, promises as fs } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import type { AnyAgentTool, OpenClawPluginApi } from "openclaw/plugin-sdk";
import { emptyPluginConfigSchema } from "openclaw/plugin-sdk";

const execFileAsync = promisify(execFile);

type ToolResult = {
  content: Array<{ type: "text"; text: string }>;
  details: unknown;
};

type SessionSnapshot = {
  resolvedUserKey: string;
  aliasFrom?: string;
  seenAt: number;
  promptPreview: string;
  userInput?: string;
  userInputRecorded?: boolean;
};

type MemoryNote = {
  text: string;
  ts: number;
  source: "explicit" | "derived";
};

type ChatEntry = {
  role: "user" | "assistant";
  text: string;
  ts: number;
};

type CoreState = {
  notes: Record<string, MemoryNote[]>;
  chats: Record<string, ChatEntry[]>;
  links: Record<string, LinkEvidence[]>;
};

type RagHit = {
  score: number;
  ts: number;
  text: string;
  from: "note" | "chat";
};

type PendingUrl = {
  url: string;
  seenAt: number;
  sourceInput: string;
};

type LinkEvidence = {
  url: string;
  ts: number;
  source: "user" | "assistant";
  context: string;
};

const STARTED_AT = Date.now();
const SESSION_USER_MAP = new Map<string, SessionSnapshot>();
const PENDING_URL_BY_USER = new Map<string, PendingUrl>();
const SESSION_TTL_MS = 6 * 60 * 60 * 1000;
const SESSION_MAX_SIZE = 2000;
const PENDING_URL_TTL_MS = 10 * 60 * 1000;

const MAX_NOTE_LEN = 300;
const MAX_NOTES_PER_USER = 200;
const MAX_CHAT_LEN = 400;
const MAX_CHATS_PER_USER = 240;
const MAX_LINKS_PER_USER = 80;

const DEFAULT_CORE_STATE: CoreState = {
  notes: {},
  chats: {},
  links: {},
};

let envCache: Record<string, string> | null = null;
let envMtimeMs = -1;
let stateCache: CoreState | null = null;
let stateWriteQueue: Promise<void> = Promise.resolve();

function jsonResult(payload: unknown): ToolResult {
  return {
    content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
    details: payload,
  };
}

function resolveEnvFilePath(): string {
  const fromEnv = (process.env.XIAO_ENV_FILE || "").trim();
  if (fromEnv) {
    return fromEnv;
  }
  return path.join(homedir(), ".openclaw", ".env");
}

function resolveStateFilePath(): string {
  const fromEnv = (process.env.XIAO_CORE_STATE_FILE || "").trim();
  if (fromEnv) {
    return fromEnv;
  }
  return path.join(homedir(), ".openclaw", "xiao-core", "state.json");
}

function unquoteEnvValue(value: string): string {
  const v = value.trim();
  if (
    (v.startsWith('"') && v.endsWith('"') && v.length >= 2) ||
    (v.startsWith("'") && v.endsWith("'") && v.length >= 2)
  ) {
    return v.slice(1, -1).trim();
  }
  return v;
}

function loadEnvFile(): Record<string, string> {
  const file = resolveEnvFilePath();
  if (!existsSync(file)) {
    envCache = {};
    envMtimeMs = -1;
    return envCache;
  }

  try {
    const stat = statSync(file);
    if (envCache && envMtimeMs === stat.mtimeMs) {
      return envCache;
    }

    const content = readFileSync(file, "utf8");
    const parsed: Record<string, string> = {};
    for (const rawLine of content.split(/\r?\n/)) {
      const line = rawLine.trim();
      if (!line || line.startsWith("#")) {
        continue;
      }
      const idx = line.indexOf("=");
      if (idx <= 0) {
        continue;
      }
      const key = line.slice(0, idx).trim();
      const value = unquoteEnvValue(line.slice(idx + 1));
      if (!key) {
        continue;
      }
      parsed[key] = value;
    }

    envCache = parsed;
    envMtimeMs = stat.mtimeMs;
    return parsed;
  } catch {
    envCache = {};
    envMtimeMs = -1;
    return envCache;
  }
}

function env(name: string): string {
  const runtime = (process.env[name] || "").trim();
  if (runtime) {
    return runtime;
  }
  const fromFile = loadEnvFile();
  return (fromFile[name] || "").trim();
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function shorten(text: string, maxLen: number): string {
  const t = (text || "").replace(/\s+/g, " ").trim();
  if (t.length <= maxLen) {
    return t;
  }
  return `${t.slice(0, maxLen)}...`;
}

function cleanAssistantText(text: string): string {
  let cleaned = (text || "").trim();
  cleaned = cleaned.replace(/\[MOOD_CHANGE[:：]\s*-?\d+\s*\]/gi, "");
  cleaned = cleaned.replace(/\[UPDATE_PROFILE[:：]\s*[^\]]+\]/gi, "");
  cleaned = cleaned.replace(/\s+$/g, "");
  return cleaned.trim();
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

function normalizeQqIdentity(raw: string): string {
  const text = (raw || "").trim();
  if (/^[A-Fa-f0-9]{24,64}$/.test(text)) {
    return text.toUpperCase();
  }
  return text;
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
    return `qqbot:${normalizeQqIdentity(text.slice("qqbot:".length))}`;
  }

  if (/^[A-Za-z0-9._:-]{6,128}$/.test(text) && !text.includes(":")) {
    return `qqbot:${normalizeQqIdentity(text)}`;
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

function applyAlias(rawUserKey: string): { resolved: string; aliasFrom?: string } {
  const aliasRaw = env("XIAO_USER_ALIAS_MAP") || env("XIAO_EMOTION_ALIAS_MAP") || "";
  const aliasMap = parseUserAliasMap(aliasRaw);

  let current = normalizeUserKey(rawUserKey);
  const origin = current;
  const visited = new Set<string>();

  while (aliasMap.has(current) && !visited.has(current)) {
    visited.add(current);
    current = aliasMap.get(current) || current;
  }

  if (current !== origin) {
    return { resolved: current, aliasFrom: origin };
  }
  return { resolved: current };
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

function envStatus(name: string): "set" | "missing" {
  return env(name) ? "set" : "missing";
}

function formatUptimeSec(): number {
  return Math.floor((Date.now() - STARTED_AT) / 1000);
}

function sweepSessionCache(now: number): void {
  for (const [k, v] of SESSION_USER_MAP.entries()) {
    if (now - v.seenAt > SESSION_TTL_MS) {
      SESSION_USER_MAP.delete(k);
    }
  }
  if (SESSION_USER_MAP.size <= SESSION_MAX_SIZE) {
    return;
  }

  const sorted = [...SESSION_USER_MAP.entries()].sort((a, b) => a[1].seenAt - b[1].seenAt);
  const overflow = SESSION_USER_MAP.size - SESSION_MAX_SIZE;
  for (let i = 0; i < overflow; i += 1) {
    const key = sorted[i]?.[0];
    if (key) {
      SESSION_USER_MAP.delete(key);
    }
  }
}

function extractUserInput(prompt: string): string {
  const src = (prompt || "").trim();
  if (!src) {
    return "";
  }

  const patterns = [
    /(?:^|\n)(?:用户输入|用户|User|USER|message|Message)\s*[：:]\s*(.+)$/gim,
    /(?:^|\n)(?:任务|问题|query)\s*[：:]\s*(.+)$/gim,
  ];

  for (const p of patterns) {
    let m: RegExpExecArray | null = null;
    let last: RegExpExecArray | null = null;
    while ((m = p.exec(src)) !== null) {
      last = m;
    }
    const candidate = (last?.[1] || "").trim();
    if (candidate) {
      return shorten(candidate, 800);
    }
  }

  const lines = src
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => !!line);
  if (lines.length === 0) {
    return "";
  }
  return shorten(lines[lines.length - 1] || "", 800);
}

function extractExplicitMemory(input: string): string | null {
  const text = (input || "").trim();
  if (!text) {
    return null;
  }

  const prefixes = ["记住：", "记住:", "请记住：", "请记住:", "备忘：", "备忘:"];
  for (const prefix of prefixes) {
    if (text.startsWith(prefix)) {
      const payload = text.slice(prefix.length).trim();
      return payload ? shorten(payload, MAX_NOTE_LEN) : null;
    }
  }
  return null;
}

function hasWeatherIntent(input: string): boolean {
  const t = (input || "").toLowerCase();
  return ["天气", "气温", "下雨", "降雨", "温度", "weather", "forecast"].some((k) => t.includes(k));
}

function hasStockIntent(input: string): boolean {
  const t = (input || "").toLowerCase();
  return ["查股", "股票", "股价", "a股", "港股", "美股", "stock", "ticker"].some((k) => t.includes(k));
}

function hasGithubTrendingIntent(input: string): boolean {
  const t = (input || "").toLowerCase();
  return ["github周榜", "github 热榜", "github trending", "trending", "开源周榜"].some((k) => t.includes(k));
}

function extractUrls(input: string): string[] {
  const text = (input || "").trim();
  if (!text) return [];
  const matches = text.match(/https?:\/\/[^\s<>"'`，。！？、]+/gi) || [];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const m of matches) {
    const url = m.trim();
    if (!url || seen.has(url)) continue;
    seen.add(url);
    out.push(url);
    if (out.length >= 5) break;
  }
  return out;
}

function hasUrlSummaryIntent(input: string): boolean {
  const t = (input || "").toLowerCase();
  if (!t) return false;
  const keys = [
    "总结",
    "概括",
    "提炼",
    "看下",
    "解读",
    "这链接讲了啥",
    "这篇讲了啥",
    "summarize",
    "summary",
  ];
  return keys.some((k) => t.includes(k));
}

function hasSourceFollowupIntent(input: string): boolean {
  const t = (input || "").toLowerCase();
  if (!t) return false;
  const keys = [
    "来源",
    "链接",
    "出处",
    "原文",
    "参考",
    "发我链接",
    "给我链接",
    "source",
    "link",
  ];
  return keys.some((k) => t.includes(k));
}

function normalizeEvidenceUrl(url: string): string {
  const raw = (url || "").trim();
  if (!raw) return "";
  try {
    const u = new URL(raw);
    if (!/^https?:$/i.test(u.protocol)) return "";
    return u.toString();
  } catch {
    return "";
  }
}

function sweepPendingUrlCache(now: number): void {
  for (const [k, v] of PENDING_URL_BY_USER.entries()) {
    if (now - v.seenAt > PENDING_URL_TTL_MS) {
      PENDING_URL_BY_USER.delete(k);
    }
  }
}

function setPendingUrl(userKey: string, url: string, sourceInput: string): void {
  const key = normalizeUserKey(userKey);
  if (!key) return;
  PENDING_URL_BY_USER.set(key, {
    url: shorten(url, 600),
    seenAt: Date.now(),
    sourceInput: shorten(sourceInput, 240),
  });
}

function getPendingUrl(userKey: string): PendingUrl | null {
  const key = normalizeUserKey(userKey);
  const p = PENDING_URL_BY_USER.get(key);
  if (!p) return null;
  if (Date.now() - p.seenAt > PENDING_URL_TTL_MS) {
    PENDING_URL_BY_USER.delete(key);
    return null;
  }
  return p;
}

function parseReminderArgs(raw: string): { minutes: number; content: string } | null {
  const text = (raw || "").trim();
  if (!text) {
    return null;
  }

  const patterns = [
    /^(\d{1,5})\s+(.+)$/,
    /^(\d{1,5})m(?:in)?\s+(.+)$/i,
    /^(\d{1,5})\s*(?:分钟|分)\s*(?:后)?\s+(.+)$/i,
  ];

  for (const p of patterns) {
    const m = text.match(p);
    if (!m?.[1] || !m?.[2]) {
      continue;
    }
    const minutes = clamp(Number(m[1]), 1, 43200);
    const content = shorten(m[2], 240).trim();
    if (!content) {
      return null;
    }
    return { minutes, content };
  }
  return null;
}

function parseReminderIntent(input: string): { minutes: number; content: string } | null {
  const text = (input || "").trim();
  if (!text) {
    return null;
  }

  const minDirect = text.match(/提醒我\s*(\d{1,4})\s*(分钟|分|min)\s*后?\s*(.+)$/i);
  if (minDirect?.[1] && minDirect?.[3]) {
    return {
      minutes: clamp(Number(minDirect[1]), 1, 43200),
      content: shorten(minDirect[3], 240),
    };
  }

  const hourDirect = text.match(/提醒我\s*(\d{1,3})\s*(小时|时|h|hour)\s*后?\s*(.+)$/i);
  if (hourDirect?.[1] && hourDirect?.[3]) {
    return {
      minutes: clamp(Number(hourDirect[1]) * 60, 1, 43200),
      content: shorten(hourDirect[3], 240),
    };
  }

  const minInvert = text.match(/(\d{1,4})\s*(分钟|分|min)\s*后?\s*提醒我\s*(.+)$/i);
  if (minInvert?.[1] && minInvert?.[3]) {
    return {
      minutes: clamp(Number(minInvert[1]), 1, 43200),
      content: shorten(minInvert[3], 240),
    };
  }

  return null;
}

function reminderTargetFromUserKey(userKey: string): string | null {
  const m = (userKey || "").trim().match(/^qqbot:([A-Za-z0-9._:-]{6,128})$/);
  if (!m?.[1]) {
    return null;
  }
  return `qqbot:c2c:${m[1]}`;
}

function extractJsonPayload(raw: string): unknown {
  const text = (raw || "").trim();
  if (!text) {
    return {};
  }

  const fromBrace = text.slice(Math.max(0, text.indexOf("{"))).trim();
  if (fromBrace.startsWith("{")) {
    try {
      return JSON.parse(fromBrace);
    } catch {
      // no-op
    }
  }

  const fromBracket = text.slice(Math.max(0, text.indexOf("["))).trim();
  if (fromBracket.startsWith("[")) {
    try {
      return JSON.parse(fromBracket);
    } catch {
      // no-op
    }
  }

  return { raw: text.slice(0, 600) };
}

function resolveQqTargetFromCtx(ctx: {
  channel?: string;
  from?: string;
  senderId?: string;
  conversationId?: string;
}): string | null {
  if ((ctx.channel || "").trim() !== "qqbot") {
    return null;
  }

  const conv = (ctx.conversationId || "").trim();
  const scoped = conv.match(/qqbot:(c2c|group):([A-Za-z0-9._:-]{6,128})/i);
  if (scoped?.[1] && scoped?.[2]) {
    return `qqbot:${scoped[1].toLowerCase()}:${scoped[2]}`;
  }

  const actor = (ctx.from || "").trim() || (ctx.senderId || "").trim();
  if (!actor) {
    return null;
  }
  return `qqbot:c2c:${actor}`;
}

function inferCityFromInput(input: string): string | null {
  const text = (input || "").trim();
  if (!text) {
    return null;
  }

  const m1 = text.match(/(?:查|看|问|告诉我|知道|今天|明天|后天|现在)?([\p{Script=Han}]{2,8})(?:天气|气温|温度|下雨|降雨)/u);
  if (m1?.[1]) {
    return m1[1];
  }

  const m2 = text.match(/([\p{Script=Han}]{2,8})(?:今天|明天|后天)?(?:冷不冷|热不热)/u);
  if (m2?.[1]) {
    return m2[1];
  }

  return null;
}

function inferStockSymbol(input: string): string | null {
  const text = (input || "").toUpperCase().trim();
  if (!text) {
    return null;
  }

  const m1 = text.match(/\b(SH|SZ)\d{6}\b/);
  if (m1?.[0]) {
    return m1[0];
  }

  const m2 = text.match(/\b\d{6}\.(SH|SZ)\b/);
  if (m2?.[0]) {
    const [code, market] = m2[0].split(".");
    return `${market}${code}`;
  }

  const m3 = text.match(/\b\d{6}\b/);
  if (!m3?.[0]) {
    return null;
  }

  const code = m3[0];
  if (/^[6895]/.test(code)) {
    return `SH${code}`;
  }
  return `SZ${code}`;
}

function weatherCodeToText(code: number): string {
  const c = Math.trunc(code);
  if (c === 0) return "晴";
  if (c === 1 || c === 2 || c === 3) return "多云";
  if (c === 45 || c === 48) return "雾";
  if (c >= 51 && c <= 57) return "毛毛雨";
  if (c >= 61 && c <= 67) return "雨";
  if (c >= 71 && c <= 77) return "雪";
  if (c >= 80 && c <= 82) return "阵雨";
  if (c >= 95 && c <= 99) return "雷雨";
  return "未知";
}

async function fetchJsonWithTimeout(url: string, timeoutMs: number): Promise<unknown> {
  const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${shorten(t, 120)}`);
  }
  return await res.json();
}

async function fetchWeatherSummary(city: string): Promise<string | null> {
  try {
    const geoUrl = new URL("https://geocoding-api.open-meteo.com/v1/search");
    geoUrl.searchParams.set("name", city);
    geoUrl.searchParams.set("count", "1");
    geoUrl.searchParams.set("language", "zh");
    geoUrl.searchParams.set("format", "json");

    const geo = (await fetchJsonWithTimeout(geoUrl.toString(), 7000)) as {
      results?: Array<{ name?: string; country?: string; latitude?: number; longitude?: number }>;
    };
    const loc = geo.results?.[0];
    if (!loc || typeof loc.latitude !== "number" || typeof loc.longitude !== "number") {
      return null;
    }

    const forecastUrl = new URL("https://api.open-meteo.com/v1/forecast");
    forecastUrl.searchParams.set("latitude", String(loc.latitude));
    forecastUrl.searchParams.set("longitude", String(loc.longitude));
    forecastUrl.searchParams.set("current", "temperature_2m,apparent_temperature,weather_code,wind_speed_10m");
    forecastUrl.searchParams.set(
      "daily",
      "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    );
    forecastUrl.searchParams.set("timezone", "Asia/Shanghai");

    const fc = (await fetchJsonWithTimeout(forecastUrl.toString(), 9000)) as {
      current?: {
        temperature_2m?: number;
        apparent_temperature?: number;
        weather_code?: number;
        wind_speed_10m?: number;
      };
      daily?: {
        weather_code?: number[];
        temperature_2m_max?: number[];
        temperature_2m_min?: number[];
        precipitation_probability_max?: number[];
      };
    };

    const cur = fc.current || {};
    const daily = fc.daily || {};
    const todayCode = Number(daily.weather_code?.[0] ?? cur.weather_code ?? -1);
    const todayMax = Number(daily.temperature_2m_max?.[0] ?? NaN);
    const todayMin = Number(daily.temperature_2m_min?.[0] ?? NaN);
    const pop = Number(daily.precipitation_probability_max?.[0] ?? NaN);
    const nowTemp = Number(cur.temperature_2m ?? NaN);

    const pieces: string[] = [];
    pieces.push(`城市=${loc.name || city}`);
    if (Number.isFinite(nowTemp)) {
      pieces.push(`当前温度=${nowTemp.toFixed(1)}C`);
    }
    if (Number.isFinite(todayMin) && Number.isFinite(todayMax)) {
      pieces.push(`今日温度=${todayMin.toFixed(1)}~${todayMax.toFixed(1)}C`);
    }
    if (Number.isFinite(todayCode) && todayCode >= 0) {
      pieces.push(`天气=${weatherCodeToText(todayCode)}(code=${todayCode})`);
    }
    if (Number.isFinite(pop)) {
      pieces.push(`降雨概率=${Math.max(0, Math.round(pop))}%`);
    }
    return pieces.join("；");
  } catch {
    return null;
  }
}

function resolveEastmoneySecid(symbol: string): string | null {
  const m = (symbol || "").trim().toUpperCase().match(/^(SH|SZ)(\d{6})$/);
  if (!m) {
    return null;
  }
  const market = m[1];
  const code = m[2];
  return `${market === "SH" ? "1" : "0"}.${code}`;
}

function asNum(v: unknown): number | null {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function priceFromCent(v: unknown): string {
  const n = asNum(v);
  if (n === null) return "-";
  return (n / 100).toFixed(2);
}

async function fetchStockSummary(symbol: string): Promise<string | null> {
  try {
    const secid = resolveEastmoneySecid(symbol);
    if (!secid) {
      return null;
    }

    const url = new URL("https://push2.eastmoney.com/api/qt/stock/get");
    url.searchParams.set("secid", secid);
    url.searchParams.set("fields", "f57,f58,f43,f44,f45,f46,f47,f48,f170,f169,f60");

    const resp = (await fetchJsonWithTimeout(url.toString(), 9000)) as {
      data?: Record<string, unknown>;
    };
    const d = resp.data;
    if (!d) {
      return null;
    }

    const name = String(d.f58 || symbol);
    const code = String(d.f57 || symbol.slice(2));
    const price = priceFromCent(d.f43);
    const high = priceFromCent(d.f44);
    const low = priceFromCent(d.f45);
    const open = priceFromCent(d.f46);
    const pctRaw = asNum(d.f170);
    const chgRaw = asNum(d.f169);
    const pct = pctRaw === null ? "-" : `${(pctRaw / 100).toFixed(2)}%`;
    const chg = chgRaw === null ? "-" : (chgRaw / 100).toFixed(2);

    const pieces: string[] = [];
    pieces.push(`标的=${name}(${code})`);
    pieces.push(`现价=${price}`);
    pieces.push(`涨跌=${chg} (${pct})`);
    pieces.push(`开盘=${open}`);
    pieces.push(`最高/最低=${high}/${low}`);
    return pieces.join("；");
  } catch {
    return null;
  }
}

function tokenize(text: string): string[] {
  const t = (text || "").toLowerCase();
  const raw = t.match(/[\p{Script=Han}A-Za-z0-9_]{1,}/gu) || [];
  const tokens = raw
    .map((s) => s.trim())
    .filter((s) => s.length > 1)
    .slice(0, 128);
  return [...new Set(tokens)];
}

function overlapScore(aTokens: string[], bTokens: string[]): number {
  if (aTokens.length === 0 || bTokens.length === 0) {
    return 0;
  }
  const bSet = new Set(bTokens);
  let hit = 0;
  for (const t of aTokens) {
    if (bSet.has(t)) {
      hit += 1;
    }
  }
  return hit;
}

async function ensureStateLoaded(): Promise<CoreState> {
  if (stateCache) {
    return stateCache;
  }

  const stateFile = resolveStateFilePath();
  try {
    const raw = await fs.readFile(stateFile, "utf8");
    const parsed = JSON.parse(raw) as Partial<CoreState>;
    stateCache = {
      notes: parsed.notes && typeof parsed.notes === "object" ? parsed.notes : {},
      chats: parsed.chats && typeof parsed.chats === "object" ? parsed.chats : {},
      links: parsed.links && typeof parsed.links === "object" ? parsed.links : {},
    };
    return stateCache;
  } catch {
    stateCache = {
      notes: {},
      chats: {},
      links: {},
    };
    return stateCache;
  }
}

async function persistState(): Promise<void> {
  const stateFile = resolveStateFilePath();
  const dir = path.dirname(stateFile);

  stateWriteQueue = stateWriteQueue.then(async () => {
    await fs.mkdir(dir, { recursive: true });
    const payload = stateCache || DEFAULT_CORE_STATE;
    await fs.writeFile(stateFile, JSON.stringify(payload, null, 2), "utf8");
  });

  await stateWriteQueue;
}

async function addMemoryNote(userKey: string, text: string, source: "explicit" | "derived"): Promise<void> {
  const normalized = normalizeUserKey(userKey);
  if (!normalized || !text.trim()) {
    return;
  }

  const store = await ensureStateLoaded();
  const arr = store.notes[normalized] || [];
  arr.push({
    text: shorten(text, MAX_NOTE_LEN),
    ts: Date.now(),
    source,
  });

  if (arr.length > MAX_NOTES_PER_USER) {
    arr.splice(0, arr.length - MAX_NOTES_PER_USER);
  }
  store.notes[normalized] = arr;
  await persistState();
}

async function addChatEntry(userKey: string, role: "user" | "assistant", text: string): Promise<void> {
  const normalized = normalizeUserKey(userKey);
  const clean = shorten(text, MAX_CHAT_LEN).trim();
  if (!normalized || !clean) {
    return;
  }

  const store = await ensureStateLoaded();
  const arr = store.chats[normalized] || [];
  arr.push({
    role,
    text: clean,
    ts: Date.now(),
  });

  if (arr.length > MAX_CHATS_PER_USER) {
    arr.splice(0, arr.length - MAX_CHATS_PER_USER);
  }
  store.chats[normalized] = arr;
  await persistState();
}

async function getRecentChats(userKey: string, limit: number): Promise<ChatEntry[]> {
  const store = await ensureStateLoaded();
  const arr = (store.chats[normalizeUserKey(userKey)] || []).slice();
  return arr.slice(Math.max(0, arr.length - limit));
}

async function getRecentNotes(userKey: string, limit: number): Promise<MemoryNote[]> {
  const store = await ensureStateLoaded();
  const arr = (store.notes[normalizeUserKey(userKey)] || []).slice();
  return arr.slice(Math.max(0, arr.length - limit));
}

async function addLinkEvidence(
  userKey: string,
  source: "user" | "assistant",
  url: string,
  context: string,
): Promise<void> {
  const normalizedUser = normalizeUserKey(userKey);
  const normalizedUrl = normalizeEvidenceUrl(url);
  if (!normalizedUser || !normalizedUrl) {
    return;
  }
  const store = await ensureStateLoaded();
  const arr = store.links[normalizedUser] || [];
  const now = Date.now();

  // Move same URL to tail to represent latest evidence.
  const dedup = arr.filter((x) => x.url !== normalizedUrl);
  dedup.push({
    url: normalizedUrl,
    ts: now,
    source,
    context: shorten(context || "", 180),
  });
  if (dedup.length > MAX_LINKS_PER_USER) {
    dedup.splice(0, dedup.length - MAX_LINKS_PER_USER);
  }
  store.links[normalizedUser] = dedup;
  await persistState();
}

async function getRecentLinks(userKey: string, limit: number): Promise<LinkEvidence[]> {
  const store = await ensureStateLoaded();
  const arr = (store.links[normalizeUserKey(userKey)] || []).slice();
  arr.sort((a, b) => Number(a.ts || 0) - Number(b.ts || 0));
  return arr.slice(Math.max(0, arr.length - clamp(limit, 1, 12)));
}

async function retrieveRagHits(userKey: string, query: string, limit: number): Promise<RagHit[]> {
  const store = await ensureStateLoaded();
  const normalized = normalizeUserKey(userKey);
  const q = shorten(query, 400);
  const qTokens = tokenize(q);

  const hits: RagHit[] = [];

  for (const n of store.notes[normalized] || []) {
    const tokens = tokenize(n.text);
    const score = overlapScore(qTokens, tokens);
    if (score > 0) {
      hits.push({ score, ts: n.ts, text: n.text, from: "note" });
    }
  }

  for (const c of store.chats[normalized] || []) {
    const tokens = tokenize(c.text);
    const score = overlapScore(qTokens, tokens);
    if (score > 0) {
      hits.push({ score, ts: c.ts, text: `${c.role}: ${c.text}`, from: "chat" });
    }
  }

  hits.sort((a, b) => {
    if (b.score !== a.score) {
      return b.score - a.score;
    }
    return b.ts - a.ts;
  });

  return hits.slice(0, clamp(limit, 1, 8));
}

const REFLECTION_STOPWORDS = new Set([
  "今天",
  "明天",
  "后天",
  "这个",
  "那个",
  "然后",
  "就是",
  "感觉",
  "一下",
  "现在",
  "我们",
  "你们",
  "他们",
  "因为",
  "所以",
  "但是",
  "如果",
  "还是",
  "已经",
  "可以",
  "不要",
  "你好",
  "哈哈",
  "嗯嗯",
  "好的",
  "知道",
  "谢谢",
]);

function isLowSignalText(text: string): boolean {
  const t = (text || "").trim();
  if (!t) return true;
  if (t.length <= 2) return true;
  const low = ["早", "晚安", "哈哈", "嗯", "哦", "ok", "收到", "在吗", "好吧"];
  return low.some((k) => t.toLowerCase() === k || t.includes(k));
}

function summarizeForReflection(chats: ChatEntry[], hours: number): string | null {
  if (!Array.isArray(chats) || chats.length === 0) {
    return null;
  }
  const cutoff = Date.now() - clamp(hours, 1, 168) * 3600 * 1000;
  const recent = chats.filter((x) => Number(x.ts || 0) >= cutoff);
  const userTexts = recent
    .filter((x) => x.role === "user")
    .map((x) => (x.text || "").trim())
    .filter((t) => !!t && !isLowSignalText(t));
  if (userTexts.length < 3) {
    return null;
  }

  const tokenFreq = new Map<string, number>();
  for (const text of userTexts) {
    for (const tok of tokenize(text)) {
      if (tok.length < 2 || tok.length > 20) continue;
      if (REFLECTION_STOPWORDS.has(tok)) continue;
      if (/^\d+$/.test(tok)) continue;
      tokenFreq.set(tok, (tokenFreq.get(tok) || 0) + 1);
    }
  }

  const topics = [...tokenFreq.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([k]) => k);

  const recentSignals = userTexts
    .slice(-3)
    .map((t) => shorten(t, 36))
    .filter(Boolean);

  const parts: string[] = [];
  parts.push(`用户近${hours}小时有${userTexts.length}条有效表达。`);
  if (topics.length > 0) {
    parts.push(`高频关注：${topics.join("、")}。`);
  }
  if (recentSignals.length > 0) {
    parts.push(`近期线索：${recentSignals.join("；")}。`);
  }
  return parts.join("");
}

async function runDailyReflection(params: {
  userKey: string;
  hours: number;
  minUserMessages: number;
}): Promise<{ ok: boolean; saved: boolean; reason?: string; userKey: string; summary?: string }> {
  const userKey = normalizeUserKey(params.userKey);
  const hours = clamp(Number(params.hours || 24), 1, 168);
  const minUserMessages = clamp(Number(params.minUserMessages || 5), 3, 60);
  if (!userKey || userKey === "session:unknown") {
    return { ok: false, saved: false, reason: "invalid_user_key", userKey };
  }

  const recent = await getRecentChats(userKey, MAX_CHATS_PER_USER);
  const cutoff = Date.now() - hours * 3600 * 1000;
  const userCount = recent.filter((x) => x.role === "user" && Number(x.ts || 0) >= cutoff).length;
  if (userCount < minUserMessages) {
    return { ok: true, saved: false, reason: "insufficient_messages", userKey };
  }

  const summary = summarizeForReflection(recent, hours);
  if (!summary) {
    return { ok: true, saved: false, reason: "no_signal", userKey };
  }
  await addMemoryNote(userKey, summary, "derived");
  return { ok: true, saved: true, userKey, summary };
}

const identityProbeSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    rawUserKey: { type: "string", description: "Raw user key to normalize/alias." },
    prompt: { type: "string", description: "Optional prompt text to infer qq identity." },
    sessionKey: { type: "string", description: "Optional OpenClaw session key." },
  },
};

const memorySearchSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    userKey: { type: "string", description: "Normalized user key, e.g. qqbot:123456789" },
    query: { type: "string", description: "Search query" },
    limit: { type: "integer", minimum: 1, maximum: 8 },
  },
  required: ["userKey", "query"],
};

const dailyReflectionSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    userKey: { type: "string", description: "Normalized user key, e.g. qqbot:123456789" },
    hours: { type: "integer", minimum: 1, maximum: 168, description: "Lookback hours, default 24" },
    minUserMessages: { type: "integer", minimum: 3, maximum: 60, description: "Minimum user messages to save" },
  },
  required: ["userKey"],
};

const xiaoCorePlugin = {
  id: "xiao-core",
  name: "Xiao Core",
  description: "Core migration helpers for OpenClaw QQ channel cutover",
  configSchema: emptyPluginConfigSchema(),
  register(api: OpenClawPluginApi) {
    api.on("before_agent_start", async (event, ctx) => {
      const now = Date.now();
      sweepSessionCache(now);
      sweepPendingUrlCache(now);

      const prompt = event.prompt || "";
      const rawUserKey = resolveUserKeyFromPrompt(prompt, ctx.sessionKey);
      const mapped = applyAlias(rawUserKey);
      const userInput = extractUserInput(prompt);

      if (ctx.sessionKey) {
        SESSION_USER_MAP.set(ctx.sessionKey, {
          resolvedUserKey: mapped.resolved,
          aliasFrom: mapped.aliasFrom,
          seenAt: now,
          promptPreview: shorten(prompt, 120),
          userInput,
          userInputRecorded: !!userInput,
        });
      }

      const recentNotes = await getRecentNotes(mapped.resolved, 5);
      const recentChats = await getRecentChats(mapped.resolved, 6);
      const ragHits = userInput ? await retrieveRagHits(mapped.resolved, userInput, 5) : [];
      const explicitMemo = extractExplicitMemory(userInput);
      const reminderIntent = parseReminderIntent(userInput);
      const weatherIntent = hasWeatherIntent(userInput);
      const stockIntent = hasStockIntent(userInput);
      const githubIntent = hasGithubTrendingIntent(userInput);
      const summaryIntent = hasUrlSummaryIntent(userInput);
      const sourceIntent = hasSourceFollowupIntent(userInput);
      const urlsInInput = extractUrls(userInput);
      const directUrl = urlsInInput[0] || "";
      if (urlsInInput.length > 0) {
        for (const url of urlsInInput) {
          await addLinkEvidence(mapped.resolved, "user", url, userInput);
        }
      }
      if (directUrl) {
        setPendingUrl(mapped.resolved, directUrl, userInput);
      }
      const pendingUrl = !directUrl && summaryIntent ? getPendingUrl(mapped.resolved) : null;
      const recentLinks = sourceIntent ? await getRecentLinks(mapped.resolved, 6) : [];
      const weatherCity = weatherIntent ? inferCityFromInput(userInput) : null;
      const stockSymbol = stockIntent ? inferStockSymbol(userInput) : null;

      const [prefetchedWeather, prefetchedStock] = await Promise.all([
        weatherCity ? fetchWeatherSummary(weatherCity) : Promise.resolve(null),
        stockSymbol ? fetchStockSummary(stockSymbol) : Promise.resolve(null),
      ]);

      // Some OpenAI-compatible invocations may bypass message_sending hooks.
      // Persist user-side memory here to avoid losing explicit memory updates.
      if (userInput) {
        await addChatEntry(mapped.resolved, "user", userInput);
      }
      if (explicitMemo) {
        await addMemoryNote(mapped.resolved, explicitMemo, "explicit");
      }

      const lines: string[] = [];
      lines.push("XIAO_CORE_CONTEXT");
      lines.push("runtime=openclaw_primary");
      lines.push(`user_key=${mapped.resolved}`);
      if (mapped.aliasFrom) {
        lines.push(`user_key_alias_from=${mapped.aliasFrom}`);
      }

      if (userInput) {
        lines.push(`user_input=${shorten(userInput, 240)}`);
      }

      if (recentNotes.length > 0) {
        lines.push("recent_notes=");
        for (const n of recentNotes) {
          lines.push(`- [${n.source}] ${shorten(n.text, 120)}`);
        }
      }

      if (ragHits.length > 0) {
        lines.push("rag_hits=");
        for (const h of ragHits) {
          lines.push(`- (${h.from},score=${h.score}) ${shorten(h.text, 140)}`);
        }
      }

      if (recentChats.length > 0) {
        lines.push("recent_chats=");
        for (const c of recentChats) {
          lines.push(`- ${c.role}: ${shorten(c.text, 120)}`);
        }
      }

      if (explicitMemo) {
        lines.push("用户明确要求你记住一条信息，先自然确认，再回答问题。无需暴露内部标签。");
      }

      if (prefetchedWeather) {
        lines.push(`prefetched_weather=${prefetchedWeather}`);
        lines.push("关于天气优先使用 prefetched_weather；若信息不足再调用 xiao_weather_openmeteo。");
      } else if (weatherIntent) {
        if (weatherCity) {
          lines.push(
            `已识别天气城市=${weatherCity}，请优先调用工具 xiao_weather_openmeteo 获取实时数据，不要凭空猜天气。`,
          );
        } else {
          lines.push("用户在问天气但未识别出城市，请先简短追问城市，或调用 xiao_weather_openmeteo 默认城市后说明条件。");
        }
      }

      if (prefetchedStock) {
        lines.push(`prefetched_stock=${prefetchedStock}`);
        lines.push("关于股票优先使用 prefetched_stock；若用户追问公告/深度分析再调用工具补充。");
      } else if (stockIntent) {
        if (stockSymbol) {
          lines.push(
            `已识别股票代码=${stockSymbol}，请优先调用 xiao_stock_quote 获取行情，不要编造代码或价格。`,
          );
        } else {
          lines.push("用户在问股票但未识别代码，请先追问 6 位代码。");
        }
      }

      if (githubIntent) {
        lines.push(
          "用户在问 GitHub 周榜/热榜。请优先调用 xiao_github_trending（since=weekly, limit=5），基于真实返回结果再总结，不要编造项目名或 star 数据。",
        );
      }

      if (directUrl) {
        if (summaryIntent) {
          lines.push(
            `用户发了链接且希望总结。请优先调用 xiao_url_digest，参数建议：url=${shorten(directUrl, 220)}；基于返回内容做2-5行口语化总结。`,
          );
        } else {
          lines.push(
            `用户发了链接（${shorten(directUrl, 120)}）。若对方未明确要求总结，请先简短确认“要不要我帮你总结这篇链接”。`,
          );
        }
      } else if (summaryIntent) {
        if (pendingUrl?.url) {
          lines.push(
            `用户在追问“总结”。请优先调用 xiao_url_digest，总结最近一条链接：url=${shorten(pendingUrl.url, 220)}。`,
          );
        } else {
          lines.push("用户想要链接总结，但未提供可用 URL。请先让用户发链接。");
        }
      }

      if (sourceIntent) {
        if (recentLinks.length > 0) {
          lines.push("recent_links=");
          const latestFirst = recentLinks.slice().sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0));
          for (const item of latestFirst) {
            const at = new Date(Number(item.ts || 0)).toISOString();
            const context = item.context ? ` | context=${shorten(item.context, 90)}` : "";
            lines.push(`- [${item.source}] ${shorten(item.url, 220)} | at=${at}${context}`);
          }
          lines.push("用户在追问来源/链接。请优先基于 recent_links 给出真实 URL 列表，不要编造新链接。");
        } else {
          lines.push("用户在追问来源/链接，但当前没有可引用记录。请说明暂无可用链接，并请用户补发原文链接。");
        }
      }

      if (reminderIntent) {
        const to = reminderTargetFromUserKey(mapped.resolved);
        if (to) {
          lines.push(
            `识别到提醒意图：minutes=${reminderIntent.minutes}, content=${shorten(reminderIntent.content, 120)}`,
          );
          lines.push(
            `请优先调用 xiao_schedule_reminder，参数建议：to=${to}, minutesFromNow=${reminderIntent.minutes}, message=${shorten(reminderIntent.content, 120)}。`,
          );
        } else {
          lines.push("识别到提醒意图，但未解析到 QQ 用户目标。请先确认提醒对象后再调用 xiao_schedule_reminder。");
        }
      }

      lines.push("人设：你是小a，20岁，亲密陪伴型聊天风格。语气自然、口语化、避免客服腔。");
      lines.push("回复规则：优先短句，1-4行，必要时再展开；事实类问题先给结论，再给依据。");
      lines.push("如果被问及部署方式，请说明：业务运行时是 OpenClaw QQ channel。compose/docker 仅可能用于某些环境的进程编排。\n");

      return { prependContext: lines.join("\n") };
    });

    api.on("message_sending", async (_event, ctx) => {
      const content = typeof _event.content === "string" ? _event.content : "";
      if (!content.trim()) {
        return;
      }

      const sessionKey = (ctx as { sessionKey?: string }).sessionKey || "";
      const snapshot = SESSION_USER_MAP.get(sessionKey);
      const fallbackKey = applyAlias(
        normalizeUserKey(`${ctx.channel || "unknown"}:${(_event.to || "unknown").trim() || "unknown"}`),
      ).resolved;
      const userKey = snapshot?.resolvedUserKey || fallbackKey;

      if (snapshot && snapshot.userInput && !snapshot.userInputRecorded) {
        await addChatEntry(userKey, "user", snapshot.userInput);
        const explicit = extractExplicitMemory(snapshot.userInput);
        if (explicit) {
          await addMemoryNote(userKey, explicit, "explicit");
        }
        snapshot.userInputRecorded = true;
        snapshot.seenAt = Date.now();
        SESSION_USER_MAP.set(sessionKey, snapshot);
      }

      const clean = cleanAssistantText(content);
      if (clean) {
        await addChatEntry(userKey, "assistant", clean);
      }
      const urlsInReply = extractUrls(content);
      if (urlsInReply.length > 0) {
        for (const url of urlsInReply) {
          await addLinkEvidence(userKey, "assistant", url, clean || content);
        }
      }
    });

    api.registerTool({
      name: "xiao_identity_probe",
      label: "Xiao Identity Probe",
      description: "Normalize and alias-resolve user identity for QQ channel migration.",
      parameters: identityProbeSchema,
      async execute(
        _toolCallId: string,
        params: { rawUserKey?: string; prompt?: string; sessionKey?: string },
      ) {
        const prompt = (params.prompt || "").trim();
        const sessionKey = (params.sessionKey || "").trim();
        const inferredFromPrompt = prompt ? inferRecipientId(prompt) : null;

        const candidate = (params.rawUserKey || "").trim() || (inferredFromPrompt ? `qqbot:${inferredFromPrompt}` : "");
        const normalized = normalizeUserKey(candidate || resolveUserKeyFromPrompt(prompt, sessionKey || undefined));
        const mapped = applyAlias(normalized);

        return jsonResult({
          ok: true,
          input: {
            rawUserKey: params.rawUserKey || "",
            prompt: shorten(prompt, 180),
            sessionKey,
          },
          inferred: {
            recipientIdFromPrompt: inferredFromPrompt,
          },
          normalized,
          resolved: mapped.resolved,
          aliasFrom: mapped.aliasFrom || null,
        });
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_memory_search",
      label: "Xiao Memory Search",
      description: "Search lightweight memory/RAG hits from xiao-core state.",
      parameters: memorySearchSchema,
      async execute(_toolCallId: string, params: { userKey?: string; query?: string; limit?: number }) {
        const userKey = normalizeUserKey((params.userKey || "").trim());
        const query = (params.query || "").trim();
        const limit = clamp(Number(params.limit || 5), 1, 8);

        if (!userKey || userKey === "session:unknown") {
          return jsonResult({ ok: false, error: "userKey is required" });
        }
        if (!query) {
          return jsonResult({ ok: false, error: "query is required" });
        }

        const hits = await retrieveRagHits(userKey, query, limit);
        return jsonResult({ ok: true, userKey, query, hits });
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_daily_reflection",
      label: "Xiao Daily Reflection",
      description: "Generate and save a lightweight daily reflection note from recent chats.",
      parameters: dailyReflectionSchema,
      async execute(
        _toolCallId: string,
        params: { userKey?: string; hours?: number; minUserMessages?: number },
      ) {
        const userKey = normalizeUserKey((params.userKey || "").trim());
        if (!userKey || userKey === "session:unknown") {
          return jsonResult({ ok: false, error: "userKey is required" });
        }
        const result = await runDailyReflection({
          userKey,
          hours: Number(params.hours || 24),
          minUserMessages: Number(params.minUserMessages || 5),
        });
        return jsonResult(result);
      },
    } as AnyAgentTool);

    api.registerCommand({
      name: "xiao-health",
      description: "Show OpenClaw QQ migration health snapshot.",
      acceptsArgs: false,
      handler: async (ctx) => {
        const lines: string[] = [];
        lines.push("xiao-core health");
        lines.push(`- now: ${new Date().toISOString()}`);
        lines.push(`- uptime_sec: ${formatUptimeSec()}`);
        lines.push(`- channel: ${ctx.channel}`);
        lines.push(`- conversation: ${(ctx.conversationId || "").trim() || "(none)"}`);
        lines.push(`- sender: ${(ctx.senderId || "").trim() || "(none)"}`);
        lines.push(`- session_cache_size: ${SESSION_USER_MAP.size}`);
        lines.push(`- state_file: ${resolveStateFilePath()}`);
        lines.push("");
        lines.push("env status:");
        lines.push(`- OPENCLAW_GATEWAY_TOKEN: ${envStatus("OPENCLAW_GATEWAY_TOKEN")}`);
        lines.push(`- XIAO_USER_ALIAS_MAP: ${envStatus("XIAO_USER_ALIAS_MAP")}`);
        lines.push(`- SILICONFLOW_API_KEY: ${envStatus("SILICONFLOW_API_KEY")}`);
        lines.push(`- DEEPSEEK_API_KEY: ${envStatus("DEEPSEEK_API_KEY")}`);
        lines.push(`- OPENAI_API_KEY: ${envStatus("OPENAI_API_KEY")}`);
        return { text: lines.join("\n") };
      },
    });

    api.registerCommand({
      name: "xiao-whoami",
      description: "Show raw identity and resolved user_key mapping.",
      acceptsArgs: false,
      handler: async (ctx) => {
        const actor =
          (ctx.from && String(ctx.from).trim()) ||
          (ctx.senderId && String(ctx.senderId).trim()) ||
          "unknown";
        const raw = `${ctx.channel}:${actor}`;
        const normalized = normalizeUserKey(raw);
        const mapped = applyAlias(normalized);

        const lines: string[] = [];
        lines.push("xiao-core whoami");
        lines.push(`- channel: ${ctx.channel}`);
        lines.push(`- from: ${(ctx.from && String(ctx.from).trim()) || "(none)"}`);
        lines.push(`- senderId: ${(ctx.senderId && String(ctx.senderId).trim()) || "(none)"}`);
        lines.push(`- conversationId: ${(ctx.conversationId || "").trim() || "(none)"}`);
        lines.push(`- raw_user_key: ${raw}`);
        lines.push(`- normalized_user_key: ${normalized}`);
        lines.push(`- resolved_user_key: ${mapped.resolved}`);
        if (mapped.aliasFrom) {
          lines.push(`- alias_from: ${mapped.aliasFrom}`);
        }
        return { text: lines.join("\n") };
      },
    });

    api.registerCommand({
      name: "xiao-echo",
      description: "Echo text with normalized identity (for QQ channel smoke test).",
      acceptsArgs: true,
      handler: async (ctx) => {
        const actor =
          (ctx.from && String(ctx.from).trim()) ||
          (ctx.senderId && String(ctx.senderId).trim()) ||
          "unknown";
        const raw = `${ctx.channel}:${actor}`;
        const normalized = normalizeUserKey(raw);
        const mapped = applyAlias(normalized);

        const rawArgs = (ctx.args || "").trim();
        const text = rawArgs || "(empty)";
        const safeText = shorten(text, 512);

        return {
          text: [
            `echo: ${safeText}`,
            `user_key: ${mapped.resolved}`,
            `channel: ${ctx.channel}`,
          ].join("\n"),
        };
      },
    });

    api.registerCommand({
      name: "xiao-memory",
      description: "Memory ops. Usage: /xiao-memory [list|add <text>|search <query>]",
      acceptsArgs: true,
      handler: async (ctx) => {
        const actor =
          (ctx.from && String(ctx.from).trim()) ||
          (ctx.senderId && String(ctx.senderId).trim()) ||
          "unknown";
        const raw = `${ctx.channel}:${actor}`;
        const userKey = applyAlias(normalizeUserKey(raw)).resolved;
        const args = (ctx.args || "").trim();

        if (!args || args === "list") {
          const notes = await getRecentNotes(userKey, 10);
          if (notes.length === 0) {
            return { text: "memory is empty" };
          }
          const lines = notes.map((n, i) => `${String(i + 1).padStart(2, "0")}. [${n.source}] ${n.text}`);
          return { text: lines.join("\n") };
        }

        if (args.startsWith("add ")) {
          const payload = args.slice(4).trim();
          if (!payload) {
            return { text: "usage: /xiao-memory add <text>" };
          }
          await addMemoryNote(userKey, payload, "explicit");
          return { text: "memory saved" };
        }

        if (args.startsWith("search ")) {
          const query = args.slice(7).trim();
          if (!query) {
            return { text: "usage: /xiao-memory search <query>" };
          }
          const hits = await retrieveRagHits(userKey, query, 6);
          if (hits.length === 0) {
            return { text: "no memory hit" };
          }
          const lines = hits.map((h, i) => `${i + 1}. (${h.from},score=${h.score}) ${shorten(h.text, 120)}`);
          return { text: lines.join("\n") };
        }

        return { text: "usage: /xiao-memory [list|add <text>|search <query>]" };
      },
    });

    api.registerCommand({
      name: "xiao-links",
      description: "Show recent link evidence. Usage: /xiao-links [limit]",
      acceptsArgs: true,
      handler: async (ctx) => {
        const actor =
          (ctx.from && String(ctx.from).trim()) ||
          (ctx.senderId && String(ctx.senderId).trim()) ||
          "unknown";
        const raw = `${ctx.channel}:${actor}`;
        const userKey = applyAlias(normalizeUserKey(raw)).resolved;
        const limitRaw = Number((ctx.args || "").trim() || 6);
        const limit = clamp(Number.isFinite(limitRaw) ? limitRaw : 6, 1, 12);
        const links = await getRecentLinks(userKey, limit);
        if (links.length === 0) {
          return { text: "no recent links" };
        }
        const latestFirst = links.slice().sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0));
        const lines: string[] = [];
        lines.push(`recent links (user=${userKey})`);
        for (let i = 0; i < latestFirst.length; i += 1) {
          const item = latestFirst[i];
          const at = new Date(Number(item.ts || 0)).toISOString();
          lines.push(`${i + 1}. [${item.source}] ${item.url}`);
          if (item.context) {
            lines.push(`   context: ${shorten(item.context, 120)}`);
          }
          lines.push(`   at: ${at}`);
        }
        return { text: lines.join("\n") };
      },
    });

    api.registerCommand({
      name: "xiao-reflect",
      description: "Generate derived reflection memory. Usage: /xiao-reflect [hours]",
      acceptsArgs: true,
      handler: async (ctx) => {
        const actor =
          (ctx.from && String(ctx.from).trim()) ||
          (ctx.senderId && String(ctx.senderId).trim()) ||
          "unknown";
        const raw = `${ctx.channel}:${actor}`;
        const userKey = applyAlias(normalizeUserKey(raw)).resolved;
        const hoursRaw = Number((ctx.args || "").trim() || 24);
        const hours = clamp(Number.isFinite(hoursRaw) ? hoursRaw : 24, 1, 168);
        const result = await runDailyReflection({
          userKey,
          hours,
          minUserMessages: 5,
        });
        if (!result.ok) {
          return { text: `reflection failed: ${result.reason || "unknown"}` };
        }
        if (!result.saved) {
          return { text: `reflection skipped: ${result.reason || "no_signal"}` };
        }
        return {
          text: [
            "reflection saved",
            `- user_key: ${result.userKey}`,
            `- hours: ${hours}`,
            `- summary: ${shorten(result.summary || "", 180)}`,
          ].join("\n"),
        };
      },
    });

    api.registerCommand({
      name: "xiao-remind",
      description: "Create one-shot reminder. Usage: /xiao-remind <minutes> <content>",
      acceptsArgs: true,
      handler: async (ctx) => {
        const parsed = parseReminderArgs((ctx.args || "").trim());
        if (!parsed) {
          return {
            text: "usage: /xiao-remind <minutes> <content>\nexample: /xiao-remind 30 记得喝水",
          };
        }

        const to = resolveQqTargetFromCtx({
          channel: ctx.channel,
          from: (ctx.from && String(ctx.from)) || "",
          senderId: (ctx.senderId && String(ctx.senderId)) || "",
          conversationId: ctx.conversationId || "",
        });
        if (!to) {
          return {
            text: "当前上下文不是 qqbot，无法自动识别提醒目标。请在 QQ 私聊使用此命令。",
          };
        }

        const name = `xiao-reminder-${Date.now()}-${Math.trunc(Math.random() * 1000)}`;
        const message = `你是小a。提醒内容：${parsed.content}`;
        const args = [
          "cron",
          "add",
          "--name",
          name,
          "--at",
          `${parsed.minutes}m`,
          "--message",
          message,
          "--announce",
          "--channel",
          "qqbot",
          "--to",
          to,
          "--session",
          "isolated",
          "--delete-after-run",
          "--json",
        ];

        try {
          const { stdout } = await execFileAsync("openclaw", args, {
            timeout: 25000,
            maxBuffer: 1024 * 1024,
          });
          const parsedOut = extractJsonPayload(String(stdout || ""));
          const out = parsedOut as Record<string, unknown>;
          const jobId = String(out.id || "").trim() || "(unknown)";
          return {
            text: [
              "提醒已创建",
              `- to: ${to}`,
              `- after: ${parsed.minutes}m`,
              `- content: ${parsed.content}`,
              `- job_id: ${jobId}`,
            ].join("\n"),
          };
        } catch (err) {
          const e = err as Error & { stderr?: string; stdout?: string };
          const msg =
            `${(e.stderr || "").trim()} ${(e.stdout || "").trim()}`.trim() ||
            (e.message || "failed to create reminder");
          return { text: `提醒创建失败：${shorten(msg, 280)}` };
        }
      },
    });
  },
};

export default xiaoCorePlugin;
