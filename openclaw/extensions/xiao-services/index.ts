import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { existsSync, readFileSync, statSync } from "node:fs";
import { promises as fs } from "node:fs";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import type { AnyAgentTool, OpenClawPluginApi } from "openclaw/plugin-sdk";
import { emptyPluginConfigSchema } from "openclaw/plugin-sdk";

const execFileAsync = promisify(execFile);

type ToolResult = {
  content: Array<{ type: "text"; text: string }>;
  details: unknown;
};

type ProbeStatus = "ok" | "fail" | "skip";
type ObsMetric = {
  ts: string;
  request_id: string;
  user_key: string;
  tool_name: string;
  latency_ms: number;
  error_code: string;
};

let xiaoEnvCache: Record<string, string> | null = null;
let xiaoEnvMtimeMs = -1;

function resolveEnvFilePath(): string {
  const fromEnv = (process.env.XIAO_ENV_FILE || "").trim();
  if (fromEnv) {
    return fromEnv;
  }
  return path.join(homedir(), ".openclaw", ".env");
}

function unquoteEnvValue(value: string): string {
  const v = value.trim();
  if (
    (v.startsWith("\"") && v.endsWith("\"") && v.length >= 2) ||
    (v.startsWith("'") && v.endsWith("'") && v.length >= 2)
  ) {
    return v.slice(1, -1).trim();
  }
  return v;
}

function loadXiaoEnvFile(): Record<string, string> {
  const file = resolveEnvFilePath();
  if (!existsSync(file)) {
    xiaoEnvCache = {};
    xiaoEnvMtimeMs = -1;
    return xiaoEnvCache;
  }

  try {
    const stat = statSync(file);
    if (xiaoEnvCache && xiaoEnvMtimeMs === stat.mtimeMs) {
      return xiaoEnvCache;
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

    xiaoEnvCache = parsed;
    xiaoEnvMtimeMs = stat.mtimeMs;
    return parsed;
  } catch {
    xiaoEnvCache = {};
    xiaoEnvMtimeMs = -1;
    return xiaoEnvCache;
  }
}

function env(name: string): string {
  const runtime = (process.env[name] || "").trim();
  if (runtime) {
    return runtime;
  }
  const fileEnv = loadXiaoEnvFile();
  return (fileEnv[name] || "").trim();
}

function envAny(names: string[]): string {
  for (const name of names) {
    const runtime = (process.env[name] || "").trim();
    if (runtime) {
      return runtime;
    }
  }
  const fileEnv = loadXiaoEnvFile();
  for (const name of names) {
    const fileValue = (fileEnv[name] || "").trim();
    if (fileValue) {
      return fileValue;
    }
  }
  return "";
}

function errToString(err: unknown): string {
  if (err instanceof Error) {
    return err.message;
  }
  return String(err);
}

function jsonResult(payload: unknown): ToolResult {
  return {
    content: [{ type: "text", text: JSON.stringify(payload, null, 2) }],
    details: payload,
  };
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function mediaMaxBytes(): number {
  const mbRaw = env("XIAO_MEDIA_MAX_MB") || "20";
  const mb = clamp(Number(mbRaw), 1, 200);
  return Math.trunc(mb * 1024 * 1024);
}

function envTimeoutMs(name: string, defaultMs: number): number {
  const raw = env(name);
  const n = Number(raw || defaultMs);
  if (!Number.isFinite(n) || n <= 0) {
    return defaultMs;
  }
  return clamp(Math.trunc(n), 3000, 120000);
}

function normalizeScale(value: unknown, fallback: number = 1): number {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) {
    return fallback;
  }
  return clamp(n, 0.5, 2.0);
}

function classifyToolError(err: unknown): string {
  const msg = errToString(err).toLowerCase();
  if (msg.includes("media_too_large") || msg.includes("audio_too_large")) return "media_too_large";
  if (msg.includes("unsupported_media_type")) return "unsupported_media_type";
  if (msg.includes("aborted") || msg.includes("timeout")) return "timeout";
  if (msg.includes("http 401") || msg.includes("http 403")) return "auth_failed";
  if (msg.includes("http 429") || msg.includes("rate")) return "rate_limited";
  if (msg.includes("invalid_input")) return "invalid_input";
  if (msg.includes("missing_env")) return "missing_env";
  if (msg.includes("location_not_found")) return "location_not_found";
  if (msg.includes("invalid_symbol")) return "invalid_symbol";
  return "tool_error";
}

function fallbackHintForError(tool: "vision" | "asr" | "tts", errorCode: string): string {
  const code = (errorCode || "").trim().toLowerCase();
  if (tool === "vision") {
    if (code === "timeout") return "图片识别超时了，请稍后重试，或换更清晰的图片。";
    if (code === "media_too_large") return "图片文件太大了，请压缩到更小体积后再试。";
    if (code === "unsupported_media_type") return "图片格式暂不支持，建议使用 jpg/png/webp。";
    if (code === "auth_failed") return "图片识别服务鉴权失败，请检查 DASHSCOPE_API_KEY。";
    if (code === "rate_limited") return "图片识别服务当前较忙，请稍后重试。";
    return "图片识别暂时失败，请稍后重试或换一张更清晰/更小的图片。";
  }
  if (tool === "asr") {
    if (code === "timeout") return "语音识别超时了，请重试或改用更短音频。";
    if (code === "media_too_large") return "音频文件太大了，请压缩或截短后再试。";
    if (code === "unsupported_media_type") return "音频格式暂不支持，建议使用 mp3/wav/m4a。";
    if (code === "auth_failed") return "语音识别服务鉴权失败，请检查 DASHSCOPE_API_KEY。";
    if (code === "rate_limited") return "语音识别服务当前较忙，请稍后重试。";
    return "语音识别暂时失败，请重试或改用更短、更清晰的音频。";
  }
  if (code === "timeout") return "语音合成超时了，请稍后重试或缩短文本。";
  if (code === "auth_failed") return "语音合成服务鉴权失败，请检查 DASHSCOPE_API_KEY。";
  if (code === "rate_limited") return "语音合成服务当前较忙，请稍后重试。";
  return "语音合成暂时失败，请稍后重试或缩短文本。";
}

function buildTtsInstruction(base: string | undefined, rate: number, pitch: number, volume: number): string | undefined {
  const chunks: string[] = [];
  if (base && base.trim()) {
    chunks.push(base.trim());
  }
  if (Math.abs(rate - 1) > 0.01 || Math.abs(pitch - 1) > 0.01 || Math.abs(volume - 1) > 0.01) {
    chunks.push(`语速=${rate.toFixed(2)}x，音调=${pitch.toFixed(2)}x，音量=${volume.toFixed(2)}x。`);
  }
  if (!chunks.length) {
    return undefined;
  }
  return chunks.join(" ");
}

function buildVisionPrompt(prompt?: string): string {
  const base =
    env("XIAO_VISION_DEFAULT_PROMPT") ||
    "你是小a。请先客观描述图片中的主体与关键信息，再结合用户问题给出结论。若有不确定之处，请明确标注。请用简洁中文回答。";
  const custom = (prompt || "").trim();
  if (!custom) {
    return base;
  }
  return `${base}\n用户补充要求：${custom}`;
}

function resolveObsFilePath(): string {
  const fromEnv = (process.env.XIAO_OBS_FILE || "").trim();
  if (fromEnv) {
    return fromEnv;
  }
  return path.join(homedir(), ".openclaw", "xiao-core", "observability.jsonl");
}

function resolveObsUserKey(params: unknown): string {
  const p = (params || {}) as Record<string, unknown>;
  const raw =
    (typeof p.userKey === "string" && p.userKey.trim()) ||
    (typeof p.to === "string" && p.to.trim()) ||
    "unknown";
  const qq = raw.match(/^qqbot:(?:c2c|group):([A-Za-z0-9._:-]{6,128})$/i);
  if (qq?.[1]) {
    return `qqbot:${qq[1]}`;
  }
  return raw.slice(0, 160);
}

async function writeObsMetric(metric: ObsMetric): Promise<void> {
  try {
    const file = resolveObsFilePath();
    await fs.mkdir(path.dirname(file), { recursive: true });
    await fs.appendFile(file, `${JSON.stringify(metric)}\n`, "utf8");
  } catch {
    // best effort metrics logging
  }
}

async function obsWrap(toolName: string, userKey: string, startedAt: number, payload: unknown): Promise<ToolResult> {
  const obj = (payload || {}) as Record<string, unknown>;
  const errorCode =
    obj && obj.ok === false ? String(obj.error || "tool_error").slice(0, 120) : "";
  await writeObsMetric({
    ts: new Date().toISOString(),
    request_id: randomUUID(),
    user_key: userKey || "unknown",
    tool_name: toolName,
    latency_ms: Math.max(0, Date.now() - startedAt),
    error_code: errorCode,
  });
  return jsonResult(payload);
}

function resolveDashscopeAigcEndpoint(baseUrl: string): string {
  const fallback = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation";
  try {
    const u = new URL(baseUrl);
    return `${u.origin}/api/v1/services/aigc/multimodal-generation/generation`;
  } catch {
    return fallback;
  }
}

async function fetchJson(url: string, init?: RequestInit, timeoutMs: number = 12000): Promise<unknown> {
  const res = await fetch(url, {
    ...init,
    signal: AbortSignal.timeout(timeoutMs),
  });
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 300)}`);
  }
  if (!text) {
    return {};
  }
  try {
    return JSON.parse(text);
  } catch {
    return { raw: text };
  }
}

async function fetchJsonByCurl(params: {
  url: string;
  timeoutSec?: number;
  proxy?: string;
  headers?: Record<string, string>;
}): Promise<unknown> {
  const timeoutSec = clamp(Number(params.timeoutSec || 20), 3, 120);
  const args: string[] = ["-sS", "-L", "--fail-with-body", "--max-time", String(timeoutSec)];
  const proxy = (params.proxy || "").trim();
  if (proxy) {
    args.push("-x", proxy);
  }
  for (const [k, v] of Object.entries(params.headers || {})) {
    args.push("-H", `${k}: ${v}`);
  }
  args.push(params.url);

  try {
    const { stdout } = await execFileAsync("curl", args, {
      timeout: timeoutSec * 1000 + 3000,
      maxBuffer: 8 * 1024 * 1024,
      env: {
        ...process.env,
        // Avoid inheriting stale process-wide proxies; use explicit proxy only.
        HTTP_PROXY: proxy || "",
        HTTPS_PROXY: proxy || "",
        ALL_PROXY: proxy || "",
        http_proxy: proxy || "",
        https_proxy: proxy || "",
        all_proxy: proxy || "",
      },
    });
    const text = (stdout || "").trim();
    if (!text) {
      return {};
    }
    try {
      return JSON.parse(text);
    } catch {
      return { raw: text };
    }
  } catch (err) {
    const e = err as Error & { stdout?: string; stderr?: string };
    const msg = `${(e.stderr || "").trim()} ${(e.stdout || "").trim()}`.trim() || errToString(err);
    throw new Error(`curl request failed: ${msg.slice(0, 300)}`);
  }
}

async function fetchTextByCurl(params: {
  url: string;
  timeoutSec?: number;
  proxy?: string;
  headers?: Record<string, string>;
  compressed?: boolean;
}): Promise<string> {
  const timeoutSec = clamp(Number(params.timeoutSec || 20), 3, 120);
  const args: string[] = ["-sS", "-L", "--fail-with-body", "--max-time", String(timeoutSec)];
  if (params.compressed === true) {
    args.push("--compressed");
  }
  const proxy = (params.proxy || "").trim();
  if (proxy) {
    args.push("-x", proxy);
  }
  for (const [k, v] of Object.entries(params.headers || {})) {
    args.push("-H", `${k}: ${v}`);
  }
  args.push(params.url);

  try {
    const { stdout } = await execFileAsync("curl", args, {
      timeout: timeoutSec * 1000 + 3000,
      maxBuffer: 8 * 1024 * 1024,
      env: {
        ...process.env,
        HTTP_PROXY: proxy || "",
        HTTPS_PROXY: proxy || "",
        ALL_PROXY: proxy || "",
        http_proxy: proxy || "",
        https_proxy: proxy || "",
        all_proxy: proxy || "",
      },
    });
    return String(stdout || "");
  } catch (err) {
    const e = err as Error & { stdout?: string; stderr?: string };
    const msg = `${(e.stderr || "").trim()} ${(e.stdout || "").trim()}`.trim() || errToString(err);
    throw new Error(`curl request failed: ${msg.slice(0, 300)}`);
  }
}

function isoDateDaysAgo(days: number): string {
  const d = new Date(Date.now() - Math.max(0, days) * 24 * 60 * 60 * 1000);
  return d.toISOString().slice(0, 10);
}

async function fetchGithubTrendingBySearchApi(params: {
  since: "daily" | "weekly" | "monthly";
  language?: string;
  limit: number;
}): Promise<
  Array<{
    repo: string;
    url: string;
    description: string;
    language: string;
    starsTotal: number | null;
    starsPeriod: number | null;
    since: "daily" | "weekly" | "monthly";
    source: "search_api";
  }>
> {
  const since = params.since;
  const language = (params.language || "").trim();
  const limit = clamp(params.limit, 1, 20);
  const days = since === "daily" ? 1 : since === "monthly" ? 30 : 7;

  const queryParts = [`created:>=${isoDateDaysAgo(days)}`];
  if (language) {
    queryParts.push(`language:${language}`);
  }

  const url = new URL("https://api.github.com/search/repositories");
  url.searchParams.set("q", queryParts.join(" "));
  url.searchParams.set("sort", "stars");
  url.searchParams.set("order", "desc");
  url.searchParams.set("per_page", String(limit));

  const proxy = envAny([
    "GITHUB_TRENDING_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "https_proxy",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
  ]);
  const token = env("GITHUB_TOKEN");

  const headers: Record<string, string> = {
    Accept: "application/vnd.github+json",
    "User-Agent": "xiao-a-openclaw",
    "X-GitHub-Api-Version": "2022-11-28",
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const data = (await fetchJsonByCurl({
    url: url.toString(),
    timeoutSec: 20,
    proxy: proxy || undefined,
    headers,
  })) as {
    items?: Array<{
      full_name?: string;
      html_url?: string;
      description?: string | null;
      language?: string | null;
      stargazers_count?: number;
    }>;
    message?: string;
  };

  const items = Array.isArray(data.items) ? data.items : [];
  return items.slice(0, limit).map((it) => {
    const repo = String(it.full_name || "").trim();
    return {
      repo,
      url: String(it.html_url || `https://github.com/${repo}`),
      description: cleanText(it.description || "", 260),
      language: String(it.language || "").trim(),
      starsTotal: Number.isFinite(Number(it.stargazers_count)) ? Number(it.stargazers_count) : null,
      starsPeriod: null,
      since,
      source: "search_api" as const,
    };
  });
}

