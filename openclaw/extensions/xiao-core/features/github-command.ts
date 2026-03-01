import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

type GithubTrendingLiteItem = {
  repo: string;
  description: string;
  language: string;
  stars: string;
};

type GithubCommandDeps = {
  clamp: (n: number, lo: number, hi: number) => number;
  fetchGithubTrendingLite: (params: {
    since: "daily" | "weekly" | "monthly";
    limit: number;
    language?: string;
  }) => Promise<GithubTrendingLiteItem[]>;
};

export function registerXiaoGithubCommand(api: OpenClawPluginApi, deps: GithubCommandDeps): void {
  api.registerCommand({
    name: "xiao-github",
    description: "Direct GitHub trending query without chat-LLM. Usage: /xiao-github [daily|weekly|monthly] [limit] [language]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const raw = (ctx.args || "").trim();
      const tokens = raw ? raw.split(/\s+/).filter(Boolean) : [];
      const sinceToken = (tokens[0] || "weekly").toLowerCase();
      const since = (["daily", "weekly", "monthly"].includes(sinceToken) ? sinceToken : "weekly") as
        | "daily"
        | "weekly"
        | "monthly";
      const limitToken = Number(tokens[1] || 5);
      const limit = deps.clamp(Number.isFinite(limitToken) ? limitToken : 5, 1, 8);
      const language = tokens.slice(2).join(" ").trim();

      try {
        const items = await deps.fetchGithubTrendingLite({ since, limit, language });
        if (items.length === 0) {
          return { text: "飞飞，我刚刚没抓到 GitHub 热榜内容，晚点我再帮你看一眼。" };
        }
        const lines: string[] = [];
        lines.push(`飞飞，GitHub ${since} 热榜我看好了：`);
        for (const it of items.slice(0, limit)) {
          const seg: string[] = [];
          seg.push(`- ${it.repo}`);
          if (it.language) seg.push(it.language);
          if (it.stars) seg.push(`★${it.stars}`);
          lines.push(seg.join(" · "));
          if (it.description) {
            lines.push(`  功能看点：${it.description}`);
          } else {
            lines.push("  功能看点：仓库简介暂缺，点仓库名我再展开。");
          }
        }
        lines.push("你点一个仓库名，我再给你展开讲看点。");
        return { text: lines.join("\n") };
      } catch {
        return { text: "飞飞，GitHub 热榜抓取暂时失败了，我稍后再试一次给你。" };
      }
    },
  });
}
