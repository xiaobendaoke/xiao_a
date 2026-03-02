import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { fetchJson } from "../../shared/request.js";
import { shorten } from "../../shared/text.js";

export function inferStockSymbol(input: string): string | null {
  const text = (input || "").toUpperCase().trim();
  if (!text) {
    return null;
  }

  const m1 = text.match(/\b(SH|SZ)\d{6}\b/);
  if (m1?.[0]) {
    return m1[0];
  }

  const m2 = text.match(/\b\d{6}\.(SH|SZ)\b/);
  if (m2?.[0]) {
    const [code, market] = m2[0].split(".");
    return `${market}${code}`;
  }

  const m3 = text.match(/\b\d{6}\b/);
  if (!m3?.[0]) {
    return null;
  }

  const code = m3[0];
  if (/^[6895]/.test(code)) {
    return `SH${code}`;
  }
  return `SZ${code}`;
}

export function resolveEastmoneySecid(symbol: string): string | null {
  const m = (symbol || "").trim().toUpperCase().match(/^(SH|SZ)(\d{6})$/);
  if (!m) {
    return null;
  }
  const market = m[1];
  const code = m[2];
  return `${market === "SH" ? "1" : "0"}.${code}`;
}

export function asNum(v: unknown): number | null {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export function priceFromCent(v: unknown): string {
  const n = asNum(v);
  if (n === null) return "-";
  return (n / 100).toFixed(2);
}

export async function fetchStockSummary(symbol: string): Promise<string | null> {
  try {
    const secid = resolveEastmoneySecid(symbol);
    if (!secid) {
      return null;
    }

    const url = new URL("https://push2.eastmoney.com/api/qt/stock/get");
    url.searchParams.set("secid", secid);
    url.searchParams.set("fields", "f57,f58,f43,f44,f45,f46,f47,f48,f170,f169,f60");

    const resp = (await fetchJson(url.toString(), undefined, 9000)) as {
      data?: Record<string, unknown>;
    };
    const d = resp.data;
    if (!d) {
      return null;
    }

    const name = String(d.f58 || symbol);
    const code = String(d.f57 || symbol.slice(2));
    const price = priceFromCent(d.f43);
    const high = priceFromCent(d.f44);
    const low = priceFromCent(d.f45);
    const open = priceFromCent(d.f46);
    const pctRaw = asNum(d.f170);
    const chgRaw = asNum(d.f169);
    const pct = pctRaw === null ? "-" : `${(pctRaw / 100).toFixed(2)}%`;
    const chg = chgRaw === null ? "-" : (chgRaw / 100).toFixed(2);

    const pieces: string[] = [];
    pieces.push(`标的=${name}(${code})`);
    pieces.push(`现价=${price}`);
    pieces.push(`涨跌=${chg} (${pct})`);
    pieces.push(`开盘=${open}`);
    pieces.push(`最高/最低=${high}/${low}`);
    return pieces.join("；");
  } catch {
    return null;
  }
}

export function registerXiaoStockCommand(api: OpenClawPluginApi): void {
  // 注册 /xiao-stock 命令，用于获取基础版的股票行市摘要
  api.registerCommand({
    name: "xiao-stock",
    description: "Direct stock quote query without chat-LLM. Usage: /xiao-stock <symbol>",
    acceptsArgs: true,
    handler: async (ctx) => {
      // 提取输入参数并统一转换为大写，方便后续规则匹配
      const args = (ctx.args || "").trim().toUpperCase();

      // 尝试推断出标准化的股票代码（例如补齐市场前缀 sz/sh 等）
      const symbol = inferStockSymbol(args || "");
      if (!symbol) {
        return { text: "飞飞，给我一个 6 位股票代码吧～例如：/xiao-stock 600519" };
      }

      // 调用依赖接口拉取该股票的实时行情摘要文本
      const summary = await fetchStockSummary(symbol);
      if (!summary) {
        return { text: `飞飞，我暂时没拉到 ${symbol} 的行情，等会儿我再试试。` };
      }

      // 构建回复并附加免责声明
      return {
        text: [
          `飞飞，最新行情在这：${summary}`,
          "这条是信息播报，不构成投资建议哦。",
        ].join("\n"),
      };
    },
  });
}
