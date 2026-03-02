import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { clamp, shorten } from "../../shared/text.js";
import { fetchTextWithTimeout, stripHtmlToText } from "./url-basic-command.js";

export type GithubTrendingLiteItem = {
  repo: string;
  description: string;
  language: string;
  stars: string;
};

export type GithubRepoMeta = {
  description: string;
  topics: string[];
  language: string;
};

export async function fetchGithubTrendingLite(params: {
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

export function registerXiaoGithubCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-github 命令，用于不经过 LLM 直接拉取 GitHub 热榜内容
  api.registerCommand({
    name: "xiao-github",
    description: "Direct GitHub trending query without chat-LLM. Usage: /xiao-github [daily|weekly|monthly] [limit] [language]",
    acceptsArgs: true,
    handler: async (ctx) => {
      // 解析用户传递的参数
      const raw = (ctx.args || "").trim();
      const tokens = raw ? raw.split(/\s+/).filter(Boolean) : [];

      // 提取时间跨度参数（默认为 weekly）
      const sinceToken = (tokens[0] || "weekly").toLowerCase();
      const since = (["daily", "weekly", "monthly"].includes(sinceToken) ? sinceToken : "weekly") as
        | "daily"
        | "weekly"
        | "monthly";

      // 提取想要查看的数量限制（默认 5 个，最大限制为 8）
      const limitToken = Number(tokens[1] || 5);
      const limit = clamp(Number.isFinite(limitToken) ? limitToken : 5, 1, 8);

      // 提取语言过滤条件，将剩余词元拼接
      const language = tokens.slice(2).join(" ").trim();

      try {
        // 调用依赖接口并行抓取 GitHub 简单版热榜数据
        const items = await fetchGithubTrendingLite({ since, limit, language });

        // 若没有抓取到，直接走 fallback 回复策略
        if (items.length === 0) {
          return { text: "飞飞，我刚刚没抓到 GitHub 热榜内容，晚点我再帮你看一眼。" };
        }

        // 构建要输出的富文本文本行
        const lines: string[] = [];
        lines.push(`飞飞，GitHub ${since} 热榜我看好了：`);

        // 只截取最大要求数量的热榜进行格式化拼接
        for (const it of items.slice(0, limit)) {
          const seg: string[] = [];
          seg.push(`- ${it.repo}`);
          if (it.language) seg.push(it.language);
          if (it.stars) seg.push(`★${it.stars}`);
          lines.push(seg.join(" · "));

          // 如果有描述就加上描述，没有就占位提示
          if (it.description) {
            lines.push(`  功能看点：${it.description}`);
          } else {
            lines.push("  功能看点：仓库简介暂缺，点仓库名我再展开。");
          }
        }

        lines.push("你点一个仓库名，我再给你展开讲看点。");
        return { text: lines.join("\n") };
      } catch {
        // 全局捕获接口抛出的异常防止命令崩溃
        return { text: "飞飞，GitHub 热榜抓取暂时失败了，我稍后再试一次给你。" };
      }
    },
  });
}
