import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { getRecentNotes, addMemoryNote, retrieveRagHits } from "../state/store.js";
import { shorten } from "../../shared/text.js";

export function registerXiaoMemoryCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-memory 命令，用于提供基于 RAG 或短文本式的轻量记忆检索与录入
  api.registerCommand({
    name: "xiao-memory",
    description: "Memory ops. Usage: /xiao-memory [list|add <text>|search <query>]",
    acceptsArgs: true,
    handler: async (ctx) => {
      // 解析用户身份并转化为标准化 userKey
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const raw = `${ctx.channel}:${actor}`;
      const userKey = applyAlias(normalizeUserKey(raw)).resolved;

      const args = (ctx.args || "").trim();

      // 分支一：缺省或显式传 list，列出最近捕获或显式添加的记忆片段
      if (!args || args === "list") {
        const notes = await getRecentNotes(userKey, 10);
        if (notes.length === 0) {
          return { text: "memory is empty" };
        }

        // 展示包含来源类型的扁平记忆列表
        const lines = notes.map((n, i) => `${String(i + 1).padStart(2, "0")}. [${n.source}] ${n.text}`);
        return { text: lines.join("\n") };
      }

      // 分支二：手动强制添加一条新记忆（类型记为 "explicit" 显式要求）
      if (args.startsWith("add ")) {
        const payload = args.slice(4).trim();
        if (!payload) {
          return { text: "usage: /xiao-memory add <text>" };
        }

        await addMemoryNote(userKey, payload, "explicit");
        return { text: "memory saved" };
      }

      // 分支三：利用向量或 BM25 进行记忆检索，返回匹配度较高的条目
      if (args.startsWith("search ")) {
        const query = args.slice(7).trim();
        if (!query) {
          return { text: "usage: /xiao-memory search <query>" };
        }

        // 限制返回最多6条 hits
        const hits = await retrieveRagHits(userKey, query, 6);
        if (hits.length === 0) {
          return { text: "no memory hit" };
        }

        // 格式化输出带有匹配分值的结果
        const lines = hits.map((h, i) => `${i + 1}. (${h.from},score=${h.score}) ${shorten(h.text, 120)}`);
        return { text: lines.join("\n") };
      }

      // Fallback：提示如何使用该命令
      return { text: "usage: /xiao-memory [list|add <text>|search <query>]" };
    },
  });
}
