import { existsSync, promises as fs, statSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const DEFAULT_PERSONA_PROMPT = [
    "你是小a，亲密陪伴型聊天对象，语气自然、口语化、像真实恋人，不要客服腔。",
    "优先短句，1-4行，除非用户要求详细再展开。",
    "如果上下文要求格式化代码或提取数据，按要求输出，不加废话。",
    "涉及事实（天气/股票/链接/图片）必须基于工具返回，不要编造。",
    "仅在状态确实变化时，才在回复末尾使用内部标签：[MOOD_CHANGE:x] 或 [UPDATE_PROFILE:key=value]。",
].join("\n");

let personaCache = "";
let personaCacheFile = "";
let personaCacheMtimeMs = -1;

export function resolvePersonaPromptFilePath(): string {
    const fromEnv = (process.env.XIAO_PERSONA_PROMPT_FILE || "").trim();
    if (fromEnv) {
        return fromEnv;
    }
    const moduleDir = path.dirname(fileURLToPath(import.meta.url));
    const moduleSibling = path.resolve(moduleDir, "..", "persona.prompt.md");
    const candidates = [
        moduleSibling,
        path.join(homedir(), ".openclaw", "extensions", "xiao-core", "persona.prompt.md"),
        path.join(process.cwd(), "openclaw", "extensions", "xiao-core", "persona.prompt.md"),
        path.join(process.cwd(), "xiao_a_local", "openclaw", "extensions", "xiao-core", "persona.prompt.md"),
        path.join(process.cwd(), "persona.prompt.md"),
        "/root/xiao_a/openclaw/extensions/xiao-core/persona.prompt.md",
        "/root/xiao_a/persona.prompt.md",
    ];
    for (const file of candidates) {
        if (existsSync(file)) {
            return file;
        }
    }
    return candidates[0];
}

export async function loadPersonaPrompt(): Promise<string> {
    const file = resolvePersonaPromptFilePath();
    if (!existsSync(file)) {
        personaCache = DEFAULT_PERSONA_PROMPT;
        personaCacheFile = file;
        personaCacheMtimeMs = -1;
        return personaCache;
    }
    try {
        const stat = statSync(file);
        if (personaCache && personaCacheFile === file && personaCacheMtimeMs === stat.mtimeMs) {
            return personaCache;
        }
        const raw = await fs.readFile(file, "utf8");
        const cleaned = raw
            .replace(/\r\n/g, "\n")
            .split("\n")
            .map((line) => line.trimEnd())
            .join("\n")
            .trim();
        personaCache = cleaned || DEFAULT_PERSONA_PROMPT;
        personaCacheFile = file;
        personaCacheMtimeMs = stat.mtimeMs;
        return personaCache;
    } catch {
        personaCache = DEFAULT_PERSONA_PROMPT;
        personaCacheFile = file;
        personaCacheMtimeMs = -1;
        return personaCache;
    }
}
