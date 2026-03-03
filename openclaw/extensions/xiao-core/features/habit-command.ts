import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { upsertHabit, listHabits, checkinHabit, cancelHabit } from "../state/store.js";

export function registerXiaoHabitCommand(api: OpenClawPluginApi): void {
  api.registerCommand({
    name: "xiao-habit",
    description: "Habit ops. Usage: /xiao-habit [create|checkin|list|stats|cancel]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const userKey = applyAlias(normalizeUserKey(`${ctx.channel}:${actor}`)).resolved;
      const args = (ctx.args || "").trim();
      if (!args || args === "list" || args === "stats") {
        const rows = await listHabits(userKey, true);
        if (!rows.length) return { text: "你还没有创建习惯打卡。" };
        const lines = rows.map((x) => `- ${x.name}: 连续${x.currentStreak}天，总${x.totalCheckins}次`);
        return { text: ["习惯打卡：", ...lines].join("\n") };
      }
      if (args.startsWith("create ")) {
        const body = args.slice(7).trim();
        const created = await upsertHabit(userKey, body);
        return { text: created ? `已创建习惯：${created.name}` : "创建失败" };
      }
      if (args.startsWith("checkin ")) {
        const body = args.slice(8).trim();
        const res = await checkinHabit(userKey, body);
        return { text: res.message };
      }
      if (args.startsWith("cancel ")) {
        const body = args.slice(7).trim();
        const item = await cancelHabit(userKey, body);
        return { text: item ? `已取消习惯：${item.name}` : "未找到该习惯" };
      }
      return { text: "usage: /xiao-habit [create <name>|checkin <name>|list|stats|cancel <name>]" };
    },
  });
}