async function fetchBytes(
  url: string,
  init?: RequestInit,
  timeoutMs: number = 15000,
): Promise<{ bytes: Uint8Array; contentType: string }> {
  const res = await fetch(url, {
    ...init,
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 300)}`);
  }
  const maxBytes = mediaMaxBytes();
  const contentLength = Number(res.headers.get("content-length") || 0);
  if (Number.isFinite(contentLength) && contentLength > 0 && contentLength > maxBytes) {
    throw new Error(`media_too_large: content-length=${contentLength} > max=${maxBytes}`);
  }
  const contentType = res.headers.get("content-type") || "application/octet-stream";
  const ab = await res.arrayBuffer();
  if (ab.byteLength > maxBytes) {
    throw new Error(`media_too_large: bytes=${ab.byteLength} > max=${maxBytes}`);
  }
  return {
    bytes: new Uint8Array(ab),
    contentType,
  };
}

function extFromMime(mimeType: string): string {
  const mime = (mimeType || "").toLowerCase();
  if (mime.includes("mpeg") || mime.includes("mp3")) return "mp3";
  if (mime.includes("wav")) return "wav";
  if (mime.includes("ogg")) return "ogg";
  if (mime.includes("webm")) return "webm";
  if (mime.includes("aac")) return "aac";
  if (mime.includes("flac")) return "flac";
  if (mime.includes("m4a") || mime.includes("mp4")) return "m4a";
  return "bin";
}

function mimeFromPath(filePath: string): string {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".mp3") return "audio/mpeg";
  if (ext === ".wav") return "audio/wav";
  if (ext === ".ogg") return "audio/ogg";
  if (ext === ".webm") return "audio/webm";
  if (ext === ".aac") return "audio/aac";
  if (ext === ".flac") return "audio/flac";
  if (ext === ".m4a" || ext === ".mp4") return "audio/mp4";
  return "application/octet-stream";
}

function normalizeStockSymbol(input: string): { code: string; market: "SH" | "SZ" } | null {
  const t = (input || "").trim().toUpperCase();
  if (!t) {
    return null;
  }

  const matchTs = t.match(/^(\d{6})\.(SH|SZ)$/);
  if (matchTs) {
    return { code: matchTs[1], market: matchTs[2] as "SH" | "SZ" };
  }

  const matchPrefixed = t.match(/^(SH|SZ)(\d{6})$/);
  if (matchPrefixed) {
    return { code: matchPrefixed[2], market: matchPrefixed[1] as "SH" | "SZ" };
  }

  const matchCode = t.match(/(\d{6})/);
  if (!matchCode) {
    return null;
  }

  const code = matchCode[1];
  const market = code.startsWith("6") ? "SH" : "SZ";
  return { code, market };
}

async function fetchStockEastmoney(normalized: {
  code: string;
  market: "SH" | "SZ";
}): Promise<{
  provider: "eastmoney";
  symbol: string;
  name: string;
  quote: {
    price: number;
    preclose: number;
    open: number;
    high: number;
    low: number;
    pctChange: number;
    volume: number;
    amount: number;
  };
}> {
  const secid = `${normalized.market === "SH" ? "1" : "0"}.${normalized.code}`;
  const url =
    "https://push2.eastmoney.com/api/qt/stock/get" +
    `?secid=${encodeURIComponent(secid)}` +
    "&fields=f57,f58,f43,f44,f45,f46,f47,f48,f60,f169";
  const data = (await fetchJson(url, undefined, 8000)) as { data?: Record<string, number | string> };
  const d = data.data || {};
  const price = Number(d.f43 || 0) / 100;
  if (!Number.isFinite(price) || price <= 0) {
    throw new Error("eastmoney returned empty quote");
  }
  return {
    provider: "eastmoney",
    symbol: `${normalized.code}.${normalized.market}`,
    name: String(d.f58 || "").trim(),
    quote: {
      price,
      preclose: Number(d.f60 || 0) / 100,
      open: Number(d.f46 || 0) / 100,
      high: Number(d.f44 || 0) / 100,
      low: Number(d.f45 || 0) / 100,
      pctChange: Number(d.f169 || 0) / 100,
      volume: Number(d.f47 || 0),
      amount: Number(d.f48 || 0),
    },
  };
}

async function fetchStockSina(normalized: {
  code: string;
  market: "SH" | "SZ";
}): Promise<{
  provider: "sina";
  symbol: string;
  name: string;
  quote: {
    price: number;
    preclose: number;
    open: number;
    high: number;
    low: number;
    pctChange: number;
    volume: number;
    amount: number;
  };
}> {
  const symbol = `${normalized.market.toLowerCase()}${normalized.code}`;
  const url = `https://hq.sinajs.cn/list=${encodeURIComponent(symbol)}`;
  const res = await fetch(url, {
    method: "GET",
    headers: {
      "User-Agent": "Mozilla/5.0",
      Referer: "https://finance.sina.com.cn/",
    },
    signal: AbortSignal.timeout(8000),
  });
  const rawBody = new Uint8Array(await res.arrayBuffer());
  let body = "";
  try {
    body = new TextDecoder("gb18030").decode(rawBody);
  } catch {
    body = new TextDecoder("utf-8").decode(rawBody);
  }
  if (!res.ok) {
    throw new Error(`sina HTTP ${res.status}`);
  }
  const payloadMatch = body.match(/=\"([^\"]+)\"/);
  if (!payloadMatch?.[1]) {
    throw new Error("sina response parse failed");
  }
  const parts = payloadMatch[1].split(",");
  if (parts.length < 10) {
    throw new Error("sina quote fields insufficient");
  }

  const name = (parts[0] || "").trim() || `${normalized.code}.${normalized.market}`;
  const open = Number(parts[1] || 0);
  const preclose = Number(parts[2] || 0);
  const price = Number(parts[3] || 0);
  const high = Number(parts[4] || 0);
  const low = Number(parts[5] || 0);
  const volume = Number(parts[8] || 0);
  const amount = Number(parts[9] || 0);
  if (!Number.isFinite(price) || price <= 0) {
    throw new Error("sina returned empty quote");
  }
  const pctChange = preclose > 0 ? ((price - preclose) / preclose) * 100 : 0;

  return {
    provider: "sina",
    symbol: `${normalized.code}.${normalized.market}`,
    name,
    quote: {
      price,
      preclose,
      open,
      high,
      low,
      pctChange,
      volume,
      amount,
    },
  };
}

function weatherCodeToText(code: unknown): string {
  const n = Number(code);
  const mapping: Record<number, string> = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
  };
  return Number.isFinite(n) && mapping[n] ? mapping[n] : "Unknown";
}

