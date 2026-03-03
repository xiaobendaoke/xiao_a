# xiao_a 迁移方案（腾讯云 OpenClaw + QQ 通道）

## 0. 当前执行状态（2026-02-27）

说明：下面是基于当前仓库与线上运行环境的实况进度，不是理想计划状态。

| 阶段 | 状态 | 当前结果 | 下一步 |
|---|---|---|---|
| 阶段0 冻结基线与可观测性 | 基本完成 | 已补回归用例清单 `docs/OPENCLAW_REGRESSION_CASES.md`，并落地统一日志字段与自动验收脚本 | 持续补充用例并做周期性回归 |
| 阶段1 通道切换 | 已完成（最小目标） | 腾讯云 OpenClaw QQ 通道可用，`xiao-core` 验活命令可执行 | 维持观测，准备阶段2全量迁移 |
| 阶段2 核心聊天主流程迁移 | 进行中 | `xiao-core` 已承接普通聊天/显式记忆/轻量RAG/天气股票 + GitHub周榜 + 链接总结意图引导（`xiao_url_digest`）+ 来源追问链接回传（`/xiao-links`）+ QQ提醒/反思命令 | 迁移更多 companion_core 逻辑并做回归对齐 |
| 阶段3 定时任务迁移 | 进行中 | OpenClaw cron 已从 4 项扩展到 6 项（新增 info-digest / reflection），并支持 `schedules`->cron 导入脚本 | 观察连续 3 天无漏发/重发并收敛推送策略 |
| 阶段4 语音与图片对齐 | 基本完成（持续观察） | 已补图片提示词/回退文案对齐、媒体限制与超时边界；新增成功率验收脚本并达标 | 连续观察 7 天，确认无回归后再改为“已完成” |
| 阶段5 下线 旧主控栈 | 已完成（代码归档） | `bot/`、`napcat/`、`docker-compose*.yml` 已迁入 `legacy/`，主路径仅保留 OpenClaw | 持续观察稳定性；如需回滚可从 `legacy/` 恢复 |

### 0.1 已完成项证据（可复核）

- OpenClaw 服务：`openclaw-gateway.service` 运行中（`active`）
- 网关可达：`openclaw status --json` 中 `gateway.reachable = true`
- 定时任务：`openclaw cron list --json` 当前可见
- `xiao-weather-<hash>`
- `xiao-finance-<hash>`
- `xiao-proactive-<hash>`
- `xiao-github-weekly-<hash>`
- `xiao-info-digest-<hash>`
- `xiao-reflection-<hash>`
- `xiao-core` 功能位置：`openclaw/extensions/xiao-core/index.ts`
- 包含验活命令 `/xiao-health`、`/xiao-whoami`、`/xiao-echo`、`/xiao-memory`、`/xiao-links`、`/xiao-remind`
- 可观测性日志：`~/.openclaw/xiao-core/observability.jsonl`
- 自动验收脚本：`scripts/openclaw_regression_smoke.sh --deep`

## 1. 目标与边界

目标：将当前 `LegacyBot + NapCat + OpenClaw侧车` 架构，迁移为 `OpenClaw(腾讯云) + QQ通道` 主控架构。

边界：

- 保留你现在的小a人设、记忆、情绪、工具能力。
- 逐步下线 `LegacyBot` 与 `NapCat`，最终仅保留 OpenClaw 侧运行。
- 先保证“功能可用”，再做“行为一致性”细抠（语气、气泡节奏、主动互动策略）。

## 2. 现状（代码事实）

历史主链路依赖 LegacyBot（现已归档到 `legacy/legacy-bot/`）：

- 启动入口是 LegacyBot：`legacy/legacy-bot/bot/bot.py`
- 私聊处理、notice、rule 在：`legacy/legacy-bot/bot/plugins/companion_core/handlers.py`
- 定时任务依赖 `legacy_apscheduler_plugin`：
  - `proactive.py`
  - `scheduler_custom.py`
  - `weather_push.py`
  - `github_weekly_push.py`
  - `finance_daily/daily_job.py`
- OpenClaw 旧桥接代码：`legacy/legacy-bot/bot/plugins/companion_core/openclaw_bridge.py`

你已有的 OpenClaw 基础：

- `xiao-emotion` 扩展已具备：情绪、画像、历史库映射、发送前标签清洗。
- `xiao-services` 扩展已具备：搜索/天气/股票/视觉/ASR/TTS/定时提醒/probe。

## 3. 目标架构

