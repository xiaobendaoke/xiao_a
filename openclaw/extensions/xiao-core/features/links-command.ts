import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { clamp, shorten } from "../../shared/text.js";
import { getRecentLinks } from "../state/store.js";

export function registerXiaoLinksCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-links 命令，用于获取用户最近发送或接收到的链接痕迹
  api.registerCommand({
    name: "xiao-links",
    description: "Show recent link evidence. Usage: /xiao-links [limit]",
    acceptsArgs: true,
    handler: async (ctx) => {
      // 解析用户身份并转化为标准化 userKey
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const raw = `${ctx.channel}:${actor}`;
      const userKey = applyAlias(normalizeUserKey(raw)).resolved;

      // 解析分页数量限制参数，默认为 6 个，并且被限制在 1 到 12 之间防刷屏
      const limitRaw = Number((ctx.args || "").trim() || 6);
      const limit = clamp(Number.isFinite(limitRaw) ? limitRaw : 6, 1, 12);

      // 获取关联当前用户的历史链接记录
      const links = await getRecentLinks(userKey, limit);
      if (links.length === 0) {
        return { text: "no recent links" };
      }

      // 按照时间戳倒序排序，保证最新的记录展示在最前面
      const latestFirst = links.slice().sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0));

      // 构建展示的富文本列表
      const lines: string[] = [];
      lines.push(`recent links (user=${userKey})`);
      for (let i = 0; i < latestFirst.length; i += 1) {
        const item = latestFirst[i];

        // 格式化 ISO 字符串时间
        const at = new Date(Number(item.ts || 0)).toISOString();

        // 展示来源（是用户发来还是助手生成的）及对应链接
        lines.push(`${i + 1}. [${item.source}] ${item.url}`);

        // 若有捕获的文字上下文则一并追加显示（截断超长内容）
        if (item.context) {
          lines.push(`   context: ${shorten(item.context, 120)}`);
        }
        lines.push(`   at: ${at}`);
      }
      return { text: lines.join("\n") };
    },
  });
}
