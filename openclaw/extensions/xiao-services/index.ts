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
  const contentType = res.headers.get("content-type") || "application/octet-stream";
  const ab = await res.arrayBuffer();
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
    signal: AbortSignal.timeout(45000),
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
    60000,
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
    signal: AbortSignal.timeout(45000),
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
}): Promise<{ audioBytes: Uint8Array; mimeType: string; raw: unknown }> {
  const endpoint = resolveDashscopeAigcEndpoint(params.baseUrl);
  const payload = {
    model: params.model,
    input: { text: params.input },
    parameters: {
      voice: params.voice,
      format: params.format,
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
    60000,
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
      name: "xiao_weather_openmeteo",
      label: "Xiao Weather",
      description: "Get weather summary from Open-Meteo.",
      parameters: weatherSchema,
      async execute(_toolCallId: string, params: { city?: string }) {
        const city = (params.city || "").trim();
        if (!city) {
          return jsonResult({ ok: false, error: "city is required" });
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
            return jsonResult({ ok: false, error: "location_not_found", city });
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

          return jsonResult({
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
          return jsonResult({ ok: false, error: errToString(err) });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_stock_quote",
      label: "Xiao Stock Quote",
      description: "Get China A-share quote (Eastmoney primary, Sina fallback).",
      parameters: stockSchema,
      async execute(_toolCallId: string, params: { symbol?: string }) {
        const symbol = (params.symbol || "").trim();
        const normalized = normalizeStockSymbol(symbol);
        if (!normalized) {
          return jsonResult({ ok: false, error: "invalid_symbol", symbol });
        }

        try {
          const primary = await fetchStockEastmoney(normalized);
          return jsonResult({ ok: true, ...primary });
        } catch (err) {
          try {
            const fallback = await fetchStockSina(normalized);
            return jsonResult({
              ok: true,
              ...fallback,
              fallbackFrom: "eastmoney",
              fallbackReason: errToString(err),
            });
          } catch (fallbackErr) {
            return jsonResult({
              ok: false,
              error: errToString(err),
              fallbackError: errToString(fallbackErr),
            });
          }
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_vision_analyze",
      label: "Xiao Vision Analyze",
      description: "Analyze an image using Qwen-VL through DashScope compatible API.",
      parameters: visionSchema,
      async execute(_toolCallId: string, params: { imageUrl?: string; prompt?: string }) {
        const imageUrl = (params.imageUrl || "").trim();
        const prompt = (params.prompt || "").trim() || "Please analyze the image and summarize key points.";
        if (!imageUrl) {
          return jsonResult({ ok: false, error: "imageUrl is required" });
        }

        const apiKey = env("DASHSCOPE_API_KEY");
        if (!apiKey) {
          return jsonResult({
            ok: false,
            error: "missing_env",
            missing: ["DASHSCOPE_API_KEY"],
            migration_hint: "Set DASHSCOPE_API_KEY to migrate xiao_a vision capability.",
          });
        }

        const baseUrl = (env("DASHSCOPE_BASE_URL") || "https://dashscope.aliyuncs.com/compatible-mode/v1").replace(/\/$/, "");
        const model = env("QWEN_VL_MODEL") || "qwen-vl-plus-latest";

        const body = {
          model,
          messages: [
            {
              role: "user",
              content: [
                { type: "text", text: prompt },
                { type: "image_url", image_url: { url: imageUrl } },
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
            35000,
          )) as {
            choices?: Array<{ message?: { content?: string } }>;
          };

          const content = data.choices?.[0]?.message?.content || "";
          return jsonResult({
            ok: true,
            provider: "dashscope_compatible",
            model,
            content,
          });
        } catch (err) {
          return jsonResult({ ok: false, error: errToString(err) });
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
        const apiKey = env("DASHSCOPE_API_KEY");
        if (!apiKey) {
          return jsonResult({
            ok: false,
            error: "missing_env",
            missing: ["DASHSCOPE_API_KEY"],
            migration_hint: "Set DASHSCOPE_API_KEY to enable ASR migration from xiao_a.",
          });
        }

        const baseUrl = env("DASHSCOPE_BASE_URL") || "https://dashscope.aliyuncs.com/compatible-mode/v1";
        const model = (params.model || "").trim() || env("DASHSCOPE_ASR_MODEL") || "qwen3-asr-flash";

        try {
          const audio = await resolveAudioInput({
            audioUrl: params.audioUrl,
            audioBase64: params.audioBase64,
            audioPath: params.audioPath,
          });
          const maxBytes = 25 * 1024 * 1024;
          if (audio.bytes.byteLength > maxBytes) {
            return jsonResult({
              ok: false,
              error: "audio_too_large",
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

          return jsonResult({
            ok: true,
            provider,
            model: usedModel,
            text: result.text,
            audioMeta: {
              bytes: audio.bytes.byteLength,
              mimeType: audio.mimeType,
              filename: audio.filename,
            },
            raw: result.raw,
          });
        } catch (err) {
          return jsonResult({ ok: false, error: errToString(err) });
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
          returnBase64?: boolean;
        },
      ) {
        const text = (params.text || "").trim();
        if (!text) {
          return jsonResult({ ok: false, error: "text is required" });
        }

        const apiKey = env("DASHSCOPE_API_KEY");
        if (!apiKey) {
          return jsonResult({
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
              instructions: (params.instructions || "").trim() || undefined,
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
            mimeType: spoken.mimeType,
            bytes: spoken.audioBytes.byteLength,
            filePath,
          };
          if (params.returnBase64 === true) {
            result.audioBase64 = Buffer.from(spoken.audioBytes).toString("base64");
          }
          return jsonResult(result);
        } catch (err) {
          return jsonResult({ ok: false, error: errToString(err) });
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
        const to = (params.to || "").trim();
        const message = (params.message || "").trim();
        const minutesFromNow = clamp(Number(params.minutesFromNow || 0), 1, 43200);
        const channel = (params.channel || "").trim() || "qqbot";

        if (!to) {
          return jsonResult({ ok: false, error: "to is required" });
        }
        if (!message) {
          return jsonResult({ ok: false, error: "message is required" });
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
          return jsonResult({
            ok: true,
            cron: parsed,
            args,
          });
        } catch (err) {
          return jsonResult({
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
        try {
          const report = await runServiceProbe();
          return jsonResult({ ok: true, report });
        } catch (err) {
          return jsonResult({ ok: false, error: errToString(err) });
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
        lines.push("- xiao_weather_openmeteo");
        lines.push("- xiao_stock_quote");
        lines.push("- xiao_vision_analyze");
        lines.push("- xiao_asr_transcribe");
        lines.push("- xiao_tts_synthesize");
        lines.push("- xiao_schedule_reminder");
        lines.push("- xiao_service_probe");
        lines.push("");
        lines.push(`env file: ${resolveEnvFilePath()}`);
        lines.push("");
        lines.push("env status:");
        lines.push(`- GOOGLE_CSE_API_KEY: ${env("GOOGLE_CSE_API_KEY") ? "set" : "missing"}`);
        lines.push(`- GOOGLE_CSE_CX: ${env("GOOGLE_CSE_CX") ? "set" : "missing"}`);
        lines.push(`- DASHSCOPE_API_KEY: ${env("DASHSCOPE_API_KEY") ? "set" : "missing"}`);
        lines.push(`- DASHSCOPE_ASR_MODEL: ${env("DASHSCOPE_ASR_MODEL") || "(default: qwen3-asr-flash)"}`);
        lines.push(`- QWEN_TTS_MODEL: ${env("QWEN_TTS_MODEL") || "(default: qwen-tts-2025-05-22)"}`);
        lines.push(`- QWEN_TTS_VOICE: ${env("QWEN_TTS_VOICE") || "(default: Cherry)"}`);
        return { text: lines.join("\n") };
      },
    });
  },
};

export default xiaoServicesPlugin;
