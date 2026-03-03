import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../../shared/identity.js";
import { getUserPersona, setUserPersona } from "../state/store.js";

const PERSONAS = ["default", "big_sister", "bestie", "little_sister"] as const;

export function registerXiaoPersonaCommand(api: OpenClawPluginApi): void {
  api.registerCommand({
    name: "xiao-persona",
    description: "Persona switch. Usage: /xiao-persona [list|current|set <name>]",
    acceptsArgs: true,
    handler: async (ctx) => {
      const actor =
        (ctx.from && String(ctx.from).trim()) ||
        (ctx.senderId && String(ctx.senderId).trim()) ||
        "unknown";
      const userKey = applyAlias(normalizeUserKey(`${ctx.channel}:${actor}`)).resolved;
      const args = (ctx.args || "").trim();
      if (!args || args === "current") {
        const current = await getUserPersona(userKey);
        return { text: `current persona: ${current}` };
      }
      if (args === "list") {
        return { text: `available: ${PERSONAS.join(", ")}` };
      }
      const m = args.match(/^set\s+(.+)$/i);
      if (!m?.[1]) return { text: "usage: /xiao-persona [list|current|set <name>]" };
      const key = m[1].trim();
      if (!PERSONAS.includes(key as (typeof PERSONAS)[number])) {
        return { text: `invalid persona: ${key}` };
      }
      await setUserPersona(userKey, key);
      return { text: `persona set to ${key}` };
    },
  });
}
