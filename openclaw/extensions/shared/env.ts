/**
 * 统一的环境变量加载模块
 * 支持从进程环境变量和 .env 文件中读取配置
 */
import { existsSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

let envCache: Record<string, string> | null = null;
let envMtimeMs = -1;

function resolveEnvFilePath(): string {
    const fromEnv = (process.env.XIAO_ENV_FILE || "").trim();
    if (fromEnv) {
        return fromEnv;
    }
    return path.join(homedir(), ".openclaw", ".env");
}

function unquoteEnvValue(value: string): string {
    const v = value.trim();
    if (
        (v.startsWith("\"") && v.endsWith("\"") && v.length >= 2) ||
        (v.startsWith("'") && v.endsWith("'") && v.length >= 2)
    ) {
        return v.slice(1, -1).trim();
    }
    return v;
}

function loadEnvFile(): Record<string, string> {
    const file = resolveEnvFilePath();
    if (!existsSync(file)) {
        envCache = {};
        envMtimeMs = -1;
        return envCache;
    }

    try {
        const stat = statSync(file);
        if (envCache && envMtimeMs === stat.mtimeMs) {
            return envCache;
        }

        const content = readFileSync(file, "utf8");
        const parsed: Record<string, string> = {};
        for (const rawLine of content.split(/\r?\n/)) {
            const line = rawLine.trim();
            if (!line || line.startsWith("#")) {
                continue;
            }
            const idx = line.indexOf("=");
            if (idx <= 0) {
                continue;
            }
            const key = line.slice(0, idx).trim();
            const value = unquoteEnvValue(line.slice(idx + 1));
            if (!key) {
                continue;
            }
            parsed[key] = value;
        }

        envCache = parsed;
        envMtimeMs = stat.mtimeMs;
        return parsed;
    } catch {
        envCache = {};
        envMtimeMs = -1;
        return envCache;
    }
}

/**
 * 按名称获取环境变量值
 * 优先从 process.env 读取，其次从 .env 文件读取
 */
export function env(name: string): string {
    const runtime = (process.env[name] || "").trim();
    if (runtime) {
        return runtime;
    }
    const fileEnv = loadEnvFile();
    return (fileEnv[name] || "").trim();
}

/**
 * 按名称列表获取第一个有值的环境变量
 * 用于支持多别名回退（如 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY）
 */
export function envAny(names: string[]): string {
    for (const name of names) {
        const runtime = (process.env[name] || "").trim();
        if (runtime) {
            return runtime;
        }
    }
    const fileEnv = loadEnvFile();
    for (const name of names) {
        const fileValue = (fileEnv[name] || "").trim();
        if (fileValue) {
            return fileValue;
        }
    }
    return "";
}

function statusText(name: string): string {
    return env(name) ? "set" : "missing";
}

export function envStatus(name?: string): string | Record<string, string> {
    if (name && name.trim()) {
        return statusText(name.trim());
    }
    const keys = [
        "OPENCLAW_GATEWAY_TOKEN",
        "XIAO_USER_ALIAS_MAP",
        "XIAO_PERSONA_PROMPT_FILE",
        "SILICONFLOW_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
    ];
    const out: Record<string, string> = {};
    for (const k of keys) {
        out[k] = statusText(k);
    }
    return out;
}
