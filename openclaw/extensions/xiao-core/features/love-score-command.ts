import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { getLoveScore } from "../state/store.js";

export function registerXiaoLoveScoreCommand(api: OpenClawPluginApi): void {
  api.registerCommand({
    name: "xiao-love-score",
    description: "Show relationship score.",
    acceptsArgs: false,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const userKey = applyAlias(normalizeUserKey(`${ctx.channel}:${actor}`)).resolved;
      const score = await getLoveScore(userKey);
      return {
        text: [
          `恋爱指数：${score.score}/100（${score.level}）`,
          `互动频率：${score.details["互动频率"]}`,
          `情绪状态：${score.details["情绪状态"]}`,
          `记忆浓度：${score.details["记忆浓度"]}`,
          `主动互动：${score.details["主动互动"]}`,
        ].join("\n"),
      };
    },
  });
}