```text
QQ用户
  -> 腾讯云 OpenClaw QQ Channel
  -> OpenClaw Agent(main)
  -> OpenClaw plugins
     - xiao-emotion (已存在)
     - xiao-services (已存在)
     - xiao-core (待新增，承接原 companion_core 核心对话逻辑)
  -> 存储
     - OpenClaw state (情绪/画像)
     - xiao_a sqlite/chroma（迁移期可复用，稳定后再收敛）
```

关键点：

- 入站/出站都走 OpenClaw QQ channel，不再通过 NapCat websocket。
- `xiao-core` 成为真正的“聊天主控插件”，替代 LegacyBot 的 handlers + scheduler。

## 4. 推荐迁移策略

推荐：双轨灰度迁移（而不是一次性重写）。

原因：

- 你现在功能面很大（推送、语音、RAG、财经、主动互动）。
- 一次性替换风险高，问题定位困难。
- 双轨可以按功能分批切流，随时回滚。

## 5. 分阶段实施

## 阶段0：冻结基线与可观测性（0.5-1天，状态：进行中）

产出：

- 功能清单与验收样例（20-30条私聊用例）。
- 对照日志字段：`request_id/user_key/tool_name/latency/error_code`。
- 基线快照：`data.db`、`chroma_db/`、`openclaw/state/`。

验收：

- 当前线上流程可稳定复测。

## 阶段1：通道切换，不动核心逻辑（1-2天，状态：已完成-最小目标）

目标：先把消息入口切到腾讯云 OpenClaw QQ 通道。

动作：

- 在腾讯云 OpenClaw 上配置 QQ channel 为唯一入口。
- 保留现有 `xiao-emotion`、`xiao-services`。
- 新增“回声/health 命令”校验收发与 sender identity。
- 落地 user_id 映射策略：统一到 `qqbot:<id>`，沿用 `XIAO_USER_ALIAS_MAP`。

验收：

- `/mood status`、`/xiao-services status` 在 QQ 内可用。
- 工具调用成功率 >= 95%。

回滚：

- 切回原 NapCat + LegacyBot（保留旧 compose 即可）。

## 阶段2：迁移核心聊天主流程（3-5天，状态：进行中）

目标：把 `handlers.py + llm_core.py + agent_core.py` 主链路迁到 OpenClaw 插件 `xiao-core`。

`xiao-core` 最小职责：

- 接收用户消息并生成回复。
- 组装系统上下文（persona + mood/profile + memory）。
- 调用工具（优先复用 `xiao-services` 里的工具）。
- 回复后写入记忆。

实现建议：

- 不要先重写所有算法，先“逻辑对齐”。
- 先迁这几块：
  - Persona 与语气约束
  - RAG 查询/写入
  - 显式记忆（“记住：”）
  - 基础命令（查天气、查股、总结链接）

当前增量：

- 新增 `xiao_url_digest` 工具（网页抓取 + 标题/描述/正文预览提取）。
- `xiao-core` 已增加“链接总结意图”识别与 pending-url 跟进逻辑，开始替代旧 `handlers.py` 的 URL 总结链路。
- `xiao-core` 已增加“来源/链接追问”识别与 recent-links 上下文注入，支持在不暴露冗余链接的前提下按追问回传来源。
- 新增 `/xiao-links [limit]` 命令，用于快速排查最近链接证据写入是否正常。
- 新增 `scripts/openclaw_source_followup_check.sh`（网关两轮对话回归），可自动验证“先发链接再追问来源”的链路。
- 新增 `xiao_daily_reflection` 工具与 `/xiao-reflect` 命令，开始迁移旧 `reflection.py` 行为。

验收：

- 关键对话回归用例通过率 >= 90%。
- 单轮平均响应时延不高于现网 +25%。

## 阶段3：迁移定时任务与推送（2-4天，状态：进行中）

目标：移除对 LegacyBot apscheduler 的依赖。

迁移对象：

- 主动互动：`proactive.py`
- 日程提醒：`scheduler_custom.py`
- 天气早报：`weather_push.py`
- GitHub周榜：`github_weekly_push.py`
- 财经日报：`finance_daily/*`

策略：

- 统一改成 OpenClaw cron/job。
- 每个任务做幂等键（按 `user_id + day/week + task_type`），避免重复推送。

当前增量：

- `scripts/openclaw_migrate_scheduler.sh` 已新增两类任务：
  - `xiao-info-digest-*`（每日轻资讯）
  - `xiao-reflection-*`（每日反思沉淀）
- 仍保留开关：`--info-digest 0/1`、`--reflection 0/1`，便于灰度和回滚。
- 新增 `scripts/openclaw_cron_delivery_audit.sh`，可按近 N 天统计各 cron 任务的漏发/重发/失败信号（用于阶段3连续观测）。

