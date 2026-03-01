/**
 * xiao-core - 小a核心功能插件
 *
 * 描述
 *     提供小a的核心功能，包括：
 *     - 用户身份识别与别名映射
 *     - 轻量级记忆系统（RAG检索）
 *     - 链接追踪与来源管理
 *     - 定时提醒意图解析
 *     - 天气/股票/GitHub意图识别
 *     - 每日反思生成
 *
 * 工具函数
 *     xiao_identity_probe      - 用户身份标准化与别名解析
 *     xiao_memory_search       - 记忆搜索（RAG检索）
 *     xiao_daily_reflection    - 每日反思生成
 *
 * 命令
 *     /xiao-health              - 显示插件健康状态
 *     /xiao-whoami              - 显示当前用户身份信息
 *     /xiao-echo                - 回显测试
 *     /xiao-memory [操作]       - 记忆管理（list/add/search）
 *     /xiao-links [数量]        - 显示最近链接
 *     /xiao-reflect [小时]      - 生成反思记忆
 *
 * 钩子
 *     before_agent_start       - Agent启动前预处理
 *         - 解析用户身份
 *         - 提取用户输入
 *         - 检索相关记忆
 *         - 识别意图（天气、股票、提醒等）
 *         - 预获取数据（天气、股票）
 *         - 注入上下文
 *
 *     message_sending           - 消息发送后处理
 *         - 记录对话历史
 *         - 保存显式记忆
 *         - 追踪链接来源
 *
 * 环境变量
 *     XIAO_ENV_FILE             - 环境变量文件路径
 *     XIAO_CORE_STATE_FILE    - 状态文件路径
 *     XIAO_USER_ALIAS_MAP     - 用户别名映射
 *     XIAO_EMOTION_ALIAS_MAP  - 情绪模块别名映射
 *
 *     Token 优化配置（降低 API 消耗）
 *     XIAO_MAX_NOTES           - 最近笔记数量（默认：3）
 *     XIAO_MAX_CHATS           - 最近对话数量（默认：4）
 *     XIAO_MAX_RAG_HITS       - RAG检索结果数量（默认：3）
 *     XIAO_ENABLE_PREFETCH    - 是否预获取天气/股票（默认：true，设为false可节省token）
 *
 * 状态存储
 *     ~/.openclaw/xiao-core/state.json
 *     {
 *       "notes": { "user_key": [...] },   // 记忆笔记
 *       "chats": { "user_key": [...] },  // 对话历史
 *       "links": { "user_key": [...] }   // 链接记录
 *     }
 *
 * 限制
 *     - 每用户最大笔记数：200
 *     - 每用户最大对话数：240
 *     - 每用户最大链接数：80
 *     - 单条笔记最大长度：300字符
 *     - 单条对话最大长度：400字符
 *
 * 示例
 *     # 记忆添加
 *     /xiao-memory add 小a喜欢草莓味的波子汽水
 *     // 输出：memory saved
 *
 *     # 记忆搜索
 *     /xiao-memory search 饮料
 *     // 输出：1. (note,score=3) 小a最喜欢的饮料是草莓味的波子汽水。
 *
 *     # 链接查询
 *     /xiao-links 3
 *     // 输出：recent links (user=qqbot:xxx)
 *     //      1. [user] https://github.com/...
 *
 *     # 反思生成
 *     /xiao-reflect 24
 *     // 输出：reflection saved
 */

import { execFile } from "node:child_process";
import { readFileSync, statSync, existsSync, promises as fs } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import type { AnyAgentTool, OpenClawPluginApi } from "openclaw/plugin-sdk";
import { emptyPluginConfigSchema } from "openclaw/plugin-sdk";
import { registerXiaoGithubWeeklyCommand } from "./features/github-weekly-command.js";
import { registerXiaoGithubCommand } from "./features/github-command.js";
import { registerXiaoDiagnosticsCommands } from "./features/diagnostics-commands.js";
import { registerXiaoLinksCommand } from "./features/links-command.js";
import { registerXiaoMemoryCommand } from "./features/memory-command.js";
import { registerXiaoMemoCommand } from "./features/memo-command.js";
import { registerXiaoReflectCommand } from "./features/reflect-command.js";
import { registerXiaoRemindCommand } from "./features/remind-command.js";
import { registerXiaoSourceCommand } from "./features/source-command.js";
import { registerXiaoStockCommand } from "./features/stock-command.js";
import { registerXiaoTimeCommand } from "./features/time-command.js";
import { registerXiaoUrlBasicCommand } from "./features/url-basic-command.js";
import { registerXiaoWeatherCommand } from "./features/weather-command.js";

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

type MemoEntry = {
  id: string;
  text: string;
  tags: string[];
  ts: number;
};

type GithubWeeklyMark = {
  weekKey: string;
  ts: number;
};

