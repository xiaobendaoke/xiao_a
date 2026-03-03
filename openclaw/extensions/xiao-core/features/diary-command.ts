import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { addDiaryEntry, getDiaryEntries } from "../state/store.js";
import { clamp } from "../../shared/text.js";

export function registerXiaoDiaryCommand(api: OpenClawPluginApi): void {
  api.registerCommand({
    name: "xiao-diary",
    description: "Diary ops. Usage: /xiao-diary [add <mood> <note>|today|weekly]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const userKey = applyAlias(normalizeUserKey(`${ctx.channel}:${actor}`)).resolved;
      const args = (ctx.args || "").trim();
      if (!args || args === "today") {
        const rows = await getDiaryEntries(userKey, 1);
        if (!rows.length) return { text: "今天还没有心情记录。" };
        const d = rows[rows.length - 1];
        return { text: `今天心情：${d.label}(${d.mood})\n备注：${d.note || "(无)"}` };
      }
      if (args === "weekly") {
        const rows = await getDiaryEntries(userKey, 7);
        if (rows.length < 3) return { text: "最近记录太少，至少3条再看周报。" };
        const avg = rows.reduce((s, x) => s + x.mood, 0) / rows.length;
        const best = rows.slice().sort((a, b) => b.mood - a.mood)[0];
        const low = rows.slice().sort((a, b) => a.mood - b.mood)[0];
        return {
          text: [
            "本周心情周报",
            `平均：${avg.toFixed(1)}`,
            `最高：${best.date} ${best.label}(${best.mood})`,
            `最低：${low.date} ${low.label}(${low.mood})`,
            `记录天数：${rows.length}/7`,
          ].join("\n"),
        };
      }
      if (args.startsWith("add ")) {
        const body = args.slice(4).trim();
        const [moodRaw, ...noteParts] = body.split(/\s+/);
        const mood = clamp(Number(moodRaw), -100, 100);
        if (!Number.isFinite(mood)) return { text: "usage: /xiao-diary add <mood -100~100> <note>" };
        const note = noteParts.join(" ").trim();
        const d = await addDiaryEntry(userKey, mood, note);
        return { text: d ? `记录完成：${d.label}(${d.mood})` : "记录失败" };
      }
      return { text: "usage: /xiao-diary [add <mood> <note>|today|weekly]" };
    },
  });
}
