import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

type ReflectionResult = {
  ok: boolean;
  saved?: boolean;
  reason?: string;
  userKey?: string;
  summary?: string;
};

type ReflectCommandDeps = {
  applyAlias: (rawUserKey: string) => { resolved: string; aliasFrom?: string };
  normalizeUserKey: (raw: string) => string;
  clamp: (n: number, lo: number, hi: number) => number;
  runDailyReflection: (params: {
    userKey: string;
    hours: number;
    minUserMessages: number;
  }) => Promise<ReflectionResult>;
  shorten: (text: string, maxLen: number) => string;
};

export function registerXiaoReflectCommand(api: OpenClawPluginApi, deps: ReflectCommandDeps): void {
  api.registerCommand({
    name: "xiao-reflect",
    description: "Generate derived reflection memory. Usage: /xiao-reflect [hours]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const raw = `${ctx.channel}:${actor}`;
      const userKey = deps.applyAlias(deps.normalizeUserKey(raw)).resolved;
      const hoursRaw = Number((ctx.args || "").trim() || 24);
      const hours = deps.clamp(Number.isFinite(hoursRaw) ? hoursRaw : 24, 1, 168);
      const result = await deps.runDailyReflection({
        userKey,
        hours,
        minUserMessages: 5,
      });
      if (!result.ok) {
        return { text: `reflection failed: ${result.reason || "unknown"}` };
      }
      if (!result.saved) {
        return { text: `reflection skipped: ${result.reason || "no_signal"}` };
      }
      return {
        text: [
          "reflection saved",
          `- user_key: ${result.userKey}`,
          `- hours: ${hours}`,
          `- summary: ${deps.shorten(result.summary || "", 180)}`,
        ].join("\n"),
      };
    },
  });
}
