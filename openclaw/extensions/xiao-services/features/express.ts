import { env } from "../../shared/env.js";
import { fetchJson } from "../../shared/request.js";

const MAP: Record<string, string> = {
  "顺丰": "SF",
  "圆通": "YTO",
  "中通": "ZTO",
  "申通": "STO",
  "韵达": "YD",
  "邮政": "YZPY",
  "京东": "JD",
};

export async function trackExpress(company: string, number: string): Promise<Record<string, unknown>> {
  const key = env("KDNIAO_KEY");
  const customer = env("KDNIAO_CUSTOMER");
  if (!key || !customer) {
    return { ok: false, error: "missing_env", missing: [!key ? "KDNIAO_KEY" : null, !customer ? "KDNIAO_CUSTOMER" : null].filter(Boolean) };
  }
  if (!number.trim()) return { ok: false, error: "invalid_number" };
  const code = MAP[company] || company;
  const api = new URL("https://api.kdniao.com/Ebusiness/EbusinessOrderhandle.aspx");
  api.searchParams.set("RequestType", "1002");
  api.searchParams.set("ShipperCode", code);
  api.searchParams.set("LogisticCode", number.trim());
  const data = await fetchJson(api.toString(), undefined, 12000);
  return { ok: true, provider: "kdniao", data };
}
