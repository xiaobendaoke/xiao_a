import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { addPlanEntry, listPlanEntries, updatePlanStatus } from "../state/store.js";

export function registerXiaoPlanCommand(api: OpenClawPluginApi): void {
  api.registerCommand({
    name: "xiao-plan",
    description: "Plan ops. Usage: /xiao-plan [add|list|done|cancel]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const userKey = applyAlias(normalizeUserKey(`${ctx.channel}:${actor}`)).resolved;
      const args = (ctx.args || "").trim();
      if (!args || args === "list") {
        const rows = await listPlanEntries(userKey, "pending");
        if (!rows.length) return { text: "暂无待办约定。" };
        return {
          text: ["待办约定：", ...rows.slice(0, 10).map((x, i) => `${i + 1}. ${x.content}${x.when ? `（${x.when}）` : ""} [id=${x.id}]`)].join("\n"),
        };
      }
      if (args.startsWith("add ")) {
        const body = args.slice(4).trim();
        const entry = await addPlanEntry(userKey, body);
        if (!entry) return { text: "添加失败，请重试。" };
        return { text: `已添加约定：${entry.content} (id=${entry.id})` };
      }
      if (args.startsWith("done ")) {
        const sel = args.slice(5).trim();
        const item = await updatePlanStatus(userKey, sel, "done");
        return { text: item ? `已完成：${item.content}` : "未找到该约定" };
      }
      if (args.startsWith("cancel ")) {
        const sel = args.slice(7).trim();
        const item = await updatePlanStatus(userKey, sel, "cancelled");
        return { text: item ? `已取消：${item.content}` : "未找到该约定" };
      }
      return { text: "usage: /xiao-plan [add <text>|list|done <id|index>|cancel <id|index>]" };
    },
  });
}
