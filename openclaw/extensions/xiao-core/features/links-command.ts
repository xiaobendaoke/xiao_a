import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

type LinkEvidence = {
  url: string;
  ts: number;
  source: string;
  context: string;
};

type LinksCommandDeps = {
  applyAlias: (rawUserKey: string) => { resolved: string; aliasFrom?: string };
  normalizeUserKey: (raw: string) => string;
  clamp: (n: number, lo: number, hi: number) => number;
  getRecentLinks: (userKey: string, limit: number) => Promise<LinkEvidence[]>;
  shorten: (text: string, maxLen: number) => string;
};

export function registerXiaoLinksCommand(api: OpenClawPluginApi, deps: LinksCommandDeps): void {
  api.registerCommand({
    name: "xiao-links",
    description: "Show recent link evidence. Usage: /xiao-links [limit]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const raw = `${ctx.channel}:${actor}`;
      const userKey = deps.applyAlias(deps.normalizeUserKey(raw)).resolved;
      const limitRaw = Number((ctx.args || "").trim() || 6);
      const limit = deps.clamp(Number.isFinite(limitRaw) ? limitRaw : 6, 1, 12);
      const links = await deps.getRecentLinks(userKey, limit);
      if (links.length === 0) {
        return { text: "no recent links" };
      }
      const latestFirst = links.slice().sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0));
      const lines: string[] = [];
      lines.push(`recent links (user=${userKey})`);
      for (let i = 0; i < latestFirst.length; i += 1) {
        const item = latestFirst[i];
        const at = new Date(Number(item.ts || 0)).toISOString();
        lines.push(`${i + 1}. [${item.source}] ${item.url}`);
        if (item.context) {
          lines.push(`   context: ${deps.shorten(item.context, 120)}`);
        }
        lines.push(`   at: ${at}`);
      }
      return { text: lines.join("\n") };
    },
  });
}
