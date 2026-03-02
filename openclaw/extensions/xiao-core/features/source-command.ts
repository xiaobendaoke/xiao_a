import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { clamp } from "../../shared/text.js";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { getRecentLinks, type LinkEvidence } from "../state/store.js";

export function registerXiaoSourceCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-source 命令，用于获取与用户上下文在近期捕获的相关链接源
  api.registerCommand({
    name: "xiao-source",
    description: "Directly return recent link sources without chat-LLM. Usage: /xiao-source [limit]",
    acceptsArgs: true,
    handler: async (ctx) => {
      // 用户身份映射与转换
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const userKey = applyAlias(normalizeUserKey(`${ctx.channel}:${actor}`)).resolved;

      // 提取返回结果最大数量
      const limitRaw = Number((ctx.args || "").trim() || 5);
      const limit = clamp(Number.isFinite(limitRaw) ? limitRaw : 5, 1, 10);

      // 取出用户的近期链接记录
      const links = await getRecentLinks(userKey, limit);
      if (links.length === 0) {
        return { text: "飞飞，我这边还没有可引用的来源链接记录，你再发我一次原链接吧。" };
      }

      // 按时间戳倒排以便先看最新出现的引用
      const latestFirst = links.slice().sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0));

      // 构造无大段摘要版本的纯纯链接列表展示
      const lines: string[] = [];
      lines.push("飞飞，最近我引用过的来源在这里：");
      for (let i = 0; i < latestFirst.length; i += 1) {
        lines.push(`${i + 1}. ${latestFirst[i]?.url || ""}`);
      }
      return { text: lines.join("\n") };
    },
  });
}
