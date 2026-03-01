import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

type MemoEntry = {
  id: string;
  text: string;
  tags: string[];
  ts: number;
};

type DeleteMemoResult = {
  ok: boolean;
  removed?: MemoEntry;
};

type MemoCommandDeps = {
  applyAlias: (rawUserKey: string) => { resolved: string; aliasFrom?: string };
  normalizeUserKey: (raw: string) => string;
  getRecentMemos: (userKey: string, limit: number) => Promise<MemoEntry[]>;
  addMemoEntry: (userKey: string, text: string) => Promise<MemoEntry | null>;
  searchMemos: (userKey: string, query: string, limit: number) => Promise<MemoEntry[]>;
  deleteMemoEntry: (userKey: string, selector: string) => Promise<DeleteMemoResult>;
  shorten: (text: string, maxLen: number) => string;
};

export function registerXiaoMemoCommand(api: OpenClawPluginApi, deps: MemoCommandDeps): void {
  api.registerCommand({
    name: "xiao-memo",
    description: "Memo ops. Usage: /xiao-memo [add|list|search|del]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const raw = `${ctx.channel}:${actor}`;
      const userKey = deps.applyAlias(deps.normalizeUserKey(raw)).resolved;
      const args = (ctx.args || "").trim();
      if (!args || args === "list") {
        const list = await deps.getRecentMemos(userKey, 10);
        if (list.length === 0) {
          return { text: "飞飞，你这边还没有备忘记录。" };
        }
        const lines: string[] = [];
        lines.push("飞飞，最近的备忘在这里：");
        for (let i = list.length - 1, rank = 1; i >= 0; i -= 1, rank += 1) {
          const item = list[i];
          const at = new Date(item.ts).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
          const tags = item.tags.length > 0 ? ` #${item.tags.join(" #")}` : "";
          lines.push(`${rank}. [${at}] ${deps.shorten(item.text, 120)}${tags} (id=${item.id})`);
        }
        return { text: lines.join("\n") };
      }

      if (args.startsWith("add ")) {
        const payload = args.slice(4).trim();
        if (!payload) {
          return { text: "usage: /xiao-memo add <text>" };
        }
        const saved = await deps.addMemoEntry(userKey, payload);
        if (!saved) {
          return { text: "飞飞，这条备忘保存失败了，你换个说法再发我一次。" };
        }
        return { text: `飞飞，我记下来了：${deps.shorten(saved.text, 80)} (id=${saved.id})` };
      }

      if (args.startsWith("search ")) {
        const q = args.slice(7).trim();
        if (!q) {
          return { text: "usage: /xiao-memo search <query>" };
        }
        const rows = await deps.searchMemos(userKey, q, 8);
        if (rows.length === 0) {
          return { text: "飞飞，我没搜到相关备忘。" };
        }
        const lines: string[] = [];
        lines.push(`飞飞，和「${deps.shorten(q, 24)}」相关的备忘有：`);
        rows.forEach((x, i) => {
          const at = new Date(x.ts).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
          lines.push(`${i + 1}. [${at}] ${deps.shorten(x.text, 100)} (id=${x.id})`);
        });
        return { text: lines.join("\n") };
      }

      if (args.startsWith("del ") || args.startsWith("delete ")) {
        const selector = args.replace(/^(del|delete)\s+/i, "").trim();
        if (!selector) {
          return { text: "usage: /xiao-memo del <id|index>" };
        }
        const removed = await deps.deleteMemoEntry(userKey, selector);
        if (!removed.ok || !removed.removed) {
          return { text: "飞飞，我没找到这条备忘，你可以用 list 先看下 id。" };
        }
        return { text: `飞飞，这条我删掉啦：${deps.shorten(removed.removed.text, 80)}` };
      }

      return { text: "usage: /xiao-memo [list|add <text>|search <query>|del <id|index>]" };
    },
  });
}
