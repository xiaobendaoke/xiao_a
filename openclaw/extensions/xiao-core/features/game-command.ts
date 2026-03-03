import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { getGameSession, setGameSession } from "../state/store.js";

const RIDDLES: Array<[string, string]> = [
  ["什么东西早上四条腿，中午两条腿，晚上三条腿？", "人"],
  ["什么球不能拍？", "铅球"],
  ["什么路不能走？", "套路"],
];

const LOVE = ["你是我的小星星", "我想和你一起起床", "你是我的唯一"];

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)] as T;
}

export function registerXiaoGameCommand(api: OpenClawPluginApi): void {
  api.registerCommand({
    name: "xiao-game",
    description: "Game ops. Usage: /xiao-game [start <riddle|love|truth>|next|stop|answer <text>]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const userKey = applyAlias(normalizeUserKey(`${ctx.channel}:${actor}`)).resolved;
      const args = (ctx.args || "").trim();
      if (!args) return { text: "usage: /xiao-game [start <riddle|love|truth>|next|stop|answer <text>]" };

      if (args.startsWith("start ")) {
        const g = args.slice(6).trim();
        if (g === "riddle") {
          const [q, a] = pick(RIDDLES);
          await setGameSession(userKey, { gameType: "riddle", status: "playing", round: 1, score: 0, data: { answer: a, question: q }, updatedTs: Date.now() });
          return { text: `来猜谜语：${q}` };
        }
        if (g === "love") {
          await setGameSession(userKey, { gameType: "love_words", status: "playing", round: 1, score: 0, data: {}, updatedTs: Date.now() });
          return { text: `情话接龙开始：${pick(LOVE)}，该你啦。` };
        }
        await setGameSession(userKey, { gameType: "truth_dare", status: "playing", round: 1, score: 0, data: {}, updatedTs: Date.now() });
        return { text: "真心话大冒险开始：选真心话还是大冒险？" };
      }

      if (args === "stop") {
        await setGameSession(userKey, null);
        return { text: "游戏结束。" };
      }

      const sess = await getGameSession(userKey);
      if (!sess) return { text: "你还没有开始游戏。" };

      if (args === "next") {
        if (sess.gameType === "riddle") {
          const [q, a] = pick(RIDDLES);
          await setGameSession(userKey, { ...sess, round: sess.round + 1, data: { question: q, answer: a }, updatedTs: Date.now() });
          return { text: `下一题：${q}` };
        }
        return { text: "继续：轮到你了。" };
      }

      if (args.startsWith("answer ")) {
        const ans = args.slice(7).trim();
        if (sess.gameType !== "riddle") return { text: "当前不是猜谜模式。" };
        const ok = ans === (sess.data.answer || "");
        await setGameSession(userKey, { ...sess, score: ok ? sess.score + 1 : sess.score, updatedTs: Date.now() });
        return { text: ok ? "答对啦！" : "不对，再想想。" };
      }

      return { text: "usage: /xiao-game [start <riddle|love|truth>|next|stop|answer <text>]" };
    },
  });
}