type CoreState = {
  notes: Record<string, MemoryNote[]>;
  chats: Record<string, ChatEntry[]>;
  links: Record<string, LinkEvidence[]>;
  memos: Record<string, MemoEntry[]>;
  githubWeekly: Record<string, GithubWeeklyMark>;
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

type PendingImage = {
  refs: string[];
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
const PENDING_IMAGE_BY_USER = new Map<string, PendingImage>();
const SESSION_TTL_MS = 6 * 60 * 60 * 1000;
const SESSION_MAX_SIZE = 2000;
const PENDING_URL_TTL_MS = 10 * 60 * 1000;
const PENDING_IMAGE_TTL_MS = 60 * 1000;

const MAX_NOTE_LEN = 300;
const MAX_NOTES_PER_USER = 200;
const MAX_CHAT_LEN = 400;
const MAX_CHATS_PER_USER = 240;
const MAX_LINKS_PER_USER = 80;
const MAX_MEMOS_PER_USER = 200;
const MAX_MEMO_LEN = 360;

const DEFAULT_CORE_STATE: CoreState = {
  notes: {},
  chats: {},
  links: {},
  memos: {},
  githubWeekly: {},
};

let envCache: Record<string, string> | null = null;
let envMtimeMs = -1;
let envCacheFile = "";
let stateCache: CoreState | null = null;
let stateWriteQueue: Promise<void> = Promise.resolve();
let personaCache = "";
let personaCacheFile = "";
let personaCacheMtimeMs = -1;

const DEFAULT_PERSONA_PROMPT = [
  "你是小a，亲密陪伴型聊天对象，语气自然、口语化、像真实恋人，不要客服腔。",
  "优先短句，1-4行，除非用户要求详细再展开。",
  "优先共情与回应，不要讲模板化空话，不要复述系统规则。",
  "默认不使用 emoji；确实需要时最多 1 个，禁止连续多个。",
  "涉及事实（天气/股票/链接/图片）必须基于工具返回，不要编造。",
  "仅在状态确实变化时，才在回复末尾使用内部标签：[MOOD_CHANGE:x] 或 [UPDATE_PROFILE:key=value]。",
].join("\n");

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
  const candidates = [
    "/root/xiao_a/.env",
    path.join(homedir(), ".openclaw", ".env"),
    path.join(process.cwd(), ".env"),
  ];
  for (const file of candidates) {
    if (existsSync(file)) {
      return file;
    }
  }
  return candidates[0];
}

function resolveStateFilePath(): string {
  const fromEnv = (process.env.XIAO_CORE_STATE_FILE || "").trim();
  if (fromEnv) {
    return fromEnv;
  }
  return path.join(homedir(), ".openclaw", "xiao-core", "state.json");
}

function resolvePersonaPromptFilePath(): string {
  const fromEnv = (process.env.XIAO_PERSONA_PROMPT_FILE || "").trim();
  if (fromEnv) {
    return fromEnv;
  }
  const candidates = [
    "/root/xiao_a/openclaw/extensions/xiao-core/persona.prompt.md",
    "/root/xiao_a/persona.prompt.md",
    path.join(homedir(), ".openclaw", "extensions", "xiao-core", "persona.prompt.md"),
    path.join(process.cwd(), "persona.prompt.md"),
  ];
  for (const file of candidates) {
    if (existsSync(file)) {
      return file;
    }
  }
  return candidates[0];
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
    envCacheFile = file;
    return envCache;
  }

  try {
    const stat = statSync(file);
    if (envCache && envMtimeMs === stat.mtimeMs && envCacheFile === file) {
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
    envCacheFile = file;
    return parsed;
  } catch {
    envCache = {};
    envMtimeMs = -1;
    envCacheFile = file;
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
  cleaned = cleaned.replace(/<qqvoice>[\s\S]*?<\/qqvoice>/gi, "");
  cleaned = cleaned.replace(/<qqimg>[\s\S]*?<\/qqimg>/gi, "");
  cleaned = cleaned.replace(/<img\b[^>]*>/gi, "");
  cleaned = cleaned.replace(/!\[[^\]]*]\((?:file|https?):\/\/[^)]+\)/gi, "");
  cleaned = cleaned.replace(/\[\[\s*audio_as_voice\s*]\]/gi, "");
  cleaned = cleaned.replace(/\s+$/g, "");
  return cleaned.trim();
}

function sanitizeAssistantOutbound(text: string): { text: string; voicePath?: string } {
  const raw = (text || "").trim();
  const voiceMatch = raw.match(/<qqvoice>\s*([^<>\n]+?)\s*<\/qqvoice>/i);
  const voicePath = (voiceMatch?.[1] || "").trim();

  let cleaned = raw.replace(/<qqvoice>[\s\S]*?<\/qqvoice>/gi, "");
  cleaned = cleaned.replace(/<qqimg>[\s\S]*?<\/qqimg>/gi, "");
  cleaned = cleaned.replace(/<img\b[^>]*>/gi, "");
  cleaned = cleaned.replace(/!\[[^\]]*]\((?:file|https?):\/\/[^)]+\)/gi, "");
  cleaned = cleaned.replace(/\[\[\s*audio_as_voice\s*]\]/gi, "");
  cleaned = cleaned.replace(/\[MOOD_CHANGE[:：]\s*-?\d+\s*\]/gi, "");
  cleaned = cleaned.replace(/\[UPDATE_PROFILE[:：]\s*[^\]]+\]/gi, "");
  cleaned = cleaned
    .split("\n")
    .map((line) => line.replace(/\s+$/g, ""))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  if (voicePath) {
    return { text: cleaned, voicePath };
  }
  return { text: cleaned };
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

