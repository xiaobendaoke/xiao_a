import type { OpenClawPluginApi, AnyAgentTool } from "openclaw/plugin-sdk";
import { applyAlias, normalizeUserKey } from "../shared/identity.js";
import { clamp, shorten } from "../shared/text.js";
import { envStatus } from "../shared/env.js";

// Core utils/state imports
import { inferRecipientId, resolveUserKeyFromPrompt } from "./utils/intent.js";
import {
  hasWeatherIntent,
  hasStockIntent,
  hasGithubTrendingIntent,
  hasUrlSummaryIntent,
  hasSourceFollowupIntent,
  parseReminderIntent,
  detectGreetingType,
  extractPlanIntent,
  hasHabitIntent,
  hasDiaryIntent,
  hasGameIntent,
  hasMusicIntent,
  hasMovieIntent,
  hasRestaurantIntent,
  hasExpressIntent,
} from "./utils/intent.js";
import {
  extractUrls,
  extractImageRefs,
  extractAudioRefs,
  getPendingUrl,
  setPendingUrl,
  sweepPendingUrlCache,
  getPendingImage,
  setPendingImage,
  clearPendingImage,
  sweepPendingImageCache,
  isLikelyAttachmentOnlyInput,
} from "./utils/media.js";
import { loadPersonaPrompt, resolvePersonaPromptFilePath } from "./state/persona.js";
import {
  SESSION_USER_MAP,
  sweepSessionCache,
  formatUptimeSec,
  resolveStateFilePath,
  addMemoryNote,
  addChatEntry,
  getRecentNotes,
  getRecentChats,
  addLinkEvidence,
  getRecentLinks,
  retrieveRagHits,
  runDailyReflection,
  getUserPersona,
} from "./state/store.js";
import {
  extractUserInput,
  extractExplicitMemory,
  sanitizeAssistantOutbound,
  cleanAssistantText,
} from "./utils/text.js";
import { transcribeAudioPathForContext } from "./utils/audio.js";

// Feature module imports
import { registerXiaoWeatherCommand, fetchWeatherSummary, inferCityFromInput } from "./features/weather-command.js";
import { registerXiaoStockCommand, fetchStockSummary, inferStockSymbol } from "./features/stock-command.js";
import { registerXiaoTimeCommand } from "./features/time-command.js";
import { registerXiaoGithubCommand } from "./features/github-command.js";
import { registerXiaoGithubWeeklyCommand } from "./features/github-weekly-command.js";
import { registerXiaoSourceCommand } from "./features/source-command.js";
import { registerXiaoUrlBasicCommand } from "./features/url-basic-command.js";
import { registerXiaoDiagnosticsCommands } from "./features/diagnostics-commands.js";
import { registerXiaoMemoCommand } from "./features/memo-command.js";
import { registerXiaoMemoryCommand } from "./features/memory-command.js";
import { registerXiaoLinksCommand } from "./features/links-command.js";
import { registerXiaoReflectCommand } from "./features/reflect-command.js";
import { registerXiaoRemindCommand, reminderTargetFromUserKey } from "./features/remind-command.js";
import { registerXiaoGreetingCommand } from "./features/greeting-command.js";
import { registerXiaoPersonaCommand } from "./features/persona-command.js";
import { registerXiaoLoveScoreCommand } from "./features/love-score-command.js";
import { registerXiaoPlanCommand } from "./features/plan-command.js";
import { registerXiaoHabitCommand } from "./features/habit-command.js";
import { registerXiaoDiaryCommand } from "./features/diary-command.js";
import { registerXiaoGameCommand } from "./features/game-command.js";

function emptyPluginConfigSchema() {
  return {
    type: "object",
    properties: {},
    additionalProperties: false,
  };
}

const jsonResult = (data: unknown) => {
  return typeof data === "string" ? data : JSON.stringify(data);
};

const identityProbeSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    rawUserKey: { type: "string", description: "Raw user key to normalize/alias." },
    prompt: { type: "string", description: "Optional prompt text to infer qq identity." },
    sessionKey: { type: "string", description: "Optional OpenClaw session key." },
  },
};

const memorySearchSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    userKey: { type: "string", description: "Normalized user key, e.g. qqbot:123456789" },
    query: { type: "string", description: "Search query" },
    limit: { type: "integer", minimum: 1, maximum: 8 },
  },
  required: ["userKey", "query"],
};

const dailyReflectionSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    userKey: { type: "string", description: "Normalized user key, e.g. qqbot:123456789" },
    hours: { type: "integer", minimum: 1, maximum: 168, description: "Lookback hours, default 24" },
    minUserMessages: { type: "integer", minimum: 3, maximum: 60, description: "Minimum user messages to save" },
  },
  required: ["userKey"],
};

const xiaoCorePlugin = {
  id: "xiao-core",
  name: "Xiao Core",
  description: "Core migration helpers for OpenClaw QQ channel cutover",
  configSchema: emptyPluginConfigSchema(),
  register(api: OpenClawPluginApi) {
    api.on("before_agent_start", async (event, ctx) => {
      const now = Date.now();
      sweepSessionCache(now);
      sweepPendingUrlCache(now);
      sweepPendingImageCache(now);
      const personaPrompt = await loadPersonaPrompt();

      const prompt = event.prompt || "";
      const rawUserKey = resolveUserKeyFromPrompt(prompt, ctx.sessionKey);
      const mapped = applyAlias(rawUserKey);
      const userInput = extractUserInput(prompt);
      const audioRefs = extractAudioRefs(prompt);
      const voiceTranscript =
        audioRefs.length > 0 ? await transcribeAudioPathForContext(audioRefs[0] || "") : null;
      const effectiveUserInput =
        voiceTranscript && (isLikelyAttachmentOnlyInput(userInput) || userInput.length < 8)
          ? voiceTranscript
          : userInput;

      if (ctx.sessionKey) {
        SESSION_USER_MAP.set(ctx.sessionKey, {
          resolvedUserKey: mapped.resolved,
          aliasFrom: mapped.aliasFrom,
          seenAt: now,
          promptPreview: shorten(prompt, 120),
          userInput: effectiveUserInput,
          userInputRecorded: !!effectiveUserInput,
        });
      }

      // Token 优化配置
      const maxNotes = parseInt(process.env.XIAO_MAX_NOTES || "3", 10);
      const maxChats = parseInt(process.env.XIAO_MAX_CHATS || "4", 10);
      const maxRagHits = parseInt(process.env.XIAO_MAX_RAG_HITS || "3", 10);
      const enablePrefetch = process.env.XIAO_ENABLE_PREFETCH !== "false";

      const recentNotes = await getRecentNotes(mapped.resolved, maxNotes);
      const recentChats = await getRecentChats(mapped.resolved, maxChats);
      const ragHits = effectiveUserInput ? await retrieveRagHits(mapped.resolved, effectiveUserInput, maxRagHits) : [];
      const explicitMemo = extractExplicitMemory(effectiveUserInput);
      const reminderIntent = parseReminderIntent(effectiveUserInput);
      const greetingType = detectGreetingType(effectiveUserInput);
      const planIntent = extractPlanIntent(effectiveUserInput);
      const habitIntent = hasHabitIntent(effectiveUserInput);
      const diaryIntent = hasDiaryIntent(effectiveUserInput);
      const gameIntent = hasGameIntent(effectiveUserInput);
      const musicIntent = hasMusicIntent(effectiveUserInput);
      const movieIntent = hasMovieIntent(effectiveUserInput);
      const restaurantIntent = hasRestaurantIntent(effectiveUserInput);
      const expressIntent = hasExpressIntent(effectiveUserInput);
      const weatherIntent = hasWeatherIntent(effectiveUserInput);
      const stockIntent = hasStockIntent(effectiveUserInput);
      const githubIntent = hasGithubTrendingIntent(effectiveUserInput);
      const summaryIntent = hasUrlSummaryIntent(effectiveUserInput);
      const sourceIntent = hasSourceFollowupIntent(effectiveUserInput);
      const urlsInInput = extractUrls(effectiveUserInput);
      const directImageRefs = extractImageRefs(`${prompt}\n${effectiveUserInput}`);
      const pendingImage = directImageRefs.length === 0 ? getPendingImage(mapped.resolved) : null;
      const imageRefs = directImageRefs.length > 0 ? directImageRefs : pendingImage?.refs || [];
      const directUrl = urlsInInput[0] || "";
      if (urlsInInput.length > 0) {
        for (const url of urlsInInput) {
          await addLinkEvidence(mapped.resolved, "user", url, effectiveUserInput);
        }
      }
      if (directUrl) {
        setPendingUrl(mapped.resolved, directUrl, effectiveUserInput);
      }
      const pendingUrl = !directUrl && summaryIntent ? getPendingUrl(mapped.resolved) : null;
      const recentLinks = sourceIntent ? await getRecentLinks(mapped.resolved, 6) : [];
      const weatherCity = weatherIntent ? inferCityFromInput(effectiveUserInput) : null;
      const stockSymbol = stockIntent ? inferStockSymbol(effectiveUserInput) : null;

      let prefetchedWeather: string | null = null;
      let prefetchedStock: string | null = null;
      if (enablePrefetch) {
        [prefetchedWeather, prefetchedStock] = await Promise.all([
          weatherCity ? fetchWeatherSummary(weatherCity) : Promise.resolve(null),
          stockSymbol ? fetchStockSummary(stockSymbol) : Promise.resolve(null),
        ]);
      }

      if (effectiveUserInput) {
        await addChatEntry(mapped.resolved, "user", effectiveUserInput);
      }
      if (explicitMemo) {
        await addMemoryNote(mapped.resolved, explicitMemo, "explicit");
      }
      if (directImageRefs.length > 0) {
        setPendingImage(mapped.resolved, directImageRefs, effectiveUserInput);
      } else if (pendingImage && effectiveUserInput) {
        clearPendingImage(mapped.resolved);
      }
      const personaKey = await getUserPersona(mapped.resolved);

      const lines: string[] = [];
      lines.push("XIAO_CORE_CONTEXT");
      lines.push("runtime=openclaw_primary");
      lines.push(`user_key=${mapped.resolved}`);
      lines.push(`persona_key=${personaKey}`);
      if (personaKey === "big_sister") {
        lines.push("当前角色：知性大姐姐。语气温柔成熟，减少撒娇。");
      } else if (personaKey === "bestie") {
        lines.push("当前角色：闺蜜。语气更直率，允许轻度吐槽但不攻击用户。");
      } else if (personaKey === "little_sister") {
        lines.push("当前角色：可爱妹妹。语气活泼简短，适度撒娇。");
      } else {
        lines.push("当前角色：默认小a亲密陪伴模式。");
      }
      if (mapped.aliasFrom) {
        lines.push(`user_key_alias_from=${mapped.aliasFrom}`);
      }

      if (effectiveUserInput) {
        lines.push(`user_input=${shorten(effectiveUserInput, 240)}`);
      }
      if (voiceTranscript) {
        lines.push(`voice_transcript=${shorten(voiceTranscript, 240)}`);
        lines.push("检测到语音消息且已完成 ASR 转写。请优先基于 voice_transcript 回答，不要忽略语音内容。");
      } else if (audioRefs.length > 0) {
        lines.push("检测到语音消息，但本次 ASR 转写失败。请先告知用户“语音暂时没听清”，并请他重发或改文字。");
      }

      if (recentNotes.length > 0) {
        lines.push("recent_notes=");
        for (const n of recentNotes) {
          lines.push(`- [${n.source}] ${shorten(n.text, 120)}`);
        }
      }

      if (ragHits.length > 0) {
        lines.push("rag_hits=");
        for (const h of ragHits) {
          lines.push(`- (${h.from},score=${h.score}) ${shorten(h.text, 140)}`);
        }
      }

      if (recentChats.length > 0) {
        lines.push("recent_chats=");
        for (const c of recentChats) {
          lines.push(`- ${c.role}: ${shorten(c.text, 120)}`);
        }
      }

      if (explicitMemo) {
        lines.push("用户明确要求你记住一条信息，先自然确认，再回答问题。无需暴露内部标签。");
      }

      if (prefetchedWeather) {
        lines.push(`prefetched_weather=${prefetchedWeather}`);
        lines.push("关于天气优先使用 prefetched_weather；若信息不足再调用 xiao_weather_openmeteo。");
      } else if (weatherIntent) {
        if (weatherCity) {
          lines.push(
            `已识别天气城市=${weatherCity}，请优先调用工具 xiao_weather_openmeteo 获取实时数据，不要凭空猜天气。`,
          );
        } else {
          lines.push("用户在问天气但未识别出城市，请先简短追问城市，或调用 xiao_weather_openmeteo 默认城市后说明条件。");
        }
      }

      if (prefetchedStock) {
        lines.push(`prefetched_stock=${prefetchedStock}`);
        lines.push("关于股票优先使用 prefetched_stock；若用户追问公告/深度分析再调用工具补充。");
      } else if (stockIntent) {
        if (stockSymbol) {
          lines.push(
            `已识别股票代码=${stockSymbol}，请优先调用 xiao_stock_quote 获取行情，不要编造代码或价格。`,
          );
        } else {
          lines.push("用户在问股票但未识别代码，请先追问 6 位代码。");
        }
      }

      if (githubIntent) {
        lines.push(
          "用户在问 GitHub 周榜/热榜。请优先调用 xiao_github_trending（since=weekly, limit=5），基于真实返回结果再总结，不要编造项目名或 star 数据。",
        );
      }

      if (directUrl) {
        if (summaryIntent) {
          lines.push(
            `用户发了链接且希望总结。请优先调用 xiao_url_digest，参数建议：url=${shorten(directUrl, 220)}；基于返回内容做2-5行口语化总结。`,
          );
        } else {
          lines.push(
            `用户发了链接（${shorten(directUrl, 120)}）。若对方未明确要求总结，请先简短确认“要不要我帮你总结这篇链接”。`,
          );
        }
      } else if (summaryIntent) {
        if (pendingUrl?.url) {
          lines.push(
            `用户在追问“总结”。请优先调用 xiao_url_digest，总结最近一条链接：url=${shorten(pendingUrl.url, 220)}。`,
          );
        } else {
          lines.push("用户想要链接总结，但未提供可用 URL。请先让用户发链接。");
        }
      }

      if (imageRefs.length > 0) {
        if (pendingImage && directImageRefs.length === 0) {
          lines.push("检测到用户在追问上一条图片，以下 image_refs 为上一条缓存图片。");
        } else {
          lines.push("检测到用户发送了图片。不要凭空描述图片内容。");
        }
        lines.push("image_refs=");
        for (const ref of imageRefs) {
          lines.push(`- ${shorten(ref, 220)}`);
        }
        lines.push(
          `回答图片问题前必须先调用 xiao_vision_analyze，首选参数：imageUrl=${shorten(imageRefs[0] || "", 220)}。`,
        );
        lines.push("如果 xiao_vision_analyze 返回失败或超时，请明确告知“图片解析失败/超时，请重发清晰图片”，不要编造细节。");
        if (directImageRefs.length > 0 && isLikelyAttachmentOnlyInput(effectiveUserInput)) {
          lines.push("用户本条更像“只发图”。请先简短确认看到了图片，并追问他希望你看哪一部分，不要直接长篇总结。");
        }
      }

      if (sourceIntent) {
        if (recentLinks.length > 0) {
          lines.push("recent_links=");
          const latestFirst = recentLinks.slice().sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0));
          for (const item of latestFirst) {
            const at = new Date(Number(item.ts || 0)).toISOString();
            const context = item.context ? ` | context=${shorten(item.context, 90)}` : "";
            lines.push(`- [${item.source}] ${shorten(item.url, 220)} | at=${at}${context}`);
          }
          lines.push("用户在追问来源/链接。请优先基于 recent_links 给出真实 URL 列表，不要编造新链接。");
        } else {
          lines.push("用户在追问来源/链接，但当前没有可引用记录。请说明暂无可用链接，并请用户补发原文链接。");
        }
      }

      if (reminderIntent) {
        const to = reminderTargetFromUserKey(mapped.resolved);
        if (to) {
          lines.push(
            `识别到提醒意图：minutes=${reminderIntent.minutes}, content=${shorten(reminderIntent.content, 120)}`,
          );
          lines.push(
            `请优先调用 xiao_schedule_reminder，参数建议：to=${to}, minutesFromNow=${reminderIntent.minutes}, message=${shorten(reminderIntent.content, 120)}。`,
          );
        } else {
          lines.push("识别到提醒意图，但未解析到 QQ 用户目标。请先确认提醒对象后再调用 xiao_schedule_reminder。");
        }
      }
      if (greetingType) {
        lines.push(`识别到问候类型=${greetingType}，请使用轻松自然短回复。`);
      }
      if (planIntent) {
        lines.push(`识别到计划内容：${shorten(planIntent.content, 120)}。可引导用户使用 /xiao-plan add 结构化记录。`);
      }
      if (habitIntent) {
        lines.push("识别到打卡/习惯意图。可引导使用 /xiao-habit create|checkin|stats。");
      }
      if (diaryIntent) {
        lines.push("识别到心情日记意图。可引导使用 /xiao-diary add/today/weekly。");
      }
      if (gameIntent) {
        lines.push("识别到互动游戏意图。可引导使用 /xiao-game start riddle|love|truth。");
      }
      if (musicIntent) {
        lines.push("识别到音乐分享意图。优先调用 xiao_music_resolve 获取歌曲信息。");
      }
      if (movieIntent) {
        lines.push("识别到电影推荐意图。优先调用 xiao_movie_recommend。");
      }
      if (restaurantIntent) {
        lines.push("识别到餐厅推荐意图。优先调用 xiao_restaurant_search，缺城市先追问。");
      }
      if (expressIntent) {
        lines.push("识别到快递查询意图。优先调用 xiao_express_track，需要快递公司与单号。");
      }

      lines.push(
        "语音回复：优先在回复末尾添加 [[audio_as_voice]]，系统会把当前回复内容直接合成为语音并发送。不要手写伪造的 <qqvoice> 网络链接。",
      );
      lines.push("不要在最终回复里展示内部状态标签（例如 [MOOD_CHANGE] / [UPDATE_PROFILE]）。");

      lines.push("XIAO_PERSONA_PROMPT_BEGIN");
      lines.push(personaPrompt);
      lines.push("XIAO_PERSONA_PROMPT_END");
      lines.push("如果被问及部署方式，请说明：业务运行时是 OpenClaw QQ channel。compose/docker 仅可能用于某些环境的进程编排。\n");

      return { prependContext: lines.join("\n") };
    });

    api.on("message_sending", async (_event, ctx) => {
      const content = typeof _event.content === "string" ? _event.content : "";
      if (!content.trim()) {
        return;
      }

      const sessionKey = (ctx as { sessionKey?: string }).sessionKey || "";
      const snapshot = SESSION_USER_MAP.get(sessionKey);
      const fallbackKey = applyAlias(
        normalizeUserKey(`${ctx.channel || "unknown"}:${(_event.to || "unknown").trim() || "unknown"}`),
      ).resolved;
      const userKey = snapshot?.resolvedUserKey || fallbackKey;

      if (snapshot && snapshot.userInput && !snapshot.userInputRecorded) {
        await addChatEntry(userKey, "user", snapshot.userInput);
        const explicit = extractExplicitMemory(snapshot.userInput);
        if (explicit) {
          await addMemoryNote(userKey, explicit, "explicit");
        }
        snapshot.userInputRecorded = true;
        snapshot.seenAt = Date.now();
        SESSION_USER_MAP.set(sessionKey, snapshot);
      }

      const outbound = sanitizeAssistantOutbound(content);
      const clean = cleanAssistantText(outbound.text || content);
      if (clean) {
        await addChatEntry(userKey, "assistant", clean);
      }
      const urlsInReply = extractUrls(content);
      if (urlsInReply.length > 0) {
        for (const url of urlsInReply) {
          await addLinkEvidence(userKey, "assistant", url, clean || content);
        }
      }

      if (outbound.voicePath) {
        const source = /^https?:\/\//i.test(outbound.voicePath) ? "url" : "file";
        const payload: Record<string, unknown> = {
          type: "media",
          mediaType: "audio",
          source,
          path: outbound.voicePath,
        };
        if (outbound.text) {
          payload.caption = outbound.text;
        }
        return {
          content: `QQBOT_PAYLOAD:\n${JSON.stringify(payload)}`,
        };
      }

      if (outbound.text && outbound.text !== content) {
        return {
          content: outbound.text,
        };
      }
    });

    api.registerTool({
      name: "xiao_identity_probe",
      label: "Xiao Identity Probe",
      description: "Normalize and alias-resolve user identity for QQ channel migration.",
      parameters: identityProbeSchema,
      async execute(
        _toolCallId: string,
        params: { rawUserKey?: string; prompt?: string; sessionKey?: string },
      ) {
        const prompt = (params.prompt || "").trim();
        const sessionKey = (params.sessionKey || "").trim();
        const inferredFromPrompt = prompt ? inferRecipientId(prompt) : null;

        const candidate = (params.rawUserKey || "").trim() || (inferredFromPrompt ? `qqbot:${inferredFromPrompt}` : "");
        const normalized = normalizeUserKey(candidate || resolveUserKeyFromPrompt(prompt, sessionKey || undefined));
        const mapped = applyAlias(normalized);

        return jsonResult({
          ok: true,
          input: {
            rawUserKey: params.rawUserKey || "",
            prompt: shorten(prompt, 180),
            sessionKey,
          },
          inferred: {
            recipientIdFromPrompt: inferredFromPrompt,
          },
          normalized,
          resolved: mapped.resolved,
          aliasFrom: mapped.aliasFrom || null,
        });
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_memory_search",
      label: "Xiao Memory Search",
      description: "Search lightweight memory/RAG hits from xiao-core state.",
      parameters: memorySearchSchema,
      async execute(_toolCallId: string, params: { userKey?: string; query?: string; limit?: number }) {
        const userKey = normalizeUserKey((params.userKey || "").trim());
        const query = (params.query || "").trim();
        const limit = clamp(Number(params.limit || 5), 1, 8);

        if (!userKey || userKey === "session:unknown") {
          return jsonResult({ ok: false, error: "userKey is required" });
        }
        if (!query) {
          return jsonResult({ ok: false, error: "query is required" });
        }

        const hits = await retrieveRagHits(userKey, query, limit);
        return jsonResult({ ok: true, userKey, query, hits });
      },
    } as AnyAgentTool);

    api.registerTool({
      name: "xiao_daily_reflection",
      label: "Xiao Daily Reflection",
      description: "Generate and save a lightweight daily reflection note from recent chats.",
      parameters: dailyReflectionSchema,
      async execute(
        _toolCallId: string,
        params: { userKey?: string; hours?: number; minUserMessages?: number },
      ) {
        const userKey = normalizeUserKey((params.userKey || "").trim());
        if (!userKey || userKey === "session:unknown") {
          return jsonResult({ ok: false, error: "userKey is required" });
        }
        const result = await runDailyReflection({
          userKey,
          hours: Number(params.hours || 24),
          minUserMessages: Number(params.minUserMessages || 5),
        });
        return jsonResult(result);
      },
    } as AnyAgentTool);

    registerXiaoWeatherCommand(api);
    registerXiaoStockCommand(api);
    registerXiaoTimeCommand(api);
    registerXiaoGithubCommand(api);
    registerXiaoGithubWeeklyCommand(api);
    registerXiaoSourceCommand(api);
    registerXiaoUrlBasicCommand(api);
    registerXiaoDiagnosticsCommands(api, {
      formatUptimeSec: () => String(formatUptimeSec()),
      sessionUserMapSize: () => SESSION_USER_MAP.size,
      resolveStateFilePath,
      resolvePersonaPromptFilePath,
      envStatus: (name: string) => String(envStatus(name)),
      normalizeUserKey,
      applyAlias,
      shorten,
    });
    registerXiaoMemoCommand(api);
    registerXiaoMemoryCommand(api);
    registerXiaoLinksCommand(api);
    registerXiaoReflectCommand(api);
    registerXiaoRemindCommand(api);
    registerXiaoGreetingCommand(api);
    registerXiaoPersonaCommand(api);
    registerXiaoLoveScoreCommand(api);
    registerXiaoPlanCommand(api);
    registerXiaoHabitCommand(api);
    registerXiaoDiaryCommand(api);
    registerXiaoGameCommand(api);
  },
};

export default xiaoCorePlugin;
