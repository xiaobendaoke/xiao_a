import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { getRecentMemos, addMemoEntry, searchMemos, deleteMemoEntry } from "../state/store.js";
import { shorten } from "../../shared/text.js";

export function registerXiaoMemoCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-memo 命令，用于管理个人的短文本备忘录
  api.registerCommand({
    name: "xiao-memo",
    description: "Memo ops. Usage: /xiao-memo [add|list|search|del]",
    acceptsArgs: true,
    handler: async (ctx) => {
      // 解析当前上下文的用户信息并处理别名映射
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const raw = `${ctx.channel}:${actor}`;
      const userKey = applyAlias(normalizeUserKey(raw)).resolved;

      const args = (ctx.args || "").trim();

      // 分支一：缺省参数或指定 list 时，列出最近的备忘
      if (!args || args === "list") {
        const list = await getRecentMemos(userKey, 10);
        if (list.length === 0) {
          return { text: "飞飞，你这边还没有备忘记录。" };
        }

        const lines: string[] = [];
        lines.push("飞飞，最近的备忘在这里：");
        // 倒序遍历（把最新的放上面还是按某种顺序，取决于设计，这里是从后往前输出列表，但序号递增）
        let rank = 1;
        for (let i = list.length - 1; i >= 0; i -= 1) {
          const item = list[i];
          if (!item) continue;
          const at = new Date(item.ts).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
          const tags = item.tags.length > 0 ? ` #${item.tags.join(" #")}` : "";
          lines.push(`${rank}. [${at}] ${shorten(item.text, 120)}${tags} (id=${item.id})`);
          rank++;
        }
        return { text: lines.join("\n") };
      }

      // 分支二：添加新备忘记录
      if (args.startsWith("add ")) {
        const payload = args.slice(4).trim();
        if (!payload) {
          return { text: "usage: /xiao-memo add <text>" };
        }

        // 调用依赖接口尝试保存到存储介质
        const saved = await addMemoEntry(userKey, payload);
        if (!saved) {
          return { text: "飞飞，这条备忘保存失败了，你换个说法再发我一次。" };
        }
        return { text: `飞飞，我记下来了：${shorten(saved.text, 80)} (id=${saved.id})` };
      }

      // 分支三：搜索已有备忘
      if (args.startsWith("search ")) {
        const q = args.slice(7).trim();
        if (!q) {
          return { text: "usage: /xiao-memo search <query>" };
        }

        // 根据关键字获取相关联的备忘条目
        const rows = await searchMemos(userKey, q, 8);
        if (rows.length === 0) {
          return { text: "飞飞，我没搜到相关备忘。" };
        }

        const lines: string[] = [];
        lines.push(`飞飞，和「${shorten(q, 24)}」相关的备忘有：`);
        rows.forEach((x, i) => {
          const at = new Date(x.ts).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
          lines.push(`${i + 1}. [${at}] ${shorten(x.text, 100)} (id=${x.id})`);
        });
        return { text: lines.join("\n") };
      }

      // 分支四：删除指定的备忘
      if (args.startsWith("del ") || args.startsWith("delete ")) {
        const selector = args.replace(/^(del|delete)\s+/i, "").trim();
        if (!selector) {
          return { text: "usage: /xiao-memo del <id|index>" };
        }

        // 按照传入的 ID 或序号进行删除
        const removed = await deleteMemoEntry(userKey, selector);
        if (!removed.ok || !removed.removed) {
          return { text: "飞飞，我没找到这条备忘，你可以用 list 先看下 id。" };
        }
        return { text: `飞飞，这条我删掉啦：${shorten(removed.removed.text, 80)}` };
      }

      // Fallback：走到这代表指令不在支持的范围内
      return { text: "usage: /xiao-memo [list|add <text>|search <query>|del <id|index>]" };
    },
  });
}
