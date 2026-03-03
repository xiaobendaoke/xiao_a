# OpenClaw QQ 回归清单（小a）

日期基线：2026-02-27

目标：覆盖迁移后的主链路（QQ通道、xiao-core、xiao-services、cron）。

## 1. 通道与基础（6条）

1. QQ 私聊发送 `你好`，应在 15 秒内收到回复。
2. QQ 私聊发送 `/xiao-health`，应返回通道与环境状态。
3. QQ 私聊发送 `/xiao-whoami`，应返回 `resolved_user_key`。
4. QQ 私聊发送 `/xiao-echo test`，应回显 `test`。
5. 网关重启后（`systemctl --user restart openclaw-gateway.service`），2 分钟内恢复收发。
6. `openclaw status --json` 显示 `gateway.reachable=true`。

## 2. 记忆与RAG（6条）

1. 发送 `记住：我喜欢冰美式`，应确认并记忆。
2. 发送 `/xiao-memory list`，应包含“冰美式”。
3. 发送 `/xiao-memory search 冰美式`，应命中相关条目。
4. 发送无关问题（如“今天天气”），不应误召回无关记忆。
5. 同一用户连续 5 轮聊天后，`/xiao-memory search` 仍能命中近期上下文。
6. 切换用户后，A 用户记忆不应泄露给 B 用户。

## 2.1 新增陪伴功能（8条）

1. `/xiao-persona list` 返回可切换角色列表。
2. `/xiao-persona set bestie` 后，下一轮语气应偏闺蜜风格。
3. `/xiao-love-score` 返回总分、等级和分项。
4. `/xiao-plan add 周末一起看电影` 后，`/xiao-plan list` 可见该条目。
5. `/xiao-habit create 早起` 后，`/xiao-habit checkin 早起` 应打卡成功。
6. `/xiao-diary add 20 今天状态还不错` 后，`/xiao-diary today` 应可读到当天记录。
7. `/xiao-game start riddle` 能进入游戏状态，`/xiao-game answer 人`可答题。
8. `/xiao-greet 晚安` 应返回晚安类回复。

## 3. 工具链（天气/股票/GitHub/链接）（10条）

1. 发送“上海今天天气”，回复应包含真实天气要素且不编造。
2. 发送“查一下 600519”，回复应包含价格/涨跌信息。
3. 发送“github周榜”，应返回至少 3 个仓库名。
4. 当 GitHub 请求失败时，应返回“抓取失败”而不是编造结果。
5. `/xiao-services probe` 应可执行并返回检查摘要。
6. `xiao_github_trending` 可在工具链中被成功调用（通过 cron run 记录验证）。
7. 发送“帮我总结这个链接：https://github.com/openai/openai-cookbook”，应触发 `xiao_url_digest` 并返回非空摘要。
8. 在第 7 条后追加“把刚刚来源链接发我”，应返回至少 1 条真实 URL（优先 recent links）。
9. QQ 私聊发送 `/xiao-links 5`，应返回最近链接证据列表或明确 `no recent links`。
10. 执行 `./scripts/openclaw_source_followup_check.sh --user phase2-source-check --url https://example.com`，应返回 `[PASS] source follow-up returned url`。

## 3.1 新增生活工具（4条）

1. 调用 `xiao_music_resolve` 解析音乐链接，返回平台和歌曲信息或明确失败原因。
2. 调用 `xiao_movie_recommend`，在缺少 `TMDB_API_KEY` 时返回 `missing_env`，有 key 时返回列表。
3. 调用 `xiao_restaurant_search`，在缺少 `AMAP_KEY` 时返回 `missing_env`，有 key 时返回列表。
4. 调用 `xiao_express_track`，在缺少快递配置时返回 `missing_env`，有 key 时返回轨迹数据。

## 4. 提醒与定时任务（8条）

1. 发送 `/xiao-remind 1 测试提醒`，1 分钟后应收到提醒。
2. `openclaw cron list --json` 应包含 weather/finance/proactive/github-weekly/info-digest/reflection 6 项任务。
3. 执行 `scripts/openclaw_migrate_scheduler.sh --remove --target <qqbot:c2c:...>`（dry-run）应显示 6 项待删任务。
4. 执行 `scripts/openclaw_migrate_schedules_from_db.sh --auto-target` 应正确识别待导入提醒数量。
5. cron 任务触发失败时应可在 `openclaw cron runs --id <id> --limit 5` 查到记录。
6. 手动 `openclaw cron run` `xiao-info-digest-*`，应生成非空 summary。
7. QQ 发送 `/xiao-reflect 24`，应返回 `reflection saved` 或 `reflection skipped`。
8. 执行 `./scripts/openclaw_cron_delivery_audit.sh --days 3 --job-prefix xiao-`，应输出每个任务的 `runs/expect/dup/miss/manual/fail` 统计。

## 5. 错误与降级（4条）

1. 临时断开外网后，天气/GitHub请求应返回“失败提示”而非胡编。
2. 提供错误股票代码（如 `123`）时，应提示代码无效。
3. 无法识别城市的天气问题，应追问城市或说明条件不足。
4. 非 QQ 通道调用 `/xiao-remind` 时，应提示上下文不支持自动识别目标。

## 6. 多媒体稳定性（3条）

1. 上传超大音频/图片时，应返回 `media_too_large` 或等价提示，不应卡死。
2. 将 `XIAO_VISION_TIMEOUT_MS` 调小后，图片请求超时应返回明确 `timeout` 类错误。
3. 调整 `QWEN_TTS_RATE/PITCH/VOLUME` 后，`xiao_tts_synthesize` 返回中应包含对应参数值。

## 7. 验收门槛

- 主链路成功率 >= 95%（上述用例至少 26 条通过 25 条）
- 关键命令（`/xiao-health`、`/xiao-whoami`、`/xiao-memory`）通过率 100%
- 定时任务连续 3 天无重复推送/漏推送