async function loadPersonaPrompt(): Promise<string> {
  const file = resolvePersonaPromptFilePath();
  if (!existsSync(file)) {
    personaCache = DEFAULT_PERSONA_PROMPT;
    personaCacheFile = file;
    personaCacheMtimeMs = -1;
    return personaCache;
  }
  try {
    const stat = statSync(file);
    if (personaCache && personaCacheFile === file && personaCacheMtimeMs === stat.mtimeMs) {
      return personaCache;
    }
    const raw = await fs.readFile(file, "utf8");
    const cleaned = raw
      .replace(/\r\n/g, "\n")
      .split("\n")
      .map((line) => line.trimEnd())
      .join("\n")
      .trim();
    personaCache = cleaned || DEFAULT_PERSONA_PROMPT;
    personaCacheFile = file;
    personaCacheMtimeMs = stat.mtimeMs;
    return personaCache;
  } catch {
    personaCache = DEFAULT_PERSONA_PROMPT;
    personaCacheFile = file;
    personaCacheMtimeMs = -1;
    return personaCache;
  }
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

function normalizeImageRef(raw: string): string {
  let ref = (raw || "").trim();
  if (!ref) {
    return "";
  }

  ref = ref.replace(/^[<\s]+|[>\s]+$/g, "");
  ref = ref.replace(/[，。；;,]+$/g, "");

  const fileMatch = ref.match(/^file:\/\/\/?(.*)$/i);
  if (fileMatch?.[1]) {
    const body = fileMatch[1].trim();
    if (/^[A-Za-z]:[\\/]/.test(body)) {
      ref = body;
    } else {
      ref = `/${body.replace(/^\/+/, "")}`;
    }
  }

  return ref;
}

function extractImageRefs(input: string): string[] {
  const text = (input || "").trim();
  if (!text) return [];

  const refs: string[] = [];
  const seen = new Set<string>();
  const patterns = [
    /(?:^|\n)\s*-\s*图片地址\s*[：:]\s*([^\n\r]+)/gim,
    /(?:^|\n)\s*(?:MediaPath|MediaUrl)\s*[：:]\s*([^\n\r]+)/gim,
    /<qqimg>\s*([^<>\n]+?)\s*<\/(?:qqimg|img)>/gim,
    /<img\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gim,
  ];

  for (const pattern of patterns) {
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(text)) !== null) {
      const ref = normalizeImageRef(match[1] || "");
      if (!ref || seen.has(ref)) {
        continue;
      }
      seen.add(ref);
      refs.push(ref);
      if (refs.length >= 6) {
        return refs;
      }
    }
  }

  return refs;
}

function normalizeAudioRef(raw: string): string {
  let ref = (raw || "").trim();
  if (!ref) {
    return "";
  }
  ref = ref.replace(/^[<\s]+|[>\s]+$/g, "");
  ref = ref.replace(/[，。；;,]+$/g, "");
  const fileMatch = ref.match(/^file:\/\/\/?(.*)$/i);
  if (fileMatch?.[1]) {
    const body = fileMatch[1].trim();
    if (/^[A-Za-z]:[\\/]/.test(body)) {
      ref = body;
    } else {
      ref = `/${body.replace(/^\/+/, "")}`;
    }
  }
  return ref;
}

function extractAudioRefs(input: string): string[] {
  const text = (input || "").trim();
  if (!text) return [];

  const refs: string[] = [];
  const seen = new Set<string>();
  const patterns = [
    /(?:^|\n)\s*-\s*语音文件\s*[：:]\s*([^\n\r]+)/gim,
    /(?:^|\n)\s*(?:AudioPath|audioPath)\s*[：:]\s*([^\n\r]+)/gim,
  ];

  for (const pattern of patterns) {
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(text)) !== null) {
      const ref = normalizeAudioRef(match[1] || "");
      if (!ref || seen.has(ref)) {
        continue;
      }
      seen.add(ref);
      refs.push(ref);
      if (refs.length >= 4) {
        return refs;
      }
    }
  }
  return refs;
}

