import { shorten, cleanAssistantText as cleanAssistantTextBase } from "../../shared/text.js";

export function extractUserInput(prompt: string): string {
  const src = (prompt || "").trim();
  if (!src) {
    return "";
  }

  const patterns = [
    /(?:^|\n)(?:用户输入|用户|User|USER|message|Message)\s*[：:]\s*(.+)$/gim,
    /(?:^|\n)(?:任务|问题|query)\s*[：:]\s*(.+)$/gim,
  ];

  for (const p of patterns) {
    let m: RegExpExecArray | null = null;
    let last: RegExpExecArray | null = null;
    while ((m = p.exec(src)) !== null) {
      last = m;
    }
    const candidate = (last?.[1] || "").trim();
    if (candidate) {
      return shorten(candidate, 800);
    }
  }

  const lines = src
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => !!line);
  if (lines.length === 0) {
    return "";
  }
  return shorten(lines[lines.length - 1] || "", 800);
}

export function extractExplicitMemory(input: string): string | null {
  const text = (input || "").trim();
  if (!text) {
    return null;
  }

  const prefixes = ["记住：", "记住:", "请记住：", "请记住:", "备忘：", "备忘:"];
  for (const prefix of prefixes) {
    if (text.startsWith(prefix)) {
      const payload = text.slice(prefix.length).trim();
      return payload ? shorten(payload, 300) : null;
    }
  }
  return null;
}

export function cleanAssistantText(text: string): string {
  return cleanAssistantTextBase(text);
}

export function sanitizeAssistantOutbound(text: string): { text: string; voicePath?: string } {
  const raw = (text || "").trim();
  const voiceMatch = raw.match(/<qqvoice>\s*([^<>\n]+?)\s*<\/qqvoice>/i);
  const voicePath = (voiceMatch?.[1] || "").trim();

  let cleaned = raw.replace(/<qqvoice>[\s\S]*?<\/qqvoice>/gi, "");
  cleaned = cleaned.replace(/<qqimg>[\s\S]*?<\/qqimg>/gi, "");
  cleaned = cleaned.replace(/<img\b[^>]*>/gi, "");
  cleaned = cleaned.replace(/!\[[^\]]*]\((?:file|https?):\/\/[^)]+\)/gi, "");
  cleaned = cleaned.replace(/\[\[\s*audio_as_voice\s*]\]/gi, "");
  cleaned = cleaned.replace(/\[MOOD_CHANGE[:：]\s*-?\d+\s*\]/gi, "");
  cleaned = cleaned.replace(/\[UPDATE_PROFILE[:：]\s*[^\]]+\]/gi, "");
  cleaned = cleaned
    .split("\n")
    .map((line) => line.replace(/\s+$/g, ""))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  if (voicePath) {
    return { text: cleaned, voicePath };
  }
  return { text: cleaned };
}
