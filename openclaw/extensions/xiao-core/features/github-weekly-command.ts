import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { clamp, shorten } from "../../shared/text.js";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import {
  fetchGithubTrendingLite,
  type GithubRepoMeta,
  type GithubTrendingLiteItem,
} from "./github-command.js";
import { fetchTextWithTimeout, stripHtmlToText } from "./url-basic-command.js";
import { ensureStateLoaded, persistState } from "../state/store.js";

export function currentIsoWeekKey(now: Date = new Date()): string {
  const d = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

export function inferGithubHotReason(item: GithubTrendingLiteItem, meta: GithubRepoMeta): string {
  const topics = (meta.topics || []).slice(0, 3).join(" / ");
  if (topics) {
    return `这周热度可能来自「${topics}」方向刚好在风口上。`;
  }
  if (item.stars) {
    return `榜单里星标增长明显（${item.stars}），说明最近关注度很集中。`;
  }
  return "它的问题定义很直接，大家一看就知道能拿来做什么，所以更容易扩散。";
}

export function inferGithubUseHint(item: GithubTrendingLiteItem, meta: GithubRepoMeta): string {
  const lang = meta.language || item.language;
  if (lang) {
    return `如果你想快速试手感，可以先按 ${lang} 环境跑一个最小 demo。`;
  }
  if ((meta.topics || []).length > 0) {
    return `适合先从 README 和示例项目下手，先跑通再按自己的场景改。`;
  }
  return "适合先看它的 README 和示例，再决定是不是要接到你自己的项目里。";
}

export async function fetchGithubRepoMeta(repo: string): Promise<GithubRepoMeta> {
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

export async function hasGithubWeeklyPushed(userKey: string, weekKey: string): Promise<boolean> {
  const normalized = normalizeUserKey(userKey);
  if (!normalized || !weekKey) return false;
  const store = await ensureStateLoaded();
  return (store.githubWeekly[normalized]?.weekKey || "") === weekKey;
}

export async function markGithubWeeklyPushed(userKey: string, weekKey: string): Promise<void> {
  const normalized = normalizeUserKey(userKey);
  if (!normalized || !weekKey) return;
  const store = await ensureStateLoaded();
  store.githubWeekly[normalized] = {
    weekKey,
    ts: Date.now(),
  };
  await persistState();
}

export function registerXiaoGithubWeeklyCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-github-weekly 命令，用于每周深度研报生成并限制频繁推送
  api.registerCommand({
    name: "xiao-github-weekly",
    description:
      "GitHub weekly deep report with dedupe/force. Usage: /xiao-github-weekly [limit] [language] [force|强制]",
    acceptsArgs: true,
    handler: async (ctx) => {
      // 解析当前上下文的用户信息并处理别名映射
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const userKey = applyAlias(normalizeUserKey(`${ctx.channel}:${actor}`)).resolved;

      // 解析用户传递的参数字符串
      const raw = (ctx.args || "").trim();
      const tokens = raw ? raw.split(/\s+/).filter(Boolean) : [];

      let force = false;
      let limit = 5;
      const langTokens: string[] = [];

      // 遍历解析命令行词元：寻找强制标志、数量上限、以及语言过滤
      for (const token of tokens) {
        const lower = token.toLowerCase();
        if (lower === "force" || token === "强制") {
          force = true; // 用户要求忽略重复推送检查，强制重新生成
          continue;
        }
        const n = Number.parseInt(token, 10);
        if (Number.isFinite(n) && String(n) === token) {
          limit = clamp(n, 1, 8); // 限制单次推送的热榜条目范围在1~8之间
          continue;
        }
        langTokens.push(token); // 剩下的内容全作为语言或附加查询条件
      }
      const language = langTokens.join(" ").trim();

      // 生成当前自然周的防重键
      const weekKey = currentIsoWeekKey();

      // 检查本周是否已经向此用户推送过，若非强刷且已发过则阻挡
      if (!force && (await hasGithubWeeklyPushed(userKey, weekKey))) {
        return {
          text: `飞飞，这周的周榜（${weekKey}）我已经发过啦。\n如果你要我重发一次，回我：github周榜 强制`,
        };
      }

      try {
        // 第一阶段抓取：从 GitHub 每日/周热榜拉取基础数据包
        const items = await fetchGithubTrendingLite({ since: "weekly", limit, language });
        if (items.length === 0) {
          return { text: "飞飞，我这次没抓到 GitHub 周榜，晚点你再叫我试一次好不好。" };
        }

        const top = items.slice(0, limit);

        // 第二阶段抓取：并行获取每个上榜仓库的详细元数据（如详细描述、topics）
        const metas = await Promise.all(top.map((x) => fetchGithubRepoMeta(x.repo)));

        const lines: string[] = [];
        lines.push(`飞飞，${weekKey} 这周的 GitHub 热榜我整理好了：`);

        // 遍历格式化每个仓库的深度研报内容
        for (let i = 0; i < top.length; i += 1) {
          const item = top[i];
          const meta = metas[i] || { description: "", topics: [], language: "" };

          // 若详细抓取包含语言则优先使用详细的语言字段，否则回退
          const lang = meta.language || item.language;
          const desc = meta.description || item.description || "仓库介绍暂时抓取不到。";

          lines.push(`${i + 1}. ${item.repo}${lang ? ` · ${lang}` : ""}${item.stars ? ` · ★${item.stars}` : ""}`);
          // 加上短截断防超限的描述摘要
          lines.push(`   它在做：${shorten(desc, 150)}`);
          // 利用 LLM/规则 推断爆火原因
          lines.push(`   这周会火：${inferGithubHotReason(item, meta)}`);
          // 预测上手指引或使用提示
          lines.push(`   怎么上手：${inferGithubUseHint(item, meta)}`);
          lines.push(`   链接：https://github.com/${item.repo}`);
        }

        if (force) {
          lines.push("这次是强制重发版，我已经重新给你整理一遍啦。");
        }

        // 确认成功获取和组织后，通过存储引擎标记本周已推送
        await markGithubWeeklyPushed(userKey, weekKey);
        return { text: lines.join("\n") };
      } catch {
        // 若任意截断抓取错误则走 fallback，不污染已推送记录
        return { text: "飞飞，GitHub 周榜整理失败了，我等会儿再给你补上。" };
      }
    },
  });
}