验收：

- 连续 3 天定时任务无漏发/重发。

## 阶段4：语音与图片行为对齐（1-2天，状态：基本完成-持续观察）

目标：从“可用”调整到“体验一致”。

动作：

- 对齐 ASR/TTS 参数（`QWEN_TTS_VOICE`、rate/pitch/volume）。
- 对齐图片理解提示词与失败回退文案。
- 增加多媒体超时/大小限制与错误码映射。

当前增量：

- `xiao-services` 已增加媒体大小限制（默认 `XIAO_MEDIA_MAX_MB=20`）及超限错误返回，降低大文件导致的链路不稳定风险。
- 语音/图片链路已支持可配置超时（`XIAO_VISION_TIMEOUT_MS` / `XIAO_ASR_TIMEOUT_MS` / `XIAO_TTS_TIMEOUT_MS`）。
- TTS 已支持速率/音调/音量控制（`QWEN_TTS_RATE` / `QWEN_TTS_PITCH` / `QWEN_TTS_VOLUME`），并统一工具错误码回退。
- 图片链路已补默认中文提示词模板（可用 `XIAO_VISION_DEFAULT_PROMPT` 覆盖），并按错误码输出差异化回退文案。
- 新增成功率验收脚本：`scripts/openclaw_media_success_rate.sh`（按 observability 统计语音/图片成功率）。

最新验收（2026-02-27）：

- `./scripts/openclaw_media_success_rate.sh --hours 24 --min-samples 6 --target 95`
- 结果：overall success_rate = `95.24%`（达标）

验收：

- 语音与图片链路成功率 >= 95%。

## 阶段5：下线 旧主控栈（0.5-1天，状态：已完成-代码归档）

前提：阶段1-4连续稳定运行 >= 7 天。

动作：

- 将 `bot/`、`napcat/`、`docker-compose.yml` 迁移到 `legacy/legacy-bot/`。
- 将 `docker-compose.openclaw.yml` 迁移到 `legacy/openclaw-docker/`。
- README/SETUP 更新为纯 OpenClaw QQ 通道部署说明。

验收：

- 仅 OpenClaw 进程即可完整运行（当前环境满足）。

## 6. 模块迁移映射（建议）

| 当前模块 | 目标归属 | 处理方式 |
|---|---|---|
| `handlers.py` | `xiao-core` | 重写为 OpenClaw 消息处理入口 |
| `llm_core.py`/`agent_core.py` | `xiao-core` | 主流程迁移，先保留提示词策略 |
| `mood.py` + profile更新 | `xiao-emotion` | 已有，继续增强 |
| `rag_core.py` | `xiao-core` | 先复用现有 Chroma 持久化 |
| `memo.py`/`db.py` | `xiao-core` | 先复用 sqlite，再考虑并库 |
| `tools/*` | `xiao-services` | 已有为主，补缺口 |
| `proactive.py` 等 scheduler | OpenClaw cron | 全部替换 |
| `voice/*` | `xiao-services` + `xiao-core` | 服务层保留，编排层迁移 |

## 7. 数据迁移与兼容

用户标识：

- 统一主键格式：`qqbot:<openid_or_uin>`。
- 历史映射用 `XIAO_USER_ALIAS_MAP`，避免记忆断档。

存储建议：

- 迁移期：继续读写 `data.db + chroma_db`，风险最低。
- 稳定期：逐步收敛到 OpenClaw state + 独立业务库。

备份策略：

- 每日快照：`data.db`、`chroma_db/`、`openclaw/state/`。
- 关键迁移动作前做一次全量备份。

## 8. 风险与控制

风险：

- QQ channel 的 sender identity 与旧 QQ号不一致导致“像新用户”。
- 定时任务重复触发或漏触发。
- 语音链路在云环境下超时概率上升。
- 提示词差异导致小a语气漂移。

控制：

- 先做 identity 映射与回放测试。
- 所有定时任务加幂等键和重试上限。
- 多媒体请求设置硬超时、重试和降级文案。
- 语气一致性做 A/B 对照样例（固定20条）。

## 9. 工期预估（1人）

- 最快可用：7-10天（阶段0-3最小闭环）
- 完整替换：10-15天（含语音/图片对齐和稳定观察）

## 10. 建议你先做的第一步

先做“阶段1 + 阶段2最小集”：

- 用腾讯云 OpenClaw QQ channel 接管收发。
- 新建 `xiao-core` 只迁移 4 个能力：
  - 普通私聊
  - 显式记忆
  - RAG 检索
  - 天气/股票工具调用

这样 2-3 天就能看到“去 LegacyBot 后是否跑得稳”。