function decodeHtmlEntities(input: string): string {
  const map: Record<string, string> = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": "\"",
    "&#39;": "'",
    "&nbsp;": " ",
  };
  return (input || "").replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (m) => map[m] || m);
}

function stripHtmlTags(input: string): string {
  return decodeHtmlEntities((input || "").replace(/<[^>]+>/g, " "));
}

function cleanText(input: string, maxLen: number = 260): string {
  const text = stripHtmlTags(input).replace(/\s+/g, " ").trim();
  if (text.length <= maxLen) {
    return text;
  }
  return `${text.slice(0, maxLen)}...`;
}

function normalizeHttpUrl(raw: string): string {
  const text = (raw || "").trim();
  if (!text) {
    throw new Error("invalid_input: url is required");
  }
  let u: URL;
  try {
    u = new URL(text);
  } catch {
    throw new Error("invalid_input: url is not valid");
  }
  if (!/^https?:$/i.test(u.protocol)) {
    throw new Error("invalid_input: only http/https url is supported");
  }
  return u.toString();
}

function pickMetaDescription(html: string): string {
  const patterns = [
    /<meta\s+name=["']description["']\s+content=["']([^"']+)["']/i,
    /<meta\s+content=["']([^"']+)["']\s+name=["']description["']/i,
    /<meta\s+property=["']og:description["']\s+content=["']([^"']+)["']/i,
    /<meta\s+content=["']([^"']+)["']\s+property=["']og:description["']/i,
  ];
  for (const p of patterns) {
    const m = html.match(p);
    if (m?.[1]) {
      const t = cleanText(m[1], 600);
      if (t) return t;
    }
  }
  return "";
}

function extractReadableFromHtml(html: string, maxChars: number): string {
  const stripped = (html || "")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ")
    .replace(/<svg[\s\S]*?<\/svg>/gi, " ")
    .replace(/<header[\s\S]*?<\/header>/gi, " ")
    .replace(/<footer[\s\S]*?<\/footer>/gi, " ")
    .replace(/<nav[\s\S]*?<\/nav>/gi, " ");

  const plain = cleanText(stripped, Math.max(4000, maxChars * 3));
  if (!plain) {
    return "";
  }

  // Prefer text chunks that are likely content body (longer sentences).
  const chunks = plain
    .split(/[。！？.!?\n]/)
    .map((s) => s.trim())
    .filter((s) => s.length >= 16)
    .slice(0, 120);
  const joined = chunks.join("。");
  return cleanText(joined || plain, maxChars);
}

async function fetchGithubTrending(params: {
  since: "daily" | "weekly" | "monthly";
  language?: string;
  limit: number;
}): Promise<
  Array<{
    repo: string;
    url: string;
    description: string;
    language: string;
    starsTotal: number | null;
    starsPeriod: number | null;
    since: "daily" | "weekly" | "monthly";
    source: "trending_html" | "search_api";
  }>
> {
  const since = params.since;
  const language = (params.language || "").trim().toLowerCase();
  const limit = clamp(params.limit, 1, 20);
  const langPath = language ? `/${encodeURIComponent(language)}` : "";
  const url = `https://github.com/trending${langPath}?since=${since}`;

  const proxy = envAny([
    "GITHUB_TRENDING_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "https_proxy",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
  ]);

  try {
    const html = await fetchTextByCurl({
      url,
      timeoutSec: 35,
      compressed: true,
      proxy: proxy || undefined,
      headers: {
        "User-Agent":
          "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        Accept: "text/html,application/xhtml+xml",
        Referer: "https://github.com/trending",
      },
    });

    const articleRegex = /<article[\s\S]*?<\/article>/g;
    const rows = (html.match(articleRegex) || [])
      .filter((row) => /Box-row/.test(row))
      .slice(0, Math.max(limit * 3, 20));

    const out: Array<{
      repo: string;
      url: string;
      description: string;
      language: string;
      starsTotal: number | null;
      starsPeriod: number | null;
      since: "daily" | "weekly" | "monthly";
      source: "trending_html" | "search_api";
    }> = [];

    const seen = new Set<string>();
    for (const row of rows) {
      const repoMatch = row.match(/<h2[\s\S]*?<a[^>]*href="\/([^"?#]+)"/i);
      const repo = cleanText(repoMatch?.[1] || "", 120).replace(/\s+/g, "");
      if (!repo || !repo.includes("/") || seen.has(repo)) {
        continue;
      }

      const descMatch = row.match(/<p[^>]*>([\s\S]*?)<\/p>/i);
      const description = cleanText(descMatch?.[1] || "", 260);

      const langMatch = row.match(/itemprop="programmingLanguage"[^>]*>([\s\S]*?)<\/span>/i);
      const repoLang = cleanText(langMatch?.[1] || "", 60);

      const starTotalMatch = row.match(/href="\/[^"?#]+\/stargazers"[^>]*>\s*([\d,]+)\s*<\/a>/i);
      const starsTotal = starTotalMatch?.[1] ? Number(starTotalMatch[1].replace(/,/g, "")) : null;

      const starPeriodMatch = row.match(/([\d,]+)\s+stars?\s+(today|this week|this month)/i);
      const starsPeriod = starPeriodMatch?.[1] ? Number(starPeriodMatch[1].replace(/,/g, "")) : null;

      seen.add(repo);
      out.push({
        repo,
        url: `https://github.com/${repo}`,
        description,
        language: repoLang || "",
        starsTotal: Number.isFinite(starsTotal) ? starsTotal : null,
        starsPeriod: Number.isFinite(starsPeriod) ? starsPeriod : null,
        since,
        source: "trending_html",
      });

      if (out.length >= limit) {
        break;
      }
    }

    if (out.length > 0) {
      return out;
    }
  } catch {
    // fallback below
  }

  return await fetchGithubTrendingBySearchApi({
    since,
    language,
    limit,
  });
}

function parseBase64AudioInput(input: string): {
  bytes: Uint8Array;
  mimeType: string;
  filename: string;
} {
  const text = (input || "").trim();
  if (!text) {
    throw new Error("audioBase64 is empty");
  }

  const dataUrlMatch = text.match(/^data:([^;]+);base64,(.+)$/i);
  if (dataUrlMatch) {
    const mimeType = dataUrlMatch[1] || "application/octet-stream";
    const b64 = dataUrlMatch[2] || "";
    const bytes = Buffer.from(b64, "base64");
    const ext = extFromMime(mimeType);
    return {
      bytes: new Uint8Array(bytes),
      mimeType,
      filename: `audio.${ext}`,
    };
  }

  const bytes = Buffer.from(text, "base64");
  if (!bytes.length) {
    throw new Error("invalid base64 audio input");
  }
  return {
    bytes: new Uint8Array(bytes),
    mimeType: "application/octet-stream",
    filename: "audio.bin",
  };
}

function parseBase64ImageInput(input: string): {
  bytes: Uint8Array;
  mimeType: string;
} {
  const text = (input || "").trim();
  if (!text) {
    throw new Error("invalid_input: imageUrl is empty");
  }
  const dataUrlMatch = text.match(/^data:([^;]+);base64,(.+)$/i);
  if (!dataUrlMatch?.[1] || !dataUrlMatch?.[2]) {
    throw new Error("invalid_input: imageUrl must be a valid image URL or data URL");
  }
  const mimeType = (dataUrlMatch[1] || "application/octet-stream").trim().toLowerCase();
  if (!mimeType.startsWith("image/")) {
    throw new Error(`unsupported_media_type: ${mimeType}`);
  }
  const bytes = Buffer.from(dataUrlMatch[2], "base64");
  if (!bytes.length) {
    throw new Error("invalid_input: image data is empty");
  }
  const maxBytes = mediaMaxBytes();
  if (bytes.byteLength > maxBytes) {
    throw new Error(`media_too_large: bytes=${bytes.byteLength} > max=${maxBytes}`);
  }
  return {
    bytes: new Uint8Array(bytes),
    mimeType,
  };
}

async function resolveVisionImageInput(imageUrl: string, timeoutMs: number): Promise<{
  imageRef: string;
  source: "data_url" | "downloaded_url";
  mimeType: string;
  bytes: number;
}> {
  const raw = (imageUrl || "").trim();
  if (!raw) {
    throw new Error("invalid_input: imageUrl is required");
  }

  if (/^data:/i.test(raw)) {
    const parsed = parseBase64ImageInput(raw);
    return {
      imageRef: raw,
      source: "data_url",
      mimeType: parsed.mimeType,
      bytes: parsed.bytes.byteLength,
    };
  }

  let urlObj: URL;
  try {
    urlObj = new URL(raw);
  } catch {
    throw new Error("invalid_input: imageUrl is not a valid URL");
  }
  if (!/^https?:$/i.test(urlObj.protocol)) {
    throw new Error("invalid_input: imageUrl must use http/https");
  }

  const downloaded = await fetchBytes(raw, undefined, clamp(Math.trunc(timeoutMs * 0.7), 6000, 30000));
  const mimeType = (downloaded.contentType || "").toLowerCase();
  if (!mimeType.startsWith("image/")) {
    throw new Error(`unsupported_media_type: ${mimeType || "unknown"}`);
  }
  const dataUrl = `data:${mimeType};base64,${Buffer.from(downloaded.bytes).toString("base64")}`;
  return {
    imageRef: dataUrl,
    source: "downloaded_url",
    mimeType,
    bytes: downloaded.bytes.byteLength,
  };
}

async function resolveAudioInput(params: {
  audioUrl?: string;
  audioBase64?: string;
  audioPath?: string;
}): Promise<{ bytes: Uint8Array; mimeType: string; filename: string }> {
  const audioUrl = (params.audioUrl || "").trim();
  const audioBase64 = (params.audioBase64 || "").trim();
  const audioPath = (params.audioPath || "").trim();

  if (!audioUrl && !audioBase64 && !audioPath) {
    throw new Error("audioUrl or audioBase64 or audioPath is required");
  }

  if (audioUrl) {
    const downloaded = await fetchBytes(audioUrl, undefined, 25000);
    const ext = extFromMime(downloaded.contentType);
    return {
      bytes: downloaded.bytes,
      mimeType: downloaded.contentType,
      filename: `audio.${ext}`,
    };
  }

  if (audioPath) {
    const absolutePath = path.resolve(audioPath);
    const st = await fs.stat(absolutePath);
    const maxBytes = mediaMaxBytes();
    if (st.size > maxBytes) {
      throw new Error(`media_too_large: file=${st.size} > max=${maxBytes}`);
    }
    const fileBytes = await fs.readFile(absolutePath);
    if (!fileBytes.byteLength) {
      throw new Error("audioPath points to empty file");
    }
    const mimeType = mimeFromPath(absolutePath);
    return {
      bytes: new Uint8Array(fileBytes),
      mimeType,
      filename: path.basename(absolutePath) || `audio.${extFromMime(mimeType)}`,
    };
  }

  return parseBase64AudioInput(audioBase64);
}

async function callAsrOpenAICompat(params: {
  apiKey: string;
  baseUrl: string;
  model: string;
  audio: { bytes: Uint8Array; mimeType: string; filename: string };
  language?: string;
  prompt?: string;
  timeoutMs?: number;
}): Promise<{ text: string; raw: unknown }> {
  const url = `${params.baseUrl.replace(/\/$/, "")}/audio/transcriptions`;
  const form = new FormData();
  form.append("model", params.model);
  if (params.language) {
    form.append("language", params.language);
  }
  if (params.prompt) {
    form.append("prompt", params.prompt);
  }
  form.append(
    "file",
    new Blob([params.audio.bytes], { type: params.audio.mimeType || "application/octet-stream" }),
    params.audio.filename,
  );

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${params.apiKey}`,
    },
    body: form,
    signal: AbortSignal.timeout(params.timeoutMs || 45000),
  });

  const textBody = await res.text();
  if (!res.ok) {
    throw new Error(`ASR HTTP ${res.status}: ${textBody.slice(0, 300)}`);
  }

  let raw: unknown = textBody;
  try {
    raw = JSON.parse(textBody);
  } catch {
    raw = textBody;
  }

  if (raw && typeof raw === "object") {
    const obj = raw as Record<string, unknown>;
    const text =
      (typeof obj.text === "string" && obj.text) ||
      (typeof obj.output_text === "string" && obj.output_text) ||
      (typeof obj.transcript === "string" && obj.transcript) ||
      "";
    return { text: text.trim(), raw };
  }

  return {
    text: String(raw || "").trim(),
    raw,
  };
}

async function callAsrDashscopeAigc(params: {
  apiKey: string;
  baseUrl: string;
  model: string;
  audio: { bytes: Uint8Array; mimeType: string; filename: string };
  prompt?: string;
  timeoutMs?: number;
}): Promise<{ text: string; raw: unknown }> {
  const endpoint = resolveDashscopeAigcEndpoint(params.baseUrl);
  const mime = params.audio.mimeType || "audio/wav";
  const audioDataUrl = `data:${mime};base64,${Buffer.from(params.audio.bytes).toString("base64")}`;
  const content: Array<Record<string, unknown>> = [{ audio: audioDataUrl }];
  if (params.prompt) {
    content.push({ text: params.prompt });
  }
  const payload: Record<string, unknown> = {
    model: params.model,
    input: {
      messages: [
        {
          role: "user",
          content,
        },
      ],
    },
    parameters: {
      asr_options: {
        sample_rate: 16000,
        channel: 1,
      },
    },
  };

  const raw = await fetchJson(
    endpoint,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${params.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
    params.timeoutMs || 60000,
  );

  const text =
    (raw as Record<string, unknown>)?.output &&
    typeof (raw as Record<string, unknown>).output === "object"
      ? (() => {
          const output = (raw as Record<string, unknown>).output as Record<string, unknown>;
          const choices = output.choices;
          if (!Array.isArray(choices) || !choices[0] || typeof choices[0] !== "object") {
            return "";
          }
          const message = (choices[0] as Record<string, unknown>).message as Record<string, unknown> | undefined;
          if (!message) {
            return "";
          }
          const contentList = message.content;
          if (!Array.isArray(contentList)) {
            return "";
          }
          for (const item of contentList) {
            if (!item || typeof item !== "object") continue;
            const t = (item as Record<string, unknown>).text;
            if (typeof t === "string" && t.trim()) {
              return t.trim();
            }
          }
          return "";
        })()
      : "";

  if (!text) {
    throw new Error(`ASR response missing text: ${JSON.stringify(raw).slice(0, 280)}`);
  }
  return { text, raw };
}

async function callTtsOpenAICompat(params: {
  apiKey: string;
  baseUrl: string;
  model: string;
  voice: string;
  input: string;
  format: string;
  instructions?: string;
  timeoutMs?: number;
}): Promise<{ audioBytes: Uint8Array; mimeType: string }> {
  const url = `${params.baseUrl.replace(/\/$/, "")}/audio/speech`;
  const payload: Record<string, unknown> = {
    model: params.model,
    input: params.input,
    voice: params.voice,
    format: params.format,
    response_format: params.format,
  };
  if (params.instructions) {
    payload.instructions = params.instructions;
  }

  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${params.apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(params.timeoutMs || 45000),
  });

  const contentType = res.headers.get("content-type") || "application/octet-stream";
  if (!res.ok) {
    const errText = await res.text().catch(() => "");
    throw new Error(`TTS HTTP ${res.status}: ${errText.slice(0, 300)}`);
  }

  if (contentType.includes("application/json")) {
    const text = await res.text();
    const parsed = JSON.parse(text) as Record<string, unknown>;
    const b64 =
      (typeof parsed.audio === "string" && parsed.audio) ||
      (typeof parsed.audio_base64 === "string" && parsed.audio_base64) ||
      "";
    if (!b64) {
      throw new Error("TTS JSON response does not contain audio base64 field");
    }
    const bytes = Buffer.from(b64, "base64");
    return {
      audioBytes: new Uint8Array(bytes),
      mimeType: params.format === "wav" ? "audio/wav" : "audio/mpeg",
    };
  }

  const ab = await res.arrayBuffer();
  return {
    audioBytes: new Uint8Array(ab),
    mimeType: contentType,
  };
}

async function callTtsDashscopeAigc(params: {
  apiKey: string;
  baseUrl: string;
  model: string;
  voice: string;
  input: string;
  format: string;
  rate?: number;
  pitch?: number;
  volume?: number;
  timeoutMs?: number;
}): Promise<{ audioBytes: Uint8Array; mimeType: string; raw: unknown }> {
  const endpoint = resolveDashscopeAigcEndpoint(params.baseUrl);
  const payload = {
    model: params.model,
    input: { text: params.input },
    parameters: {
      voice: params.voice,
      format: params.format,
      rate: params.rate,
      pitch: params.pitch,
      volume: params.volume,
    },
  };

  const raw = (await fetchJson(
    endpoint,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${params.apiKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
    params.timeoutMs || 60000,
  )) as Record<string, unknown>;

  const output = (raw.output || {}) as Record<string, unknown>;
  const audio = (output.audio || {}) as Record<string, unknown>;
  const b64 = typeof audio.data === "string" ? audio.data.trim() : "";
  if (b64) {
    return {
      audioBytes: new Uint8Array(Buffer.from(b64, "base64")),
      mimeType: params.format === "wav" ? "audio/wav" : params.format === "ogg" ? "audio/ogg" : "audio/mpeg",
      raw,
    };
  }

  const url = typeof audio.url === "string" ? audio.url.trim() : "";
  if (url) {
    const downloaded = await fetchBytes(url, undefined, 30000);
    return {
      audioBytes: downloaded.bytes,
      mimeType: downloaded.contentType || "application/octet-stream",
      raw,
    };
  }

  throw new Error(`TTS response missing audio data/url: ${JSON.stringify(raw).slice(0, 280)}`);
}

async function writeTempAudioFile(bytes: Uint8Array, ext: string): Promise<string> {
  const dir = path.join(tmpdir(), "openclaw-xiao-services");
  await fs.mkdir(dir, { recursive: true });
  const filePath = path.join(dir, `tts-${Date.now()}-${randomUUID()}.${ext}`);
  await fs.writeFile(filePath, Buffer.from(bytes));
  return filePath;
}

async function runServiceProbe(): Promise<{
  summary: { ok: number; fail: number; skip: number };
  checks: Array<{ name: string; status: ProbeStatus; detail: string }>;
}> {
  const checks: Array<{ name: string; status: ProbeStatus; detail: string }> = [];

  const googleKey = env("GOOGLE_CSE_API_KEY");
  const googleCx = env("GOOGLE_CSE_CX");
  if (!googleKey || !googleCx) {
    checks.push({
      name: "google_cse",
      status: "skip",
      detail: "Missing GOOGLE_CSE_API_KEY or GOOGLE_CSE_CX",
    });
  } else {
    try {
      const url = new URL("https://www.googleapis.com/customsearch/v1");
      url.searchParams.set("key", googleKey);
      url.searchParams.set("cx", googleCx);
      url.searchParams.set("q", "OpenClaw");
      url.searchParams.set("num", "1");
      const proxy = envAny([
        "GOOGLE_CSE_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "https_proxy",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
      ]);
      const data = (await fetchJsonByCurl({
        url: url.toString(),
        timeoutSec: 20,
        proxy: proxy || undefined,
      })) as { items?: unknown[]; error?: unknown };
      if (data.error) {
        throw new Error(`google api error: ${JSON.stringify(data.error).slice(0, 220)}`);
      }
      const count = Array.isArray(data.items) ? data.items.length : 0;
      checks.push({ name: "google_cse", status: "ok", detail: `items=${count}` });
    } catch (err) {
      checks.push({ name: "google_cse", status: "fail", detail: errToString(err) });
    }
  }

  try {
    const geoUrl = new URL("https://geocoding-api.open-meteo.com/v1/search");
    geoUrl.searchParams.set("name", "Mianyang");
    geoUrl.searchParams.set("count", "1");
    const geo = (await fetchJson(geoUrl.toString(), undefined, 10000)) as {
      results?: Array<{ latitude: number; longitude: number }>;
    };
    if (!Array.isArray(geo.results) || geo.results.length === 0) {
      throw new Error("no geocoding result");
    }
    checks.push({ name: "open_meteo", status: "ok", detail: "geocoding reachable" });
  } catch (err) {
    checks.push({ name: "open_meteo", status: "fail", detail: errToString(err) });
  }

  try {
    const normalized = normalizeStockSymbol("600519");
    if (!normalized) {
      throw new Error("symbol normalization failed");
    }
    try {
      const quote = await fetchStockEastmoney(normalized);
      checks.push({
        name: "stock_quote",
        status: "ok",
        detail: `${quote.provider} price=${quote.quote.price}`,
      });
    } catch (eastmoneyErr) {
      const fallback = await fetchStockSina(normalized);
      checks.push({
        name: "stock_quote",
        status: "ok",
        detail: `${fallback.provider} fallback price=${fallback.quote.price}; primary_error=${errToString(eastmoneyErr)}`,
      });
    }
  } catch (err) {
    checks.push({ name: "stock_quote", status: "fail", detail: errToString(err) });
  }

  try {
    const items = await fetchGithubTrending({ since: "weekly", limit: 1 });
    if (!items.length) {
      throw new Error("empty weekly trending result");
    }
    checks.push({
      name: "github_trending",
      status: "ok",
      detail: `${items[0].repo} stars_period=${items[0].starsPeriod ?? "-"}`,
    });
  } catch (err) {
    checks.push({ name: "github_trending", status: "fail", detail: errToString(err) });
  }

  const dashscopeKey = env("DASHSCOPE_API_KEY");
  if (!dashscopeKey) {
    checks.push({ name: "dashscope", status: "skip", detail: "Missing DASHSCOPE_API_KEY" });
  } else {
    try {
      const baseUrl = env("DASHSCOPE_BASE_URL") || "https://dashscope.aliyuncs.com/compatible-mode/v1";
      await fetchJson(
        `${baseUrl.replace(/\/$/, "")}/models`,
        {
          method: "GET",
          headers: {
            Authorization: `Bearer ${dashscopeKey}`,
          },
        },
        10000,
      );
      checks.push({ name: "dashscope", status: "ok", detail: "model endpoint reachable" });
    } catch (err) {
      checks.push({ name: "dashscope", status: "fail", detail: errToString(err) });
    }
  }

  const ok = checks.filter((x) => x.status === "ok").length;
  const fail = checks.filter((x) => x.status === "fail").length;
  const skip = checks.filter((x) => x.status === "skip").length;
  return { summary: { ok, fail, skip }, checks };
}

const searchSchema = {
  type: "object",
  additionalProperties: false,
  required: ["query"],
  properties: {
    query: { type: "string", description: "Search query" },
    maxResults: { type: "integer", minimum: 1, maximum: 10, description: "Result count (1-10)" },
  },
} as const;

const urlDigestSchema = {
  type: "object",
  additionalProperties: false,
  required: ["url"],
  properties: {
    url: { type: "string", description: "HTTP/HTTPS URL to summarize" },
    maxChars: { type: "integer", minimum: 240, maximum: 4000, description: "Max chars for extracted body text" },
  },
} as const;

const weatherSchema = {
  type: "object",
  additionalProperties: false,
  required: ["city"],
  properties: {
    city: { type: "string", description: "City name, e.g. Mianyang" },
  },
} as const;

const stockSchema = {
  type: "object",
  additionalProperties: false,
  required: ["symbol"],
  properties: {
    symbol: { type: "string", description: "Stock symbol, e.g. 600519 or 600519.SH" },
  },
} as const;

const githubTrendingSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    since: {
      type: "string",
      enum: ["daily", "weekly", "monthly"],
      description: "Trending period",
    },
    language: {
      type: "string",
      description: "Optional language filter, e.g. python / go / rust",
    },
    limit: {
      type: "integer",
      minimum: 1,
      maximum: 20,
      description: "Result count (1-20)",
    },
  },
} as const;

const visionSchema = {
  type: "object",
  additionalProperties: false,
  required: ["imageUrl"],
  properties: {
    imageUrl: { type: "string", description: "Image URL to analyze" },
    prompt: { type: "string", description: "Optional analysis prompt" },
  },
} as const;

const asrSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    audioUrl: { type: "string", description: "Audio URL for transcription" },
    audioBase64: { type: "string", description: "Raw base64 audio or data URL" },
    audioPath: { type: "string", description: "Local audio file path for transcription" },
    model: { type: "string", description: "ASR model override" },
    language: { type: "string", description: "Optional language hint" },
    prompt: { type: "string", description: "Optional transcription prompt" },
  },
} as const;

const ttsSchema = {
  type: "object",
  additionalProperties: false,
  required: ["text"],
  properties: {
    text: { type: "string", description: "Text to synthesize" },
    model: { type: "string", description: "TTS model override" },
    voice: { type: "string", description: "Voice name" },
    format: {
      type: "string",
      enum: ["mp3", "wav", "ogg"],
      description: "Output audio format",
    },
    instructions: { type: "string", description: "Optional style instruction" },
    rate: { type: "number", minimum: 0.5, maximum: 2.0, description: "Speech rate multiplier (0.5-2.0)" },
    pitch: { type: "number", minimum: 0.5, maximum: 2.0, description: "Pitch multiplier (0.5-2.0)" },
    volume: { type: "number", minimum: 0.5, maximum: 2.0, description: "Volume multiplier (0.5-2.0)" },
    returnBase64: { type: "boolean", description: "Include full base64 in output" },
  },
} as const;

const reminderSchema = {
  type: "object",
  additionalProperties: false,
  required: ["to", "message", "minutesFromNow"],
  properties: {
    to: { type: "string", description: "QQ target id/openid" },
    message: { type: "string", description: "Reminder content" },
    minutesFromNow: { type: "integer", minimum: 1, maximum: 43200, description: "Delay minutes" },
    name: { type: "string", description: "Optional job name" },
    channel: { type: "string", description: "Channel id, default qqbot" },
  },
} as const;

const emptySchema = {
  type: "object",
  additionalProperties: false,
  properties: {},
} as const;

const xiaoServicesPlugin = {
  id: "xiao-services",
  name: "Xiao Services",
  description: "Migrated service tools for search/weather/stock/vision/voice",
  configSchema: emptyPluginConfigSchema(),
  register(api: OpenClawPluginApi) {
    api.registerTool({
      name: "xiao_search_google",
      label: "Xiao Google Search",
      description: "Search web using Google CSE API.",
      parameters: searchSchema,
      async execute(_toolCallId: string, params: { query?: string; maxResults?: number }) {
        const query = (params.query || "").trim();
        const maxResults = clamp(Number(params.maxResults || 5), 1, 10);
        if (!query) {
          return jsonResult({ ok: false, error: "query is required" });
        }

        const apiKey = env("GOOGLE_CSE_API_KEY");
        const cx = env("GOOGLE_CSE_CX");
        if (!apiKey || !cx) {
          return jsonResult({
            ok: false,
            error: "missing_env",
            missing: [!apiKey ? "GOOGLE_CSE_API_KEY" : null, !cx ? "GOOGLE_CSE_CX" : null].filter(Boolean),
            migration_hint: "Set GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX to migrate search from xiao_a.",
          });
        }

        try {
          const url = new URL("https://www.googleapis.com/customsearch/v1");
          url.searchParams.set("key", apiKey);
          url.searchParams.set("cx", cx);
          url.searchParams.set("q", query);
          url.searchParams.set("num", String(maxResults));
          url.searchParams.set("fields", "items(title,link,snippet)");
          const proxy = envAny([
            "GOOGLE_CSE_PROXY",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "https_proxy",
            "http_proxy",
            "ALL_PROXY",
            "all_proxy",
          ]);
          const data = (await fetchJsonByCurl({
            url: url.toString(),
            timeoutSec: 25,
            proxy: proxy || undefined,
          })) as {
            items?: Array<{ title?: string; link?: string; snippet?: string }>;
            error?: unknown;
          };
          if (data.error) {
            return jsonResult({
              ok: false,
              error: "google_api_error",
              detail: JSON.stringify(data.error).slice(0, 280),
            });
          }
          const items = Array.isArray(data.items) ? data.items : [];
          return jsonResult({
            ok: true,
            provider: "google_cse",
            query,
            results: items.map((it) => ({
              title: String(it.title || "").trim(),
              href: String(it.link || "").trim(),
              body: String(it.snippet || "").trim(),
            })),
          });
        } catch (err) {
          return jsonResult({ ok: false, error: errToString(err) });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_url_digest",
      label: "Xiao URL Digest",
      description: "Fetch a web page and extract title/description/body preview for summarization.",
      parameters: urlDigestSchema,
      async execute(_toolCallId: string, params: { url?: string; maxChars?: number }) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        const maxChars = clamp(Number(params.maxChars || 1600), 240, 4000);
        let url = "";
        try {
          url = normalizeHttpUrl(params.url || "");
        } catch (err) {
          const errorCode = classifyToolError(err);
          return await obsWrap("xiao_url_digest", obsUser, obsStart, {
            ok: false,
            error: errorCode,
            errorDetail: errToString(err),
          });
        }

        try {
          const html = await fetchTextByCurl({
            url,
            timeoutSec: 25,
            compressed: true,
            headers: {
              "User-Agent":
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
              Accept: "text/html,application/xhtml+xml",
            },
          });

          const title = cleanText((html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] || "").trim(), 220);
          const description = pickMetaDescription(html);
          const preview = extractReadableFromHtml(html, maxChars);
          if (!title && !description && !preview) {
            return await obsWrap("xiao_url_digest", obsUser, obsStart, {
              ok: false,
              error: "empty_response",
              url,
            });
          }

          let domain = "";
          try {
            domain = new URL(url).hostname;
          } catch {
            domain = "";
          }

          return await obsWrap("xiao_url_digest", obsUser, obsStart, {
            ok: true,
            url,
            domain,
            title,
            description,
            preview,
            maxChars,
          });
        } catch (err) {
          const errorCode = classifyToolError(err);
          return await obsWrap("xiao_url_digest", obsUser, obsStart, {
            ok: false,
            error: errorCode,
            errorDetail: errToString(err),
            url,
          });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_weather_openmeteo",
      label: "Xiao Weather",
      description: "Get weather summary from Open-Meteo.",
      parameters: weatherSchema,
      async execute(_toolCallId: string, params: { city?: string }) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        const city = (params.city || "").trim();
        if (!city) {
          return await obsWrap("xiao_weather_openmeteo", obsUser, obsStart, { ok: false, error: "city is required" });
        }

        try {
          const geoUrl = new URL("https://geocoding-api.open-meteo.com/v1/search");
          geoUrl.searchParams.set("name", city);
          geoUrl.searchParams.set("count", "1");
          geoUrl.searchParams.set("language", "zh");
          geoUrl.searchParams.set("format", "json");

          const geo = (await fetchJson(geoUrl.toString())) as {
            results?: Array<{
              name: string;
              latitude: number;
              longitude: number;
              timezone?: string;
              country?: string;
            }>;
          };
          const item = Array.isArray(geo.results) ? geo.results[0] : undefined;
          if (!item) {
            return await obsWrap("xiao_weather_openmeteo", obsUser, obsStart, {
              ok: false,
              error: "location_not_found",
              city,
            });
          }

          const forecastUrl = new URL("https://api.open-meteo.com/v1/forecast");
          forecastUrl.searchParams.set("latitude", String(item.latitude));
          forecastUrl.searchParams.set("longitude", String(item.longitude));
          forecastUrl.searchParams.set("timezone", item.timezone || "auto");
          forecastUrl.searchParams.set(
            "current",
            "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
          );
          forecastUrl.searchParams.set(
            "daily",
            "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
          );
          forecastUrl.searchParams.set("forecast_days", "1");

          const fc = (await fetchJson(forecastUrl.toString())) as {
            current?: Record<string, unknown>;
            daily?: Record<string, unknown>;
            timezone?: string;
          };

          const current = fc.current || {};
          const daily = fc.daily || {};
          const dailyCode = Array.isArray(daily.weather_code) ? daily.weather_code[0] : current.weather_code;
          const todayMax = Array.isArray(daily.temperature_2m_max) ? daily.temperature_2m_max[0] : null;
          const todayMin = Array.isArray(daily.temperature_2m_min) ? daily.temperature_2m_min[0] : null;
          const precip = Array.isArray(daily.precipitation_probability_max)
            ? daily.precipitation_probability_max[0]
            : null;

          return await obsWrap("xiao_weather_openmeteo", obsUser, obsStart, {
            ok: true,
            provider: "open-meteo",
            city: item.name,
            country: item.country,
            timezone: fc.timezone || item.timezone,
            current: {
              temperature: current.temperature_2m,
              feelsLike: current.apparent_temperature,
              windSpeed: current.wind_speed_10m,
              weatherCode: current.weather_code,
            },
            today: {
              weatherCode: dailyCode,
              weatherText: weatherCodeToText(dailyCode),
              maxTemp: todayMax,
              minTemp: todayMin,
              precipProbMax: precip,
            },
          });
        } catch (err) {
          return await obsWrap("xiao_weather_openmeteo", obsUser, obsStart, {
            ok: false,
            error: errToString(err),
          });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_stock_quote",
      label: "Xiao Stock Quote",
      description: "Get China A-share quote (Eastmoney primary, Sina fallback).",
      parameters: stockSchema,
      async execute(_toolCallId: string, params: { symbol?: string }) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        const symbol = (params.symbol || "").trim();
        const normalized = normalizeStockSymbol(symbol);
        if (!normalized) {
          return await obsWrap("xiao_stock_quote", obsUser, obsStart, {
            ok: false,
            error: "invalid_symbol",
            symbol,
          });
        }

        try {
          const primary = await fetchStockEastmoney(normalized);
          return await obsWrap("xiao_stock_quote", obsUser, obsStart, { ok: true, ...primary });
        } catch (err) {
          try {
            const fallback = await fetchStockSina(normalized);
            return await obsWrap("xiao_stock_quote", obsUser, obsStart, {
              ok: true,
              ...fallback,
              fallbackFrom: "eastmoney",
              fallbackReason: errToString(err),
            });
          } catch (fallbackErr) {
            return await obsWrap("xiao_stock_quote", obsUser, obsStart, {
              ok: false,
              error: errToString(err),
              fallbackError: errToString(fallbackErr),
            });
          }
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_github_trending",
      label: "Xiao GitHub Trending",
      description: "Fetch GitHub trending repositories from github.com/trending.",
      parameters: githubTrendingSchema,
      async execute(
        _toolCallId: string,
        params: { since?: "daily" | "weekly" | "monthly"; language?: string; limit?: number },
      ) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        const since = (params.since || "weekly").trim().toLowerCase();
        const allowed = new Set(["daily", "weekly", "monthly"]);
        if (!allowed.has(since)) {
          return await obsWrap("xiao_github_trending", obsUser, obsStart, {
            ok: false,
            error: "invalid_since",
            since,
          });
        }
        const language = (params.language || "").trim();
        const limit = clamp(Number(params.limit || 5), 1, 20);

        try {
          const items = await fetchGithubTrending({
            since: since as "daily" | "weekly" | "monthly",
            language,
            limit,
          });
          const source = String((items[0] as { source?: string } | undefined)?.source || "trending_html");
          return await obsWrap("xiao_github_trending", obsUser, obsStart, {
            ok: true,
            provider: source === "search_api" ? "github_search_api" : "github_trending_html",
            since,
            language,
            count: items.length,
            items,
          });
        } catch (err) {
          return await obsWrap("xiao_github_trending", obsUser, obsStart, {
            ok: false,
            error: errToString(err),
            since,
            language,
            limit,
          });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_vision_analyze",
      label: "Xiao Vision Analyze",
      description: "Analyze an image using Qwen-VL through DashScope compatible API.",
      parameters: visionSchema,
      async execute(_toolCallId: string, params: { imageUrl?: string; prompt?: string }) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        const imageUrl = (params.imageUrl || "").trim();
        const prompt = buildVisionPrompt(params.prompt);
        if (!imageUrl) {
          return await obsWrap("xiao_vision_analyze", obsUser, obsStart, { ok: false, error: "invalid_input" });
        }

        const apiKey = env("DASHSCOPE_API_KEY");
        if (!apiKey) {
          return await obsWrap("xiao_vision_analyze", obsUser, obsStart, {
            ok: false,
            error: "missing_env",
            missing: ["DASHSCOPE_API_KEY"],
            migration_hint: "Set DASHSCOPE_API_KEY to migrate xiao_a vision capability.",
          });
        }

        const baseUrl = (env("DASHSCOPE_BASE_URL") || "https://dashscope.aliyuncs.com/compatible-mode/v1").replace(/\/$/, "");
        const model = env("QWEN_VL_MODEL") || "qwen-vl-plus-latest";
        const timeoutMs = envTimeoutMs("XIAO_VISION_TIMEOUT_MS", 35000);
        let resolvedImage:
          | {
              imageRef: string;
              source: "data_url" | "downloaded_url";
              mimeType: string;
              bytes: number;
            }
          | null = null;

        try {
          resolvedImage = await resolveVisionImageInput(imageUrl, timeoutMs);
        } catch (err) {
          const errorCode = classifyToolError(err);
          return await obsWrap("xiao_vision_analyze", obsUser, obsStart, {
            ok: false,
            error: errorCode,
            errorDetail: errToString(err),
            fallbackHint: fallbackHintForError("vision", errorCode),
          });
        }
        if (!resolvedImage) {
          return await obsWrap("xiao_vision_analyze", obsUser, obsStart, {
            ok: false,
            error: "tool_error",
            fallbackHint: fallbackHintForError("vision", "tool_error"),
          });
        }

        const body = {
          model,
          messages: [
            {
              role: "user",
              content: [
                { type: "text", text: prompt },
                { type: "image_url", image_url: { url: resolvedImage.imageRef } },
              ],
            },
          ],
          max_tokens: 300,
          temperature: 0.4,
        };

        try {
          const data = (await fetchJson(
            `${baseUrl}/chat/completions`,
            {
              method: "POST",
              headers: {
                Authorization: `Bearer ${apiKey}`,
                "Content-Type": "application/json",
              },
              body: JSON.stringify(body),
            },
            timeoutMs,
          )) as {
            choices?: Array<{ message?: { content?: string } }>;
          };

          const content = data.choices?.[0]?.message?.content || "";
          if (!content.trim()) {
            return await obsWrap("xiao_vision_analyze", obsUser, obsStart, {
              ok: false,
              error: "empty_response",
              timeoutMs,
            });
          }
          return await obsWrap("xiao_vision_analyze", obsUser, obsStart, {
            ok: true,
            provider: "dashscope_compatible",
            model,
            timeoutMs,
            imageMeta: {
              source: resolvedImage.source,
              mimeType: resolvedImage.mimeType,
              bytes: resolvedImage.bytes,
            },
            content,
          });
        } catch (err) {
          const errorCode = classifyToolError(err);
          return await obsWrap("xiao_vision_analyze", obsUser, obsStart, {
            ok: false,
            error: errorCode,
            errorDetail: errToString(err),
            fallbackHint: fallbackHintForError("vision", errorCode),
          });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_asr_transcribe",
      label: "Xiao ASR Transcribe",
      description: "Transcribe audio to text (DashScope OpenAI-compatible /audio/transcriptions).",
      parameters: asrSchema,
      async execute(
        _toolCallId: string,
        params: {
          audioUrl?: string;
          audioBase64?: string;
          audioPath?: string;
          model?: string;
          language?: string;
          prompt?: string;
        },
      ) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        const apiKey = env("DASHSCOPE_API_KEY");
        if (!apiKey) {
          return await obsWrap("xiao_asr_transcribe", obsUser, obsStart, {
            ok: false,
            error: "missing_env",
            missing: ["DASHSCOPE_API_KEY"],
            migration_hint: "Set DASHSCOPE_API_KEY to enable ASR migration from xiao_a.",
          });
        }

        const baseUrl = env("DASHSCOPE_BASE_URL") || "https://dashscope.aliyuncs.com/compatible-mode/v1";
        const model = (params.model || "").trim() || env("DASHSCOPE_ASR_MODEL") || "qwen3-asr-flash";
        const timeoutMs = envTimeoutMs("XIAO_ASR_TIMEOUT_MS", 45000);

        try {
          const audio = await resolveAudioInput({
            audioUrl: params.audioUrl,
            audioBase64: params.audioBase64,
            audioPath: params.audioPath,
          });
          const maxBytes = mediaMaxBytes();
          if (audio.bytes.byteLength > maxBytes) {
            return await obsWrap("xiao_asr_transcribe", obsUser, obsStart, {
              ok: false,
              error: "media_too_large",
              bytes: audio.bytes.byteLength,
              maxBytes,
            });
          }

          let result: { text: string; raw: unknown };
          let provider = "dashscope_compatible";
          let usedModel = model;
          const prompt = (params.prompt || "").trim() || undefined;
          try {
            result = await callAsrOpenAICompat({
              apiKey,
              baseUrl,
              model,
              audio,
              language: (params.language || "").trim() || undefined,
              prompt,
              timeoutMs,
            });
          } catch (compatErr) {
            const fallbackModels = [model, "qwen3-asr-flash"];
            let lastErr: unknown = compatErr;
            let resolved: { text: string; raw: unknown } | null = null;
            for (const m of fallbackModels) {
              if (!m) continue;
              try {
                resolved = await callAsrDashscopeAigc({
                  apiKey,
                  baseUrl,
                  model: m,
                  audio,
                  prompt,
                  timeoutMs: envTimeoutMs("XIAO_ASR_AIGC_TIMEOUT_MS", 60000),
                });
                usedModel = m;
                provider = "dashscope_aigc";
                break;
              } catch (err) {
                lastErr = err;
              }
            }
            if (!resolved) {
              throw new Error(
                `ASR compat failed: ${errToString(compatErr)}; AIGC fallback failed: ${errToString(lastErr)}`,
              );
            }
            result = resolved;
          }

          return await obsWrap("xiao_asr_transcribe", obsUser, obsStart, {
            ok: true,
            provider,
            model: usedModel,
            timeoutMs,
            text: result.text,
            audioMeta: {
              bytes: audio.bytes.byteLength,
              mimeType: audio.mimeType,
              filename: audio.filename,
            },
            raw: result.raw,
          });
        } catch (err) {
          const errorCode = classifyToolError(err);
          return await obsWrap("xiao_asr_transcribe", obsUser, obsStart, {
            ok: false,
            error: errorCode,
            errorDetail: errToString(err),
            fallbackHint: fallbackHintForError("asr", errorCode),
          });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_tts_synthesize",
      label: "Xiao TTS Synthesize",
      description: "Synthesize text to speech (DashScope OpenAI-compatible /audio/speech).",
      parameters: ttsSchema,
      async execute(
        _toolCallId: string,
        params: {
          text?: string;
          model?: string;
          voice?: string;
          format?: string;
          instructions?: string;
          rate?: number;
          pitch?: number;
          volume?: number;
          returnBase64?: boolean;
        },
      ) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        const text = (params.text || "").trim();
        if (!text) {
          return await obsWrap("xiao_tts_synthesize", obsUser, obsStart, { ok: false, error: "invalid_input" });
        }

        const apiKey = env("DASHSCOPE_API_KEY");
        if (!apiKey) {
          return await obsWrap("xiao_tts_synthesize", obsUser, obsStart, {
            ok: false,
            error: "missing_env",
            missing: ["DASHSCOPE_API_KEY"],
            migration_hint: "Set DASHSCOPE_API_KEY to enable TTS migration from xiao_a.",
          });
        }

        const baseUrl = env("DASHSCOPE_BASE_URL") || "https://dashscope.aliyuncs.com/compatible-mode/v1";
        const model = (params.model || "").trim() || env("QWEN_TTS_MODEL") || "qwen-tts-2025-05-22";
        const voice = (params.voice || "").trim() || env("QWEN_TTS_VOICE") || "Cherry";
        const format = ((params.format || "").trim().toLowerCase() || "mp3") as "mp3" | "wav" | "ogg";
        const timeoutMs = envTimeoutMs("XIAO_TTS_TIMEOUT_MS", 45000);
        const rate = normalizeScale(params.rate ?? (env("QWEN_TTS_RATE") || 1), 1);
        const pitch = normalizeScale(params.pitch ?? (env("QWEN_TTS_PITCH") || 1), 1);
        const volume = normalizeScale(params.volume ?? (env("QWEN_TTS_VOLUME") || 1), 1);
        const instructions = buildTtsInstruction((params.instructions || "").trim() || undefined, rate, pitch, volume);

        try {
          let spoken: { audioBytes: Uint8Array; mimeType: string };
          let provider = "dashscope_compatible";
          let usedModel = model;
          try {
            spoken = await callTtsOpenAICompat({
              apiKey,
              baseUrl,
              model,
              voice,
              input: text,
              format,
              instructions,
              timeoutMs,
            });
          } catch (compatErr) {
            const fallbackModels = [model, "qwen-tts-2025-05-22"];
            let lastErr: unknown = compatErr;
            let resolved: { audioBytes: Uint8Array; mimeType: string } | null = null;
            for (const m of fallbackModels) {
              if (!m) continue;
              try {
                resolved = await callTtsDashscopeAigc({
                  apiKey,
                  baseUrl,
                  model: m,
                  voice,
                  input: text,
                  format,
                  rate,
                  pitch,
                  volume,
                  timeoutMs: envTimeoutMs("XIAO_TTS_AIGC_TIMEOUT_MS", 60000),
                });
                usedModel = m;
                provider = "dashscope_aigc";
                break;
              } catch (err) {
                lastErr = err;
              }
            }
            if (!resolved) {
              throw new Error(
                `TTS compat failed: ${errToString(compatErr)}; AIGC fallback failed: ${errToString(lastErr)}`,
              );
            }
            spoken = resolved;
          }

          const ext = extFromMime(spoken.mimeType) || format;
          const filePath = await writeTempAudioFile(spoken.audioBytes, ext);
          const result: Record<string, unknown> = {
            ok: true,
            provider,
            model: usedModel,
            voice,
            format,
            timeoutMs,
            rate,
            pitch,
            volume,
            mimeType: spoken.mimeType,
            bytes: spoken.audioBytes.byteLength,
            filePath,
          };
          if (params.returnBase64 === true) {
            result.audioBase64 = Buffer.from(spoken.audioBytes).toString("base64");
          }
          return await obsWrap("xiao_tts_synthesize", obsUser, obsStart, result);
        } catch (err) {
          const errorCode = classifyToolError(err);
          return await obsWrap("xiao_tts_synthesize", obsUser, obsStart, {
            ok: false,
            error: errorCode,
            errorDetail: errToString(err),
            fallbackHint: fallbackHintForError("tts", errorCode),
          });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_schedule_reminder",
      label: "Xiao Schedule Reminder",
      description: "Create one-shot QQ reminder using OpenClaw cron.",
      parameters: reminderSchema,
      async execute(
        _toolCallId: string,
        params: { to?: string; message?: string; minutesFromNow?: number; name?: string; channel?: string },
      ) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        const to = (params.to || "").trim();
        const message = (params.message || "").trim();
        const minutesFromNow = clamp(Number(params.minutesFromNow || 0), 1, 43200);
        const channel = (params.channel || "").trim() || "qqbot";

        if (!to) {
          return await obsWrap("xiao_schedule_reminder", obsUser, obsStart, {
            ok: false,
            error: "to is required",
          });
        }
        if (!message) {
          return await obsWrap("xiao_schedule_reminder", obsUser, obsStart, {
            ok: false,
            error: "message is required",
          });
        }

        const name =
          (params.name || "").trim() ||
          `xiao-reminder-${new Date().toISOString().replace(/[:.]/g, "-")}-${Math.trunc(Math.random() * 1000)}`;

        const args = [
          "cron",
          "add",
          "--name",
          name,
          "--at",
          `${minutesFromNow}m`,
          "--message",
          message,
          "--announce",
          "--channel",
          channel,
          "--to",
          to,
          "--session",
          "isolated",
          "--delete-after-run",
          "--json",
        ];

        try {
          const { stdout, stderr } = await execFileAsync("openclaw", args, {
            timeout: 20000,
            maxBuffer: 1024 * 1024,
          });
          let parsed: unknown = { stdout: stdout.trim(), stderr: stderr.trim() };
          const out = (stdout || "").trim();
          if (out) {
            try {
              parsed = JSON.parse(out);
            } catch {
              parsed = { stdout: out, stderr: (stderr || "").trim() };
            }
          }
          return await obsWrap("xiao_schedule_reminder", obsUser, obsStart, {
            ok: true,
            cron: parsed,
            args,
          });
        } catch (err) {
          return await obsWrap("xiao_schedule_reminder", obsUser, obsStart, {
            ok: false,
            error: errToString(err),
            args,
          });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_service_probe",
      label: "Xiao Service Probe",
      description: "Probe external APIs used by xiao-services and report availability.",
      parameters: emptySchema,
      async execute() {
        const obsStart = Date.now();
        try {
          const report = await runServiceProbe();
          return await obsWrap("xiao_service_probe", "system:probe", obsStart, { ok: true, report });
        } catch (err) {
          return await obsWrap("xiao_service_probe", "system:probe", obsStart, {
            ok: false,
            error: errToString(err),
          });
        }
      },
    } as AnyAgentTool);

    api.registerCommand({
      name: "xiao-services",
      description: "Show xiao-services status or run probe. Usage: /xiao-services [status|probe]",
      acceptsArgs: true,
      handler: async (ctx) => {
        const action = (ctx.args || "status").trim().toLowerCase() || "status";

        if (action === "probe") {
          const report = await runServiceProbe();
          const lines: string[] = [];
          lines.push(
            `probe summary: ok=${report.summary.ok}, fail=${report.summary.fail}, skip=${report.summary.skip}`,
          );
          for (const c of report.checks) {
            lines.push(`- ${c.name}: ${c.status} (${c.detail})`);
          }
          return { text: lines.join("\n") };
        }

        const lines: string[] = [];
        lines.push("xiao-services tools loaded:");
        lines.push("- xiao_search_google");
        lines.push("- xiao_url_digest");
        lines.push("- xiao_weather_openmeteo");
        lines.push("- xiao_stock_quote");
        lines.push("- xiao_github_trending");
        lines.push("- xiao_vision_analyze");
        lines.push("- xiao_asr_transcribe");
        lines.push("- xiao_tts_synthesize");
        lines.push("- xiao_schedule_reminder");
        lines.push("- xiao_service_probe");
        lines.push("");
        lines.push(`env file: ${resolveEnvFilePath()}`);
        lines.push(`obs file: ${resolveObsFilePath()}`);
        lines.push("");
        lines.push("env status:");
        lines.push(`- GOOGLE_CSE_API_KEY: ${env("GOOGLE_CSE_API_KEY") ? "set" : "missing"}`);
        lines.push(`- GOOGLE_CSE_CX: ${env("GOOGLE_CSE_CX") ? "set" : "missing"}`);
        lines.push(`- GITHUB_TRENDING_PROXY: ${env("GITHUB_TRENDING_PROXY") ? "set" : "missing(optional)"}`);
        lines.push(`- DASHSCOPE_API_KEY: ${env("DASHSCOPE_API_KEY") ? "set" : "missing"}`);
        lines.push(`- DASHSCOPE_ASR_MODEL: ${env("DASHSCOPE_ASR_MODEL") || "(default: qwen3-asr-flash)"}`);
        lines.push(`- QWEN_TTS_MODEL: ${env("QWEN_TTS_MODEL") || "(default: qwen-tts-2025-05-22)"}`);
        lines.push(`- QWEN_TTS_VOICE: ${env("QWEN_TTS_VOICE") || "(default: Cherry)"}`);
        lines.push(`- QWEN_TTS_RATE: ${env("QWEN_TTS_RATE") || "(default: 1.0)"}`);
        lines.push(`- QWEN_TTS_PITCH: ${env("QWEN_TTS_PITCH") || "(default: 1.0)"}`);
        lines.push(`- QWEN_TTS_VOLUME: ${env("QWEN_TTS_VOLUME") || "(default: 1.0)"}`);
        lines.push(`- XIAO_MEDIA_MAX_MB: ${env("XIAO_MEDIA_MAX_MB") || "(default: 20)"}`);
        lines.push(`- XIAO_VISION_TIMEOUT_MS: ${env("XIAO_VISION_TIMEOUT_MS") || "(default: 35000)"}`);
        lines.push(`- XIAO_VISION_DEFAULT_PROMPT: ${env("XIAO_VISION_DEFAULT_PROMPT") ? "set" : "using built-in"}`);
        lines.push(`- XIAO_ASR_TIMEOUT_MS: ${env("XIAO_ASR_TIMEOUT_MS") || "(default: 45000)"}`);
        lines.push(`- XIAO_TTS_TIMEOUT_MS: ${env("XIAO_TTS_TIMEOUT_MS") || "(default: 45000)"}`);
        return { text: lines.join("\n") };
      },
    });
  },
};

export default xiaoServicesPlugin;
