# 小a 功能路由报告（哪些需要经过 LLM）

日期：2026-02-28  
范围：`OpenClaw + QQBot + xiao-core/xiao-emotion/xiao-services`

## 1. 结论（先看）

你现在的大部分“工具型功能”都**不必**先经过主对话 LLM（`qwen-plus-latest`）。  
真正必须经过主对话 LLM 的，主要只有：

1. 情感陪聊（自由表达、语气、人设、共情）
2. 开放式多轮推理（用户意图不明确、需要综合判断）

其他如提醒、天气、股票、GitHub、链接摘要、来源追问、命令查询，原则上都可以先走规则/工具直连，再按需用模板回复，避免把每次请求都送进大模型。

---

## 2. 关键证据

1. 当前消息主路径是 `qqbot -> dispatchReplyWithBufferedBlockDispatcher -> agent`，默认会进入 Agent（通常会触发 LLM）：  
   - `/root/.openclaw/extensions/qqbot/src/gateway.ts:1097`
2. `xiao-core` 在 `before_agent_start` 中把大量上下文（记忆、RAG、工具引导、人设）注入到 prompt：  
   - `/root/.openclaw/extensions/xiao-core/index.ts:1322`  
   - `/root/.openclaw/extensions/xiao-core/index.ts:1393`
3. 你已经有大量可规则化能力（提醒/天气/股票/URL/来源意图解析）：
   - `/root/.openclaw/extensions/xiao-core/index.ts:507`
   - `/root/.openclaw/extensions/xiao-core/index.ts:591`
   - `/root/.openclaw/extensions/xiao-core/index.ts:693`
   - `/root/.openclaw/extensions/xiao-core/index.ts:784`
   - `/root/.openclaw/extensions/xiao-core/index.ts:803`
4. 你已经有一批“不走聊天 LLM”的命令处理器（直接返回）：
   - `/root/.openclaw/extensions/xiao-core/index.ts:1673`
   - `/root/.openclaw/extensions/xiao-emotion/index.ts:855`
5. `xiao-services` 工具多数是外部 API/数据抓取，不需要主对话 LLM先参与：
   - weather/stock/url/search/github：`/root/.openclaw/extensions/xiao-services/index.ts:2016` 起
6. 语音发送链路可直接 TTS，不依赖主对话 LLM做“发送动作”：
   - `/root/.openclaw/extensions/qqbot/src/gateway.ts:1155`
7. 历史 cron 任务单次输入 token 很高（已观测）：
   - `xiao-info-digest` 平均约 `24523` 输入 token/次
   - `xiao-reflection` 平均约 `22431` 输入 token/次
   - `xiao-weather` 约 `14310` 输入 token/次  
   数据来源：`/root/.openclaw/cron/runs/*.jsonl` + `/root/.openclaw/cron/jobs.json`

---

## 3. 功能分级（必须过 LLM / 可不经过 / 混合）

### A. 必须经过主对话 LLM（建议保留）

1. 情感陪聊自由对话（核心价值）
2. 开放式闲聊 + 多轮上下文整合
3. 复杂主观任务（如“帮我安慰一下、写段话、分析情绪变化”）

原因：这类需求本质是生成式语言能力，规则系统替代后体验会明显下降。

### B. 可不经过主对话 LLM（建议优先直连）

1. `/xiao-health`、`/xiao-whoami`、`/xiao-memory`、`/xiao-links`、`/xiao-remind`、`/mood` 等命令  
2. 明确格式提醒（如“30分钟后提醒我喝水”）  
3. 天气/股票/GitHub等数据查询（先查数据，再模板化输出）  
4. 来源追问（直接回 recent links）  
5. URL 摘要的“基础模式”（标题+描述+前N字）  

原因：这些任务输入结构化、目标确定，工具结果本身就是答案核心，不需要先过大模型。

### C. 混合路径（先工具，后按需 LLM）

1. 图片：识别可走 `xiao_vision_analyze`，后处理可以模板化；仅复杂追问再进 LLM  
2. 语音：ASR/TTS 可直连；ASR结果后的“情感回复”可进 LLM  
3. URL 深度解读：先 `xiao_url_digest`，用户要求“观点/延展分析”再进 LLM  
4. 情绪画像更新：当前依赖 LLM输出标签（`[MOOD_CHANGE]`、`[UPDATE_PROFILE]`），可逐步增加规则抽取作为前置

---

## 4. 建议路由策略（保留全部功能，但省 token）

目标：功能不减，LLM只处理“必须生成”的部分。

### 路由层级

1. L0 规则直返层（0 token）
2. L1 工具直连 + 模板回复层（极低 token 或 0）
3. L2 主对话 LLM层（仅情感/开放式）

### 推荐匹配顺序

1. 命令消息（`/xiao-*`、`/mood`） -> 直接命令处理  
2. 明确提醒表达 -> 直接 `xiao_schedule_reminder`  
3. 明确天气/股票/GitHub/来源追问 -> 直接工具 + 模板  
4. 图片/语音输入 -> 先 vision/asr 工具  
5. 其余才进入主对话 LLM

---

## 5. 你这套代码里的可落地点

1. 网关预路由入口（最优先）：  
   - `/root/.openclaw/extensions/qqbot/src/gateway.ts:780` 附近  
   在 `dispatchReplyWithBufferedBlockDispatcher` 前做 L0/L1 分流。
2. 复用已有规则函数：  
   - `/root/.openclaw/extensions/xiao-core/index.ts:507` 起（intent解析）
3. 复用已有工具执行器：  
   - `/root/.openclaw/extensions/xiao-services/index.ts:1857` 起
4. 语音/图片发送逻辑已完备，可直接走媒体链路：  
   - `/root/.openclaw/extensions/qqbot/src/gateway.ts:1155`  
   - `/root/.openclaw/extensions/qqbot/src/gateway.ts:1188`

---

## 6. 优先级建议

### P0（立刻）

1. 保持 cron 关闭或仅保留必要项（你目前已关闭，正确）  
2. 命令和明确提醒走直连，不进主对话 LLM  
3. 天气/股票/来源追问先改模板回复

### P1（第二步）

1. URL 摘要分“基础模板 / 深度解读”两档  
2. 图片问答分“描述类模板 / 推理类LLM”两档  
3. 语音请求分“仅播报 / 播报+聊天”两档

### P2（第三步）

1. 情绪标签从“纯LLM生成”改为“规则优先 + LLM兜底”  
2. 做路由命中率统计（L0/L1/L2占比）并持续调参

---

## 7. 注意点

1. “不经过主对话LLM”不等于“零成本”：vision/asr/tts 仍是模型调用，只是比主聊天模型更可控。  
2. 你当前有一个可疑点：`messageBody` 被赋值但未实际使用（`gateway.ts` 903/909）；不影响本报告结论，但建议后续清理避免误判逻辑。  
3. 最稳妥路线是“先加路由，不删功能”，逐步把高频确定性请求从 L2 下沉到 L1/L0。