function isLikelyAttachmentOnlyInput(input: string): boolean {
  const t = (input || "").trim();
  if (!t) return true;
  if (t.length <= 8 && /(图片|语音|附件)/.test(t)) {
    return true;
  }
  const markers = [
    "用户发送了一张图片",
    "用户发送了一条语音消息",
    "请不要凭空描述图片内容",
    "回答前必须先调用",
    "图片地址",
    "语音文件",
    "发送时间",
  ];
  return markers.some((m) => t.includes(m));
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

function sweepPendingImageCache(now: number): void {
  for (const [k, v] of PENDING_IMAGE_BY_USER.entries()) {
    if (now - v.seenAt > PENDING_IMAGE_TTL_MS) {
      PENDING_IMAGE_BY_USER.delete(k);
    }
  }
}

function setPendingImage(userKey: string, refs: string[], sourceInput: string): void {
  const key = normalizeUserKey(userKey);
  if (!key) return;
  const normalized = refs
    .map((r) => normalizeImageRef(r))
    .filter(Boolean)
    .slice(0, 6);
  if (normalized.length === 0) {
    return;
  }
  PENDING_IMAGE_BY_USER.set(key, {
    refs: normalized,
    seenAt: Date.now(),
    sourceInput: shorten(sourceInput || "", 240),
  });
}

function getPendingImage(userKey: string): PendingImage | null {
  const key = normalizeUserKey(userKey);
  const p = PENDING_IMAGE_BY_USER.get(key);
  if (!p) return null;
  if (Date.now() - p.seenAt > PENDING_IMAGE_TTL_MS) {
    PENDING_IMAGE_BY_USER.delete(key);
    return null;
  }
  return p;
}

function clearPendingImage(userKey: string): void {
  const key = normalizeUserKey(userKey);
  if (!key) return;
  PENDING_IMAGE_BY_USER.delete(key);
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

function decodeHtmlEntities(text: string): string {
  if (!text) return "";
  return text
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, "\"")
    .replace(/&#39;/gi, "'");
}

function stripHtmlToText(html: string): string {
  if (!html) return "";
  const noScript = html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ");
  const plain = noScript.replace(/<[^>]+>/g, " ");
  return decodeHtmlEntities(plain).replace(/\s+/g, " ").trim();
}

async function fetchTextWithTimeout(
  url: string,
  timeoutMs: number,
  headers?: Record<string, string>,
): Promise<string> {
  const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs), headers });
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${shorten(t, 160)}`);
  }
  return await res.text();
}

function resolveDashscopeApiKey(): string {
  const direct = env("DASHSCOPE_API_KEY");
  if (direct) return direct;
  const alt = env("QWEN_API_KEY");
  if (alt) return alt;
  return "";
}

async function transcribeAudioPathForContext(audioPath: string): Promise<string | null> {
  const apiKey = resolveDashscopeApiKey();
  if (!apiKey) return null;
  const model = env("DASHSCOPE_ASR_MODEL") || "qwen3-asr-flash";
  const baseUrl = (env("DASHSCOPE_BASE_URL") || "https://dashscope.aliyuncs.com/compatible-mode/v1").replace(/\/$/, "");
  const absolutePath = path.resolve(audioPath);
  if (!existsSync(absolutePath)) {
    return null;
  }

  try {
    const bytes = await fs.readFile(absolutePath);
    if (!bytes || bytes.byteLength === 0) {
      return null;
    }
    const ext = path.extname(absolutePath).toLowerCase();
    const mimeType = ext === ".wav"
      ? "audio/wav"
      : ext === ".mp3"
      ? "audio/mpeg"
      : ext === ".ogg"
      ? "audio/ogg"
      : ext === ".m4a"
      ? "audio/mp4"
      : "application/octet-stream";
    const form = new FormData();
    form.set("model", model);
    form.set("file", new Blob([bytes], { type: mimeType }), path.basename(absolutePath) || "audio.wav");
    const res = await fetch(`${baseUrl}/audio/transcriptions`, {
      method: "POST",
      signal: AbortSignal.timeout(25000),
      headers: {
        Authorization: `Bearer ${apiKey}`,
      },
      body: form,
    });
    if (!res.ok) {
      return null;
    }
    const payload = (await res.json()) as Record<string, unknown>;
    const transcript =
      (typeof payload.text === "string" && payload.text.trim()) ||
      (typeof payload.transcript === "string" && payload.transcript.trim()) ||
      "";
    return transcript ? shorten(transcript, 500) : null;
  } catch {
    return null;
  }
}

function normalizeHttpUrl(input: string): string | null {
  const raw = (input || "").trim();
  if (!raw) return null;
  try {
    const u = new URL(raw);
    if (!/^https?:$/i.test(u.protocol)) return null;
    return u.toString();
  } catch {
    return null;
  }
}

function extractTitleFromHtml(html: string): string {
  const m = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  return shorten(stripHtmlToText(m?.[1] || ""), 180);
}

function extractDescriptionFromHtml(html: string): string {
  const patterns = [
    /<meta[^>]+name=["']description["'][^>]*content=["']([^"']+)["'][^>]*>/i,
    /<meta[^>]+content=["']([^"']+)["'][^>]*name=["']description["'][^>]*>/i,
    /<meta[^>]+property=["']og:description["'][^>]*content=["']([^"']+)["'][^>]*>/i,
    /<meta[^>]+content=["']([^"']+)["'][^>]*property=["']og:description["'][^>]*>/i,
  ];
  for (const p of patterns) {
    const m = html.match(p);
    if (m?.[1]) {
      const v = shorten(stripHtmlToText(m[1]), 220);
      if (v) return v;
    }
  }
  return "";
}

type UrlBasicDigest = {
  url: string;
  domain: string;
  title: string;
  description: string;
  preview: string;
};

async function fetchUrlBasicDigest(url: string): Promise<UrlBasicDigest | null> {
  const normalized = normalizeHttpUrl(url);
  if (!normalized) return null;
  try {
    const html = await fetchTextWithTimeout(normalized, 12000, {
      "User-Agent":
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      Accept: "text/html,application/xhtml+xml",
    });
    const title = extractTitleFromHtml(html);
    const description = extractDescriptionFromHtml(html);
    const bodyText = stripHtmlToText(html);
    const preview = shorten(bodyText, 260);
    const domain = (() => {
      try {
        return new URL(normalized).hostname;
      } catch {
        return "";
      }
    })();
    if (!title && !description && !preview) {
      return null;
    }
    return {
      url: normalized,
      domain,
      title,
      description,
      preview,
    };
  } catch {
    return null;
  }
}

type GithubTrendingLiteItem = {
  repo: string;
  description: string;
  language: string;
  stars: string;
};

type GithubRepoMeta = {
  description: string;
  topics: string[];
  language: string;
};

async function fetchGithubTrendingLite(params: {
  since: "daily" | "weekly" | "monthly";
  limit: number;
  language?: string;
}): Promise<GithubTrendingLiteItem[]> {
  const since = params.since;
  const limit = clamp(Number(params.limit || 5), 1, 10);
  const language = (params.language || "").trim();
  const base = language
    ? `https://github.com/trending/${encodeURIComponent(language)}`
    : "https://github.com/trending";
  const url = new URL(base);
  url.searchParams.set("since", since);

  const html = await fetchTextWithTimeout(url.toString(), 15000, {
    "User-Agent":
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    Accept: "text/html,application/xhtml+xml",
  });

  const blocks = html.match(/<article[\s\S]*?<\/article>/gi) || [];
  const out: GithubTrendingLiteItem[] = [];
  for (const block of blocks) {
    if (out.length >= limit) break;
    const repoMatch =
      block.match(/<h2[^>]*>[\s\S]*?href=["']\/([^"']+\/[^"']+)["']/i) ||
      block.match(/href=["']\/([^"']+\/[^"']+)["']/i);
    const repo = (repoMatch?.[1] || "").replace(/\s+/g, "");
    if (!repo || repo.includes("/sponsors/")) continue;

    const descMatch = block.match(/<p[^>]*>([\s\S]*?)<\/p>/i);
    const description = shorten(stripHtmlToText(descMatch?.[1] || ""), 120);

    const langMatch = block.match(/itemprop=["']programmingLanguage["'][^>]*>\s*([^<]+)\s*</i);
    const languageText = shorten(stripHtmlToText(langMatch?.[1] || ""), 30);

    const starMatch = block.match(/href=["']\/[^"']+\/stargazers["'][^>]*>\s*([^<]+)\s*</i);
    const stars = shorten(stripHtmlToText(starMatch?.[1] || ""), 30);

    out.push({
      repo,
      description,
      language: languageText,
      stars,
    });
  }
  return out;
}

async function fetchGithubRepoMeta(repo: string): Promise<GithubRepoMeta> {
  const cleanRepo = (repo || "").trim().replace(/^\/+|\/+$/g, "");
  if (!cleanRepo || !cleanRepo.includes("/")) {
    return { description: "", topics: [], language: "" };
  }
  try {
    const html = await fetchTextWithTimeout(`https://github.com/${cleanRepo}`, 12000, {
      "User-Agent":
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      Accept: "text/html,application/xhtml+xml",
    });
    const desc =
      shorten(
        stripHtmlToText(
          html.match(/<meta\s+name=["']description["']\s+content=["']([^"']*)["']/i)?.[1] ||
            html.match(/<meta\s+content=["']([^"']*)["']\s+name=["']description["']/i)?.[1] ||
            "",
        ),
        220,
      ) || "";
    const language =
      shorten(stripHtmlToText(html.match(/itemprop=["']programmingLanguage["'][^>]*>\s*([^<]+)\s*</i)?.[1] || ""), 32) ||
      "";
    const topics: string[] = [];
    const seen = new Set<string>();
    const topicRegex = /topic-tag[^>]*>\s*([^<]+)\s*</gi;
    let match: RegExpExecArray | null;
    while ((match = topicRegex.exec(html)) !== null) {
      const topic = shorten(stripHtmlToText(match[1] || ""), 40);
      if (!topic || seen.has(topic)) {
        continue;
      }
      seen.add(topic);
      topics.push(topic);
      if (topics.length >= 8) {
        break;
      }
    }
    return { description: desc, topics, language };
  } catch {
    return { description: "", topics: [], language: "" };
  }
}

function currentIsoWeekKey(now: Date = new Date()): string {
  const d = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

function inferGithubHotReason(item: GithubTrendingLiteItem, meta: GithubRepoMeta): string {
  const topics = (meta.topics || []).slice(0, 3).join(" / ");
  if (topics) {
    return `这周热度可能来自「${topics}」方向刚好在风口上。`;
  }
  if (item.stars) {
    return `榜单里星标增长明显（${item.stars}），说明最近关注度很集中。`;
  }
  return "它的问题定义很直接，大家一看就知道能拿来做什么，所以更容易扩散。";
}

function inferGithubUseHint(item: GithubTrendingLiteItem, meta: GithubRepoMeta): string {
  const lang = meta.language || item.language;
  if (lang) {
    return `如果你想快速试手感，可以先按 ${lang} 环境跑一个最小 demo。`;
  }
  if ((meta.topics || []).length > 0) {
    return `适合先从 README 和示例项目下手，先跑通再按自己的场景改。`;
  }
  return "适合先看它的 README 和示例，再决定是不是要接到你自己的项目里。";
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
      memos: parsed.memos && typeof parsed.memos === "object" ? parsed.memos : {},
      githubWeekly: parsed.githubWeekly && typeof parsed.githubWeekly === "object" ? parsed.githubWeekly : {},
    };
    return stateCache;
  } catch {
    stateCache = {
      notes: {},
      chats: {},
      links: {},
      memos: {},
      githubWeekly: {},
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

function makeMemoId(): string {
  return `${Date.now().toString(36)}${Math.trunc(Math.random() * 1e6).toString(36)}`;
}

function parseMemoTags(text: string): string[] {
  const tags = (text.match(/#([^\s#]{1,32})/g) || [])
    .map((x) => x.replace(/^#/, "").trim().toLowerCase())
    .filter(Boolean);
  return [...new Set(tags)].slice(0, 8);
}

async function addMemoEntry(userKey: string, text: string): Promise<MemoEntry | null> {
  const normalized = normalizeUserKey(userKey);
  const clean = shorten((text || "").trim(), MAX_MEMO_LEN);
  if (!normalized || !clean) return null;
  const store = await ensureStateLoaded();
  const arr = store.memos[normalized] || [];
  const entry: MemoEntry = {
    id: makeMemoId(),
    text: clean,
    tags: parseMemoTags(clean),
    ts: Date.now(),
  };
  arr.push(entry);
  if (arr.length > MAX_MEMOS_PER_USER) {
    arr.splice(0, arr.length - MAX_MEMOS_PER_USER);
  }
  store.memos[normalized] = arr;
  await persistState();
  return entry;
}

async function getRecentMemos(userKey: string, limit: number): Promise<MemoEntry[]> {
  const store = await ensureStateLoaded();
  const arr = (store.memos[normalizeUserKey(userKey)] || []).slice();
  arr.sort((a, b) => Number(a.ts || 0) - Number(b.ts || 0));
  return arr.slice(Math.max(0, arr.length - clamp(limit, 1, 30)));
}

async function searchMemos(userKey: string, query: string, limit: number): Promise<MemoEntry[]> {
  const normalized = normalizeUserKey(userKey);
  const q = shorten((query || "").trim(), 160);
  if (!normalized || !q) return [];
  const store = await ensureStateLoaded();
  const rows = (store.memos[normalized] || []).slice();
  const qTokens = tokenize(q);
  const scored = rows
    .map((x) => ({
      item: x,
      score: overlapScore(qTokens, tokenize(x.text)) + overlapScore(qTokens, x.tags),
    }))
    .filter((x) => x.score > 0)
    .sort((a, b) => (b.score !== a.score ? b.score - a.score : b.item.ts - a.item.ts))
    .slice(0, clamp(limit, 1, 20))
    .map((x) => x.item);
  return scored;
}

async function deleteMemoEntry(userKey: string, selector: string): Promise<{ ok: boolean; removed?: MemoEntry }> {
  const normalized = normalizeUserKey(userKey);
  const sel = (selector || "").trim();
  if (!normalized || !sel) return { ok: false };
  const store = await ensureStateLoaded();
  const arr = (store.memos[normalized] || []).slice();
  if (arr.length === 0) return { ok: false };

  let idx = -1;
  if (/^\d+$/.test(sel)) {
    const n = Number(sel);
    if (n >= 1 && n <= arr.length) {
      idx = arr.length - n;
    }
  }
  if (idx < 0) {
    idx = arr.findIndex((x) => x.id === sel);
  }
  if (idx < 0 || idx >= arr.length) {
    return { ok: false };
  }
  const [removed] = arr.splice(idx, 1);
  store.memos[normalized] = arr;
  await persistState();
  return { ok: true, removed };
}

async function hasGithubWeeklyPushed(userKey: string, weekKey: string): Promise<boolean> {
  const normalized = normalizeUserKey(userKey);
  if (!normalized || !weekKey) return false;
  const store = await ensureStateLoaded();
  return (store.githubWeekly[normalized]?.weekKey || "") === weekKey;
}

async function markGithubWeeklyPushed(userKey: string, weekKey: string): Promise<void> {
  const normalized = normalizeUserKey(userKey);
  if (!normalized || !weekKey) return;
  const store = await ensureStateLoaded();
  store.githubWeekly[normalized] = {
    weekKey,
    ts: Date.now(),
  };
  await persistState();
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
      sweepPendingImageCache(now);
      const personaPrompt = await loadPersonaPrompt();

      const prompt = event.prompt || "";
      const rawUserKey = resolveUserKeyFromPrompt(prompt, ctx.sessionKey);
      const mapped = applyAlias(rawUserKey);
      const userInput = extractUserInput(prompt);
      const audioRefs = extractAudioRefs(prompt);
      const voiceTranscript =
        audioRefs.length > 0 ? await transcribeAudioPathForContext(audioRefs[0] || "") : null;
      const effectiveUserInput =
        voiceTranscript && (isLikelyAttachmentOnlyInput(userInput) || userInput.length < 8)
          ? voiceTranscript
          : userInput;

      if (ctx.sessionKey) {
        SESSION_USER_MAP.set(ctx.sessionKey, {
          resolvedUserKey: mapped.resolved,
          aliasFrom: mapped.aliasFrom,
          seenAt: now,
          promptPreview: shorten(prompt, 120),
          userInput: effectiveUserInput,
          userInputRecorded: !!effectiveUserInput,
        });
      }

      // Token 优化配置
      const maxNotes = parseInt(env("XIAO_MAX_NOTES") || "3", 10);
      const maxChats = parseInt(env("XIAO_MAX_CHATS") || "4", 10);
      const maxRagHits = parseInt(env("XIAO_MAX_RAG_HITS") || "3", 10);
      const enablePrefetch = env("XIAO_ENABLE_PREFETCH") !== "false";

      const recentNotes = await getRecentNotes(mapped.resolved, maxNotes);
      const recentChats = await getRecentChats(mapped.resolved, maxChats);
      const ragHits = effectiveUserInput ? await retrieveRagHits(mapped.resolved, effectiveUserInput, maxRagHits) : [];
      const explicitMemo = extractExplicitMemory(effectiveUserInput);
      const reminderIntent = parseReminderIntent(effectiveUserInput);
      const weatherIntent = hasWeatherIntent(effectiveUserInput);
      const stockIntent = hasStockIntent(effectiveUserInput);
      const githubIntent = hasGithubTrendingIntent(effectiveUserInput);
      const summaryIntent = hasUrlSummaryIntent(effectiveUserInput);
      const sourceIntent = hasSourceFollowupIntent(effectiveUserInput);
      const urlsInInput = extractUrls(effectiveUserInput);
      const directImageRefs = extractImageRefs(`${prompt}\n${effectiveUserInput}`);
      const pendingImage = directImageRefs.length === 0 ? getPendingImage(mapped.resolved) : null;
      const imageRefs = directImageRefs.length > 0 ? directImageRefs : pendingImage?.refs || [];
      const directUrl = urlsInInput[0] || "";
      if (urlsInInput.length > 0) {
        for (const url of urlsInInput) {
          await addLinkEvidence(mapped.resolved, "user", url, effectiveUserInput);
        }
      }
      if (directUrl) {
        setPendingUrl(mapped.resolved, directUrl, effectiveUserInput);
      }
      const pendingUrl = !directUrl && summaryIntent ? getPendingUrl(mapped.resolved) : null;
      const recentLinks = sourceIntent ? await getRecentLinks(mapped.resolved, 6) : [];
      const weatherCity = weatherIntent ? inferCityFromInput(effectiveUserInput) : null;
      const stockSymbol = stockIntent ? inferStockSymbol(effectiveUserInput) : null;

      let prefetchedWeather: string | null = null;
      let prefetchedStock: string | null = null;
      if (enablePrefetch) {
        [prefetchedWeather, prefetchedStock] = await Promise.all([
          weatherCity ? fetchWeatherSummary(weatherCity) : Promise.resolve(null),
          stockSymbol ? fetchStockSummary(stockSymbol) : Promise.resolve(null),
        ]);
      }

      // Some OpenAI-compatible invocations may bypass message_sending hooks.
      // Persist user-side memory here to avoid losing explicit memory updates.
      if (effectiveUserInput) {
        await addChatEntry(mapped.resolved, "user", effectiveUserInput);
      }
      if (explicitMemo) {
        await addMemoryNote(mapped.resolved, explicitMemo, "explicit");
      }
      if (directImageRefs.length > 0) {
        setPendingImage(mapped.resolved, directImageRefs, effectiveUserInput);
      } else if (pendingImage && effectiveUserInput) {
        clearPendingImage(mapped.resolved);
      }

      const lines: string[] = [];
      lines.push("XIAO_CORE_CONTEXT");
      lines.push("runtime=openclaw_primary");
      lines.push(`user_key=${mapped.resolved}`);
      if (mapped.aliasFrom) {
        lines.push(`user_key_alias_from=${mapped.aliasFrom}`);
      }

      if (effectiveUserInput) {
        lines.push(`user_input=${shorten(effectiveUserInput, 240)}`);
      }
      if (voiceTranscript) {
        lines.push(`voice_transcript=${shorten(voiceTranscript, 240)}`);
        lines.push("检测到语音消息且已完成 ASR 转写。请优先基于 voice_transcript 回答，不要忽略语音内容。");
      } else if (audioRefs.length > 0) {
        lines.push("检测到语音消息，但本次 ASR 转写失败。请先告知用户“语音暂时没听清”，并请他重发或改文字。");
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

      if (imageRefs.length > 0) {
        if (pendingImage && directImageRefs.length === 0) {
          lines.push("检测到用户在追问上一条图片，以下 image_refs 为上一条缓存图片。");
        } else {
          lines.push("检测到用户发送了图片。不要凭空描述图片内容。");
        }
        lines.push("image_refs=");
        for (const ref of imageRefs) {
          lines.push(`- ${shorten(ref, 220)}`);
        }
        lines.push(
          `回答图片问题前必须先调用 xiao_vision_analyze，首选参数：imageUrl=${shorten(imageRefs[0] || "", 220)}。`,
        );
        lines.push("如果 xiao_vision_analyze 返回失败或超时，请明确告知“图片解析失败/超时，请重发清晰图片”，不要编造细节。");
        if (directImageRefs.length > 0 && isLikelyAttachmentOnlyInput(effectiveUserInput)) {
          lines.push("用户本条更像“只发图”。请先简短确认看到了图片，并追问他希望你看哪一部分，不要直接长篇总结。");
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

      lines.push(
        "语音回复：优先在回复末尾添加 [[audio_as_voice]]，系统会把当前回复内容直接合成为语音并发送。不要手写伪造的 <qqvoice> 网络链接。",
      );
      lines.push("不要在最终回复里展示内部状态标签（例如 [MOOD_CHANGE] / [UPDATE_PROFILE]）。");

      lines.push("XIAO_PERSONA_PROMPT_BEGIN");
      lines.push(personaPrompt);
      lines.push("XIAO_PERSONA_PROMPT_END");
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

      const outbound = sanitizeAssistantOutbound(content);
      const clean = cleanAssistantText(outbound.text || content);
      if (clean) {
        await addChatEntry(userKey, "assistant", clean);
      }
      const urlsInReply = extractUrls(content);
      if (urlsInReply.length > 0) {
        for (const url of urlsInReply) {
          await addLinkEvidence(userKey, "assistant", url, clean || content);
        }
      }

      if (outbound.voicePath) {
        const source = /^https?:\/\//i.test(outbound.voicePath) ? "url" : "file";
        const payload: Record<string, unknown> = {
          type: "media",
          mediaType: "audio",
          source,
          path: outbound.voicePath,
        };
        if (outbound.text) {
          payload.caption = outbound.text;
        }
        return {
          content: `QQBOT_PAYLOAD:\n${JSON.stringify(payload)}`,
        };
      }

      if (outbound.text && outbound.text !== content) {
        return {
          content: outbound.text,
        };
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

    registerXiaoWeatherCommand(api, {
      inferCityFromInput,
      shorten,
      fetchWeatherSummary,
    });

    registerXiaoStockCommand(api, {
      inferStockSymbol,
      fetchStockSummary,
    });

    registerXiaoTimeCommand(api);

    registerXiaoGithubCommand(api, {
      clamp,
      fetchGithubTrendingLite,
    });

    registerXiaoGithubWeeklyCommand(api, {
      applyAlias,
      normalizeUserKey,
      clamp,
      shorten,
      currentIsoWeekKey,
      hasGithubWeeklyPushed,
      markGithubWeeklyPushed,
      fetchGithubTrendingLite,
      fetchGithubRepoMeta,
      inferGithubHotReason,
      inferGithubUseHint,
    });

    registerXiaoSourceCommand(api, {
      applyAlias,
      normalizeUserKey,
      clamp,
      getRecentLinks,
    });

    registerXiaoUrlBasicCommand(api, {
      extractUrls,
      normalizeHttpUrl,
      fetchUrlBasicDigest,
    });

    registerXiaoDiagnosticsCommands(api, {
      formatUptimeSec,
      sessionUserMapSize: () => SESSION_USER_MAP.size,
      resolveStateFilePath,
      resolvePersonaPromptFilePath,
      envStatus,
      normalizeUserKey,
      applyAlias,
      shorten,
    });

    registerXiaoMemoCommand(api, {
      applyAlias,
      normalizeUserKey,
      getRecentMemos,
      addMemoEntry,
      searchMemos,
      deleteMemoEntry,
      shorten,
    });

    registerXiaoMemoryCommand(api, {
      applyAlias,
      normalizeUserKey,
      getRecentNotes,
      addMemoryNote,
      retrieveRagHits,
      shorten,
    });

    registerXiaoLinksCommand(api, {
      applyAlias,
      normalizeUserKey,
      clamp,
      getRecentLinks,
      shorten,
    });

    registerXiaoReflectCommand(api, {
      applyAlias,
      normalizeUserKey,
      clamp,
      runDailyReflection,
      shorten,
    });

    registerXiaoRemindCommand(api, {
      parseReminderArgs,
      resolveQqTargetFromCtx,
      execFileAsync,
      extractJsonPayload,
      shorten,
    });
  },
};

export default xiaoCorePlugin;
