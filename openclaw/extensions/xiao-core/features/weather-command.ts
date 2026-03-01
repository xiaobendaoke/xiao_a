import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

type WeatherCommandDeps = {
  inferCityFromInput: (text: string) => string;
  shorten: (text: string, maxLen: number) => string;
  fetchWeatherSummary: (city: string) => Promise<string>;
};

export function registerXiaoWeatherCommand(api: OpenClawPluginApi, deps: WeatherCommandDeps): void {
  api.registerCommand({
    name: "xiao-weather",
    description: "Direct weather query without chat-LLM. Usage: /xiao-weather <city>",
    acceptsArgs: true,
    handler: async (ctx) => {
      const args = (ctx.args || "").trim();
      const city = deps.inferCityFromInput(args) || deps.shorten(args, 20).trim();
      if (!city) {
        return { text: "飞飞，告诉我想查哪个城市呀～例如：/xiao-weather 绵阳" };
      }
      const summary = await deps.fetchWeatherSummary(city);
      if (!summary) {
        return { text: `飞飞，我这会儿没拉到 ${city} 的天气数据，稍后我再帮你查一次。` };
      }
      return {
        text: [
          `飞飞，我查到 ${city} 啦：${summary}`,
          "要不要我再按这个天气帮你给个出门建议？",
        ].join("\n"),
      };
    },
  });
}
