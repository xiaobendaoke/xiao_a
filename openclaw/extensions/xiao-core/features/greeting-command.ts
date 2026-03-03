import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { detectGreetingType } from "../utils/intent.js";
import { getGreetingLog, recordGreeting } from "../state/store.js";

const MORNING = ["早安呀，今天也要元气满满～", "早上好，记得吃早餐。", "早呀，我在呢。"];
const NIGHT = ["晚安，早点休息。", "好梦，明天见。", "晚安呀，别熬夜。"];
const NOON = ["中午好，记得吃饭。", "午安，休息一下。"];

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)] as T;
}

export function registerXiaoGreetingCommand(api: OpenClawPluginApi): void {
  api.registerCommand({
    name: "xiao-greet",
    description: "Greeting helper. Usage: /xiao-greet <text>",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const userKey = applyAlias(normalizeUserKey(`${ctx.channel}:${actor}`)).resolved;
      const text = (ctx.args || "").trim();
      const tp = detectGreetingType(text);
      if (!tp) return { text: "没有识别到早安/午安/晚安。" };
      const log = await getGreetingLog(userKey);
      if (log?.lastType === tp && Date.now() - Number(log.lastTs || 0) < 30 * 60 * 1000) {
        return { text: tp === "night" ? "好啦好啦，晚安～" : "收到，今天也顺顺利利。" };
      }
      await recordGreeting(userKey, tp);
      if (tp === "morning") return { text: pick(MORNING) };
      if (tp === "night") return { text: pick(NIGHT) };
      return { text: pick(NOON) };
    },
  });
}
