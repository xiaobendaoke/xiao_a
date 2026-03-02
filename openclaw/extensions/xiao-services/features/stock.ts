/**
 * A 股行情查询服务
 * 提供东方财富（主）和新浪（备）两个数据源
 */
import { fetchJson } from "../../shared/request.js";
import { errToString } from "../../shared/text.js";

export type NormalizedStock = { code: string; market: "SH" | "SZ" };

export type StockQuoteResult = {
    provider: "eastmoney" | "sina";
    symbol: string;
    name: string;
    quote: {
        price: number;
        preclose: number;
        open: number;
        high: number;
        low: number;
        pctChange: number;
        volume: number;
        amount: number;
    };
};

export function normalizeStockSymbol(input: string): NormalizedStock | null {
    const t = (input || "").trim().toUpperCase();
    if (!t) {
        return null;
    }

    const matchTs = t.match(/^(\d{6})\.(SH|SZ)$/);
    if (matchTs) {
        return { code: matchTs[1], market: matchTs[2] as "SH" | "SZ" };
    }

    const matchPrefixed = t.match(/^(SH|SZ)(\d{6})$/);
    if (matchPrefixed) {
        return { code: matchPrefixed[2], market: matchPrefixed[1] as "SH" | "SZ" };
    }

    const matchCode = t.match(/(\d{6})/);
    if (!matchCode) {
        return null;
    }

    const code = matchCode[1];
    const market = code.startsWith("6") ? "SH" : "SZ";
    return { code, market };
}

export async function fetchStockEastmoney(normalized: NormalizedStock): Promise<StockQuoteResult> {
    const secid = `${normalized.market === "SH" ? "1" : "0"}.${normalized.code}`;
    const url =
        "https://push2.eastmoney.com/api/qt/stock/get" +
        `?secid=${encodeURIComponent(secid)}` +
        "&fields=f57,f58,f43,f44,f45,f46,f47,f48,f60,f169";
    const data = (await fetchJson(url, undefined, 8000)) as { data?: Record<string, number | string> };
    const d = data.data || {};
    const price = Number(d.f43 || 0) / 100;
    if (!Number.isFinite(price) || price <= 0) {
        throw new Error("eastmoney returned empty quote");
    }
    return {
        provider: "eastmoney",
        symbol: `${normalized.code}.${normalized.market}`,
        name: String(d.f58 || "").trim(),
        quote: {
            price,
            preclose: Number(d.f60 || 0) / 100,
            open: Number(d.f46 || 0) / 100,
            high: Number(d.f44 || 0) / 100,
            low: Number(d.f45 || 0) / 100,
            pctChange: Number(d.f169 || 0) / 100,
            volume: Number(d.f47 || 0),
            amount: Number(d.f48 || 0),
        },
    };
}

export async function fetchStockSina(normalized: NormalizedStock): Promise<StockQuoteResult> {
    const symbol = `${normalized.market.toLowerCase()}${normalized.code}`;
    const url = `https://hq.sinajs.cn/list=${encodeURIComponent(symbol)}`;
    const res = await fetch(url, {
        method: "GET",
        headers: {
            "User-Agent": "Mozilla/5.0",
            Referer: "https://finance.sina.com.cn/",
        },
        signal: AbortSignal.timeout(8000),
    });
    const rawBody = new Uint8Array(await res.arrayBuffer());
    let body = "";
    try {
        body = new TextDecoder("gb18030").decode(rawBody);
    } catch {
        body = new TextDecoder("utf-8").decode(rawBody);
    }
    if (!res.ok) {
        throw new Error(`sina HTTP ${res.status}`);
    }
    const payloadMatch = body.match(/=\"([^\"]+)\"/);
    if (!payloadMatch?.[1]) {
        throw new Error("sina response parse failed");
    }
    const parts = payloadMatch[1].split(",");
    if (parts.length < 10) {
        throw new Error("sina quote fields insufficient");
    }

    const name = (parts[0] || "").trim() || `${normalized.code}.${normalized.market}`;
    const open = Number(parts[1] || 0);
    const preclose = Number(parts[2] || 0);
    const price = Number(parts[3] || 0);
    const high = Number(parts[4] || 0);
    const low = Number(parts[5] || 0);
    const volume = Number(parts[8] || 0);
    const amount = Number(parts[9] || 0);
    if (!Number.isFinite(price) || price <= 0) {
        throw new Error("sina returned empty quote");
    }
    const pctChange = preclose > 0 ? ((price - preclose) / preclose) * 100 : 0;

    return {
        provider: "sina",
        symbol: `${normalized.code}.${normalized.market}`,
        name,
        quote: {
            price,
            preclose,
            open,
            high,
            low,
            pctChange,
            volume,
            amount,
        },
    };
}
