import type { OpenClawPluginApi, AnyAgentTool } from "openclaw/plugin-sdk";
import { emptyPluginConfigSchema } from "openclaw/plugin-sdk";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { env, envAny } from "../shared/env.js";
import { errToString, clamp } from "../shared/text.js";
import { fetchJson, fetchJsonByCurl, fetchTextByCurl } from "../shared/request.js";

// Feature modules
import { normalizeStockSymbol, fetchStockEastmoney, fetchStockSina } from "./features/stock.js";
import { fetchGithubTrending } from "./features/github.js";
import { callAsrOpenAICompat, callTtsOpenAICompat } from "./features/openai.js";
import { callAsrDashscopeAigc, callTtsDashscopeAigc } from "./features/dashscope.js";
import { resolveVisionImageInput, resolveAudioInput, extFromMime } from "./features/media.js";
import { obsWrap, jsonResult, resolveObsUserKey, resolveObsFilePath } from "./features/obs.js";
import { resolveMusic } from "./features/music.js";
import { recommendMovies } from "./features/movie.js";
import { searchRestaurants } from "./features/restaurant.js";
import { trackExpress } from "./features/express.js";

const execFileAsync = promisify(execFile);

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

async function writeTempAudioFile(bytes: Uint8Array, ext: string): Promise<string> {
  const dir = path.join(tmpdir(), "openclaw-xiao-services");
  await fs.mkdir(dir, { recursive: true });
  const filePath = path.join(dir, `tts-${Date.now()}-${randomUUID()}.${ext}`);
  await fs.writeFile(filePath, Buffer.from(bytes));
  return filePath;
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

function sleepMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchUrlDigestHtml(url: string, timeoutSec: number): Promise<string> {
  const headers = {
    "User-Agent":
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    Accept: "text/html,application/xhtml+xml",
  };
  let lastErr: unknown = null;

  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      return await fetchTextByCurl({
        url,
        timeoutSec,
        compressed: true,
        headers,
      });
    } catch (err) {
      lastErr = err;
      if (attempt < 2) {
        await sleepMs(400);
      }
    }
  }

  try {
    const res = await fetch(url, {
      method: "GET",
      headers,
      signal: AbortSignal.timeout(timeoutSec * 1000),
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    return await res.text();
  } catch (fallbackErr) {
    throw new Error(`url_fetch_failed: curl=${errToString(lastErr)}; fetch=${errToString(fallbackErr)}`);
  }
}

type ProbeStatus = "ok" | "fail" | "skip";
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

const musicSchema = {
  type: "object",
  additionalProperties: false,
  required: ["url"],
  properties: {
    url: { type: "string", description: "Music share url" },
  },
} as const;

const movieSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    query: { type: "string", description: "Movie keyword" },
    limit: { type: "integer", minimum: 1, maximum: 10 },
  },
} as const;

const restaurantSchema = {
  type: "object",
  additionalProperties: false,
  required: ["city"],
  properties: {
    city: { type: "string", description: "City name" },
    keyword: { type: "string", description: "Food keyword" },
    limit: { type: "integer", minimum: 1, maximum: 10 },
  },
} as const;

const expressSchema = {
  type: "object",
  additionalProperties: false,
  required: ["company", "number"],
  properties: {
    company: { type: "string", description: "Courier company" },
    number: { type: "string", description: "Tracking number" },
  },
} as const;

const emptySchema = {
  type: "object",
  additionalProperties: false,
  properties: {},
} as const;

// 定义并导出了 xiao-services 核心插件对象
// 它集成封装了所有外部检索及 AI 衍生能力，主要通过 Tool 的形式向外提供接口
const xiaoServicesPlugin = {
  id: "xiao-services",
  name: "Xiao Services",
  description: "Migrated service tools for search/weather/stock/vision/voice",
  configSchema: emptyPluginConfigSchema(),
  register(api: OpenClawPluginApi) {
    // 注册谷歌搜索工具
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
      name: "xiao_music_resolve",
      label: "Xiao Music Resolve",
      description: "Resolve music share info from url",
      parameters: musicSchema,
      async execute(_toolCallId: string, params: { url?: string }) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        try {
          const result = await resolveMusic((params.url || "").trim());
          return await obsWrap("xiao_music_resolve", obsUser, obsStart, result);
        } catch (err) {
          return await obsWrap("xiao_music_resolve", obsUser, obsStart, {
            ok: false,
            error: errToString(err),
          });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_movie_recommend",
      label: "Xiao Movie Recommend",
      description: "Recommend movies by keyword",
      parameters: movieSchema,
      async execute(_toolCallId: string, params: { query?: string; limit?: number }) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        try {
          const result = await recommendMovies((params.query || "").trim(), clamp(Number(params.limit || 5), 1, 10));
          return await obsWrap("xiao_movie_recommend", obsUser, obsStart, result);
        } catch (err) {
          return await obsWrap("xiao_movie_recommend", obsUser, obsStart, {
            ok: false,
            error: errToString(err),
          });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_restaurant_search",
      label: "Xiao Restaurant Search",
      description: "Search restaurants by city/keyword",
      parameters: restaurantSchema,
      async execute(_toolCallId: string, params: { city?: string; keyword?: string; limit?: number }) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        try {
          const result = await searchRestaurants(
            (params.city || "").trim(),
            (params.keyword || "").trim(),
            clamp(Number(params.limit || 5), 1, 10),
          );
          return await obsWrap("xiao_restaurant_search", obsUser, obsStart, result);
        } catch (err) {
          return await obsWrap("xiao_restaurant_search", obsUser, obsStart, {
            ok: false,
            error: errToString(err),
          });
        }
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_express_track",
      label: "Xiao Express Track",
      description: "Track express order",
      parameters: expressSchema,
      async execute(_toolCallId: string, params: { company?: string; number?: string }) {
        const obsStart = Date.now();
        const obsUser = resolveObsUserKey(params);
        try {
          const result = await trackExpress((params.company || "").trim(), (params.number || "").trim());
          return await obsWrap("xiao_express_track", obsUser, obsStart, result);
        } catch (err) {
          return await obsWrap("xiao_express_track", obsUser, obsStart, {
            ok: false,
            error: errToString(err),
          });
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
          const html = await fetchUrlDigestHtml(url, 25);

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

        try {
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
          const maxReqBytes = 1024 * 1024 * 20; // 20 MB just as a limit for audio bytes
          if (audio.bytes.byteLength > maxReqBytes) {
            return await obsWrap("xiao_asr_transcribe", obsUser, obsStart, {
              ok: false,
              error: "media_too_large",
              bytes: audio.bytes.byteLength,
              maxBytes: maxReqBytes,
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
        lines.push("- xiao_music_resolve");
        lines.push("- xiao_movie_recommend");
        lines.push("- xiao_restaurant_search");
        lines.push("- xiao_express_track");
        lines.push("");
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
        lines.push(`- TMDB_API_KEY: ${env("TMDB_API_KEY") ? "set" : "missing"}`);
        lines.push(`- AMAP_KEY: ${env("AMAP_KEY") ? "set" : "missing"}`);
        lines.push(`- KDNIAO_KEY: ${env("KDNIAO_KEY") ? "set" : "missing"}`);
        lines.push(`- KDNIAO_CUSTOMER: ${env("KDNIAO_CUSTOMER") ? "set" : "missing"}`);
        return { text: lines.join("\n") };
      },
    });
  },
};

export default xiaoServicesPlugin;
