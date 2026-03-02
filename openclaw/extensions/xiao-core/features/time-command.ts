import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

export function registerXiaoTimeCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-time 命令，直接返回当前主机所在地的标准时间供群内参考
  api.registerCommand({
    name: "xiao-time",
    description: "Quick local time reply without chat-LLM. Usage: /xiao-time",
    acceptsArgs: false,
    handler: async () => {
      const now = new Date();

      // 格式化为直观的东八区本地时间字符串
      const localText = now.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });

      // 提取独立的小时部分用于判断早中晚时段
      const hourToken = new Intl.DateTimeFormat("zh-CN", {
        hour: "2-digit",
        hour12: false,
        timeZone: "Asia/Shanghai",
      }).format(now);
      const hour = Number.parseInt(hourToken, 10);

      // 划分时间段标识
      const period =
        hour < 6 ? "凌晨" : hour < 11 ? "上午" : hour < 13 ? "中午" : hour < 18 ? "下午" : "晚上";

      // 返回亲切带问候语格式的时间文本
      return {
        text: `飞飞，现在是 ${localText}（${period}）。`,
      };
    },
  });
}
