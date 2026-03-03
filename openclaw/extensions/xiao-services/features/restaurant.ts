import { env } from "../../shared/env.js";
import { fetchJson } from "../../shared/request.js";

export async function searchRestaurants(city: string, keyword: string, limit: number = 5): Promise<Record<string, unknown>> {
  const key = env("AMAP_KEY");
  if (!key) return { ok: false, error: "missing_env", missing: ["AMAP_KEY"] };
  const url = new URL("https://restapi.amap.com/v3/place/text");
  url.searchParams.set("key", key);
  url.searchParams.set("keywords", keyword || "美食");
  url.searchParams.set("city", city || "");
  url.searchParams.set("offset", String(limit));
  url.searchParams.set("types", "餐饮服务");
  const data = (await fetchJson(url.toString(), undefined, 12000)) as { status?: string; pois?: Array<Record<string, unknown>> };
  if (data.status !== "1") return { ok: false, error: "amap_failed" };
  const items = (data.pois || []).slice(0, limit).map((x) => ({
    name: String(x.name || ""),
    address: String(x.address || ""),
    type: String(x.type || ""),
    location: String(x.location || ""),
  }));
  return { ok: true, provider: "amap", items };
}
