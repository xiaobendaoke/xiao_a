import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

type UrlBasicDigest = {
  url: string;
  domain: string;
  title: string;
  description: string;
  preview: string;
};

type UrlBasicCommandDeps = {
  extractUrls: (text: string) => string[];
  normalizeHttpUrl: (url: string) => string;
  fetchUrlBasicDigest: (url: string) => Promise<UrlBasicDigest | null>;
};

export function registerXiaoUrlBasicCommand(api: OpenClawPluginApi, deps: UrlBasicCommandDeps): void {
  api.registerCommand({
    name: "xiao-url-basic",
    description: "Basic URL digest without chat-LLM. Usage: /xiao-url-basic <url>",
    acceptsArgs: true,
    handler: async (ctx) => {
      const args = (ctx.args || "").trim();
      const firstUrl = deps.extractUrls(args)[0] || "";
      const url = deps.normalizeHttpUrl(firstUrl || args || "");
      if (!url) {
        return { text: "飞飞，给我一个完整链接吧（http/https），我来做个基础摘要。" };
      }
      const digest = await deps.fetchUrlBasicDigest(url);
      if (!digest) {
        return { text: "飞飞，这个链接我暂时抓取失败了，你可以稍后再发我一次。" };
      }
      const lines: string[] = [];
      lines.push("飞飞，我先给你做个基础版摘要：");
      if (digest.title) lines.push(`- 标题：${digest.title}`);
      if (digest.description) lines.push(`- 简介：${digest.description}`);
      if (digest.preview) lines.push(`- 正文摘录：${digest.preview}`);
      lines.push(`- 来源：${digest.domain || digest.url}`);
      lines.push("如果你要深度解读，我再继续往下拆重点。");
      return { text: lines.join("\n") };
    },
  });
}
