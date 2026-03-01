import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

type LinkEvidence = {
  url: string;
  ts: number;
};

type SourceCommandDeps = {
  applyAlias: (rawUserKey: string) => { resolved: string; aliasFrom?: string };
  normalizeUserKey: (raw: string) => string;
  clamp: (n: number, lo: number, hi: number) => number;
  getRecentLinks: (userKey: string, limit: number) => Promise<LinkEvidence[]>;
};

export function registerXiaoSourceCommand(api: OpenClawPluginApi, deps: SourceCommandDeps): void {
  api.registerCommand({
    name: "xiao-source",
    description: "Directly return recent link sources without chat-LLM. Usage: /xiao-source [limit]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const userKey = deps.applyAlias(deps.normalizeUserKey(`${ctx.channel}:${actor}`)).resolved;
      const limitRaw = Number((ctx.args || "").trim() || 5);
      const limit = deps.clamp(Number.isFinite(limitRaw) ? limitRaw : 5, 1, 10);
      const links = await deps.getRecentLinks(userKey, limit);
      if (links.length === 0) {
        return { text: "飞飞，我这边还没有可引用的来源链接记录，你再发我一次原链接吧。" };
      }
      const latestFirst = links.slice().sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0));
      const lines: string[] = [];
      lines.push("飞飞，最近我引用过的来源在这里：");
      for (let i = 0; i < latestFirst.length; i += 1) {
        lines.push(`${i + 1}. ${latestFirst[i]?.url || ""}`);
      }
      return { text: lines.join("\n") };
    },
  });
}
