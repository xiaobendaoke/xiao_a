import type { OpenClawPluginApi } from "openclaw/plugin-sdk";

type MemoryNote = {
  text: string;
  source: string;
};

type RagHit = {
  from: string;
  score: number;
  text: string;
};

type MemoryCommandDeps = {
  applyAlias: (rawUserKey: string) => { resolved: string; aliasFrom?: string };
  normalizeUserKey: (raw: string) => string;
  getRecentNotes: (userKey: string, limit: number) => Promise<MemoryNote[]>;
  addMemoryNote: (userKey: string, text: string, source: "explicit" | "derived") => Promise<void>;
  retrieveRagHits: (userKey: string, query: string, limit: number) => Promise<RagHit[]>;
  shorten: (text: string, maxLen: number) => string;
};

export function registerXiaoMemoryCommand(api: OpenClawPluginApi, deps: MemoryCommandDeps): void {
  api.registerCommand({
    name: "xiao-memory",
    description: "Memory ops. Usage: /xiao-memory [list|add <text>|search <query>]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const raw = `${ctx.channel}:${actor}`;
      const userKey = deps.applyAlias(deps.normalizeUserKey(raw)).resolved;
      const args = (ctx.args || "").trim();

      if (!args || args === "list") {
        const notes = await deps.getRecentNotes(userKey, 10);
        if (notes.length === 0) {
          return { text: "memory is empty" };
        }
        const lines = notes.map((n, i) => `${String(i + 1).padStart(2, "0")}. [${n.source}] ${n.text}`);
        return { text: lines.join("\n") };
      }

      if (args.startsWith("add ")) {
        const payload = args.slice(4).trim();
        if (!payload) {
          return { text: "usage: /xiao-memory add <text>" };
        }
        await deps.addMemoryNote(userKey, payload, "explicit");
        return { text: "memory saved" };
      }

      if (args.startsWith("search ")) {
        const query = args.slice(7).trim();
        if (!query) {
          return { text: "usage: /xiao-memory search <query>" };
        }
        const hits = await deps.retrieveRagHits(userKey, query, 6);
        if (hits.length === 0) {
          return { text: "no memory hit" };
        }
        const lines = hits.map((h, i) => `${i + 1}. (${h.from},score=${h.score}) ${deps.shorten(h.text, 120)}`);
        return { text: lines.join("\n") };
      }

      return { text: "usage: /xiao-memory [list|add <text>|search <query>]" };
    },
  });
}
