import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { clamp, errToString } from "./text.js";
import { env } from "./env.js";

const execFileAsync = promisify(execFile);

export function mediaMaxBytes(): number {
    const v = env("XIAO_MEDIA_MAX_MB");
    const mb = parseInt(v, 10);
    if (Number.isFinite(mb) && mb > 0) {
        return mb * 1024 * 1024;
    }
    return 20 * 1024 * 1024;
}

export async function fetchJson(url: string, init?: RequestInit, timeoutMs: number = 12000): Promise<unknown> {
    const res = await fetch(url, {
        ...init,
        signal: AbortSignal.timeout(timeoutMs),
    });
    const text = await res.text();
    if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${text.slice(0, 300)}`);
    }
    if (!text) {
        return {};
    }
    try {
        return JSON.parse(text);
    } catch {
        return { raw: text };
    }
}

export async function fetchJsonByCurl(params: {
    url: string;
    timeoutSec?: number;
    proxy?: string;
    headers?: Record<string, string>;
}): Promise<unknown> {
    const timeoutSec = clamp(Number(params.timeoutSec || 20), 3, 120);
    const args: string[] = ["-sS", "-L", "--fail-with-body", "--max-time", String(timeoutSec)];
    const proxy = (params.proxy || "").trim();
    if (proxy) {
        args.push("-x", proxy);
    }
    for (const [k, v] of Object.entries(params.headers || {})) {
        args.push("-H", `${k}: ${v}`);
    }
    args.push(params.url);

    try {
        const { stdout } = await execFileAsync("curl", args, {
            timeout: timeoutSec * 1000 + 3000,
            maxBuffer: 8 * 1024 * 1024,
            env: {
                ...process.env,
                HTTP_PROXY: proxy || "",
                HTTPS_PROXY: proxy || "",
                ALL_PROXY: proxy || "",
                http_proxy: proxy || "",
                https_proxy: proxy || "",
                all_proxy: proxy || "",
            },
        });
        const text = (stdout || "").trim();
        if (!text) {
            return {};
        }
        try {
            return JSON.parse(text);
        } catch {
            return { raw: text };
        }
    } catch (err) {
        const e = err as Error & { stdout?: string; stderr?: string };
        const msg = `${(e.stderr || "").trim()} ${(e.stdout || "").trim()}`.trim() || errToString(err);
        throw new Error(`curl request failed: ${msg.slice(0, 300)}`);
    }
}

export async function fetchTextByCurl(params: {
    url: string;
    timeoutSec?: number;
    proxy?: string;
    headers?: Record<string, string>;
    compressed?: boolean;
}): Promise<string> {
    const timeoutSec = clamp(Number(params.timeoutSec || 20), 3, 120);
    const args: string[] = ["-sS", "-L", "--fail-with-body", "--max-time", String(timeoutSec)];
    if (params.compressed === true) {
        args.push("--compressed");
    }
    const proxy = (params.proxy || "").trim();
    if (proxy) {
        args.push("-x", proxy);
    }
    for (const [k, v] of Object.entries(params.headers || {})) {
        args.push("-H", `${k}: ${v}`);
    }
    args.push(params.url);

    try {
        const { stdout } = await execFileAsync("curl", args, {
            timeout: timeoutSec * 1000 + 3000,
            maxBuffer: 8 * 1024 * 1024,
            env: {
                ...process.env,
                HTTP_PROXY: proxy || "",
                HTTPS_PROXY: proxy || "",
                ALL_PROXY: proxy || "",
                http_proxy: proxy || "",
                https_proxy: proxy || "",
                all_proxy: proxy || "",
            },
        });
        return String(stdout || "");
    } catch (err) {
        const e = err as Error & { stdout?: string; stderr?: string };
        const msg = `${(e.stderr || "").trim()} ${(e.stdout || "").trim()}`.trim() || errToString(err);
        throw new Error(`curl request failed: ${msg.slice(0, 300)}`);
    }
}

export async function fetchBytes(
    url: string,
    init?: RequestInit,
    timeoutMs: number = 15000,
): Promise<{ bytes: Uint8Array; contentType: string }> {
    const res = await fetch(url, {
        ...init,
        signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status}: ${text.slice(0, 300)}`);
    }
    const maxBytes = mediaMaxBytes();
    const contentLength = Number(res.headers.get("content-length") || 0);
    if (Number.isFinite(contentLength) && contentLength > 0 && contentLength > maxBytes) {
        throw new Error(`media_too_large: content-length=${contentLength} > max=${maxBytes}`);
    }
    const contentType = res.headers.get("content-type") || "application/octet-stream";
    const ab = await res.arrayBuffer();
    if (ab.byteLength > maxBytes) {
        throw new Error(`media_too_large: bytes=${ab.byteLength} > max=${maxBytes}`);
    }
    return {
        bytes: new Uint8Array(ab),
        contentType,
    };
}
