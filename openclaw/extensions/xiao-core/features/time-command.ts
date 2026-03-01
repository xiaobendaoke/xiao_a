import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

export function registerXiaoTimeCommand(api: OpenClawPluginApi): void {
  api.registerCommand({
    name: "xiao-time",
    description: "Quick local time reply without chat-LLM. Usage: /xiao-time",
    acceptsArgs: false,
    handler: async () => {
      const now = new Date();
      const localText = now.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
      const hourToken = new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit",
        hour12: false,
        timeZone: "Asia/Shanghai",
      }).format(now);
      const hour = Number.parseInt(hourToken, 10);
      const period =
        hour < 6 ? "凌晨" : hour < 11 ? "上午" : hour < 13 ? "中午" : hour < 18 ? "下午" : "晚上";
      return {
        text: `飞飞，现在是 ${localText}（${period}）。`,
      };
    },
  });
}
