import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

type StockCommandDeps = {
  inferStockSymbol: (text: string) => string;
  fetchStockSummary: (symbol: string) => Promise<string>;
};

export function registerXiaoStockCommand(api: OpenClawPluginApi, deps: StockCommandDeps): void {
  api.registerCommand({
    name: "xiao-stock",
    description: "Direct stock quote query without chat-LLM. Usage: /xiao-stock <symbol>",
    acceptsArgs: true,
    handler: async (ctx) => {
      const args = (ctx.args || "").trim().toUpperCase();
      const symbol = deps.inferStockSymbol(args || "");
      if (!symbol) {
        return { text: "飞飞，给我一个 6 位股票代码吧～例如：/xiao-stock 600519" };
      }
      const summary = await deps.fetchStockSummary(symbol);
      if (!summary) {
        return { text: `飞飞，我暂时没拉到 ${symbol} 的行情，等会儿我再试试。` };
      }
      return {
        text: [
          `飞飞，最新行情在这：${summary}`,
          "这条是信息播报，不构成投资建议哦。",
        ].join("\n"),
      };
    },
  });
}
