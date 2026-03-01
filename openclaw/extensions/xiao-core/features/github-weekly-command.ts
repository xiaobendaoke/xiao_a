import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

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

type GithubWeeklyDeps = {
  applyAlias: (rawUserKey: string) => { resolved: string; aliasFrom?: string };
  normalizeUserKey: (raw: string) => string;
  clamp: (n: number, lo: number, hi: number) => number;
  shorten: (text: string, maxLen: number) => string;
  currentIsoWeekKey: () => string;
  hasGithubWeeklyPushed: (userKey: string, weekKey: string) => Promise<boolean>;
  markGithubWeeklyPushed: (userKey: string, weekKey: string) => Promise<void>;
  fetchGithubTrendingLite: (params: {
    since: "daily" | "weekly" | "monthly";
    limit: number;
    language?: string;
  }) => Promise<GithubTrendingLiteItem[]>;
  fetchGithubRepoMeta: (repo: string) => Promise<GithubRepoMeta>;
  inferGithubHotReason: (item: GithubTrendingLiteItem, meta: GithubRepoMeta) => string;
  inferGithubUseHint: (item: GithubTrendingLiteItem, meta: GithubRepoMeta) => string;
};

export function registerXiaoGithubWeeklyCommand(api: OpenClawPluginApi, deps: GithubWeeklyDeps): void {
  api.registerCommand({
    name: "xiao-github-weekly",
    description:
      "GitHub weekly deep report with dedupe/force. Usage: /xiao-github-weekly [limit] [language] [force|强制]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const userKey = deps.applyAlias(deps.normalizeUserKey(`${ctx.channel}:${actor}`)).resolved;

      const raw = (ctx.args || "").trim();
      const tokens = raw ? raw.split(/\s+/).filter(Boolean) : [];
      let force = false;
      let limit = 5;
      const langTokens: string[] = [];
      for (const token of tokens) {
        const lower = token.toLowerCase();
        if (lower === "force" || token === "强制") {
          force = true;
          continue;
        }
        const n = Number.parseInt(token, 10);
        if (Number.isFinite(n) && String(n) === token) {
          limit = deps.clamp(n, 1, 8);
          continue;
        }
        langTokens.push(token);
      }
      const language = langTokens.join(" ").trim();
      const weekKey = deps.currentIsoWeekKey();
      if (!force && (await deps.hasGithubWeeklyPushed(userKey, weekKey))) {
        return {
          text: `飞飞，这周的周榜（${weekKey}）我已经发过啦。\n如果你要我重发一次，回我：github周榜 强制`,
        };
      }

      try {
        const items = await deps.fetchGithubTrendingLite({ since: "weekly", limit, language });
        if (items.length === 0) {
          return { text: "飞飞，我这次没抓到 GitHub 周榜，晚点你再叫我试一次好不好。" };
        }

        const top = items.slice(0, limit);
        const metas = await Promise.all(top.map((x) => deps.fetchGithubRepoMeta(x.repo)));
        const lines: string[] = [];
        lines.push(`飞飞，${weekKey} 这周的 GitHub 热榜我整理好了：`);
        for (let i = 0; i < top.length; i += 1) {
          const item = top[i];
          const meta = metas[i] || { description: "", topics: [], language: "" };
          const lang = meta.language || item.language;
          const desc = meta.description || item.description || "仓库介绍暂时抓取不到。";
          lines.push(`${i + 1}. ${item.repo}${lang ? ` · ${lang}` : ""}${item.stars ? ` · ★${item.stars}` : ""}`);
          lines.push(`   它在做：${deps.shorten(desc, 150)}`);
          lines.push(`   这周会火：${deps.inferGithubHotReason(item, meta)}`);
          lines.push(`   怎么上手：${deps.inferGithubUseHint(item, meta)}`);
          lines.push(`   链接：https://github.com/${item.repo}`);
        }

        if (force) {
          lines.push("这次是强制重发版，我已经重新给你整理一遍啦。");
        }
        await deps.markGithubWeeklyPushed(userKey, weekKey);
        return { text: lines.join("\n") };
      } catch {
        return { text: "飞飞，GitHub 周榜整理失败了，我等会儿再给你补上。" };
      }
    },
  });
}
