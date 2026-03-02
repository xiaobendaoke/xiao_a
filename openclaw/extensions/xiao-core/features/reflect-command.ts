import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { clamp, shorten } from "../../shared/text.js";
import { runDailyReflection } from "../state/store.js";

export function registerXiaoReflectCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-reflect 命令，用于基于历史记录生成总结或反思并固化为记忆片段
  api.registerCommand({
    name: "xiao-reflect",
    description: "Generate derived reflection memory. Usage: /xiao-reflect [hours]",
    acceptsArgs: true,
    handler: async (ctx) => {
      // 解析当前上下文的用户信息并映射为标准化全量名称
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const raw = `${ctx.channel}:${actor}`;
      const userKey = applyAlias(normalizeUserKey(raw)).resolved;

      // 获取用户指定的回溯时长，默认回溯最近的 24 小时数据
      const hoursRaw = Number((ctx.args || "").trim() || 24);
      // 将小时数限制在 1 小时至 一周（168小时）之间
      const hours = clamp(Number.isFinite(hoursRaw) ? hoursRaw : 24, 1, 168);

      // 调用依赖引擎中的反思生成器
      const result = await runDailyReflection({
        userKey,
        hours,
        minUserMessages: 5, // 设定阈值：起码要有 5 条对话才能总结
      });

      // 若生成失败，提示错误
      if (!result.ok) {
        return { text: `reflection failed: ${result.reason || "unknown"}` };
      }

      // 若未达到总结要求所以被跳过
      if (!result.saved) {
        return { text: `reflection skipped: ${result.reason || "no_signal"}` };
      }

      // 返回反思成功的回执文本以及简短版结论
      return {
        text: [
          "reflection saved",
          `- user_key: ${result.userKey}`,
          `- hours: ${hours}`,
          `- summary: ${shorten(result.summary || "", 180)}`,
        ].join("\n"),
      };
    },
  });
}
