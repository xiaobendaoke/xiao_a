import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { shorten } from "../../shared/text.js";
import { extractUrls } from "../utils/media.js";

export function decodeHtmlEntities(text: string): string {
  if (!text) return "";
  return text
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, "\"")
    .replace(/&#39;/gi, "'");
}

export function stripHtmlToText(html: string): string {
  if (!html) return "";
  const noScript = html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, " ");
  const plain = noScript.replace(/<[^>]+>/g, " ");
  return decodeHtmlEntities(plain).replace(/\s+/g, " ").trim();
}

export async function fetchTextWithTimeout(
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

export function normalizeHttpUrl(input: string): string | null {
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

export function extractTitleFromHtml(html: string): string {
  const m = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  return shorten(stripHtmlToText(m?.[1] || ""), 180);
}

export function extractDescriptionFromHtml(html: string): string {
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

export type UrlBasicDigest = {
  url: string;
  domain: string;
  title: string;
  description: string;
  preview: string;
};

export async function fetchUrlBasicDigest(url: string): Promise<UrlBasicDigest | null> {
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

export function registerXiaoUrlBasicCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-url-basic 命令，用于提取网页内容基础静态摘要（非 LLM 深度解读版）
  api.registerCommand({
    name: "xiao-url-basic",
    description: "Basic URL digest without chat-LLM. Usage: /xiao-url-basic <url>",
    acceptsArgs: true,
    handler: async (ctx) => {
      const args = (ctx.args || "").trim();

      // 尝试在混杂参数中解析出合法的网址 URL
      const firstUrl = extractUrls(args)[0] || "";
      const url = normalizeHttpUrl(firstUrl || args || "");
      if (!url) {
        return { text: "飞飞，给我一个完整链接吧（http/https），我来做个基础摘要。" };
      }

      // 获取页面的头部元数据（title/description）部分
      const digest = await fetchUrlBasicDigest(url);
      if (!digest) {
        return { text: "飞飞，这个链接我暂时抓取失败了，你可以稍后再发我一次。" };
      }

      // 构建展示列表，依序展示标题、简介和剪短的正文残片
      const lines: string[] = [];
      lines.push("飞飞，我先给你做个基础版摘要：");
      if (digest.title) lines.push(`- 标题：${digest.title}`);
      if (digest.description) lines.push(`- 简介：${digest.description}`);
      if (digest.preview) lines.push(`- 正文摘录：${digest.preview}`);

      // 追加原始域名及引申服务说明
      lines.push(`- 来源：${digest.domain || digest.url}`);
      lines.push("如果你要深度解读，我再继续往下拆重点。");

      return { text: lines.join("\n") };
    },
  });
}
