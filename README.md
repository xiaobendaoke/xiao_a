# qqbot-stack（NapCat + NoneBot2 + docker-compose）

本项目用 `docker-compose` 一键启动：
- `napcat`：提供 OneBot 协议与 WebUI
- `nonebot`：机器人本体（本仓库的 `./bot`）
- `gptsovits`：占位服务（你可以自行替换为实际 GPT-SoVITS）

## 1. 前置条件

- 已安装 Docker 与 Docker Compose（`docker compose version` 能输出版本）
- Linux/Ubuntu 推荐（Windows/macOS 也可，但路径/代理略有差异）

## 2. 克隆代码

```bash
git clone https://github.com/xiaobendaoke/xiao_a.git
cd xiao_a
```

## 3. 配置环境变量（必做）

本项目 `nonebot` 会读取 `./bot/.env`。

```bash
cp bot/.env.example bot/.env
```

然后编辑 `bot/.env`，至少配置一个 LLM Key（`SILICONFLOW_*` / `DEEPSEEK_*` / `OPENAI_API_KEY` 三选一）。

安全提示：
- 不要把真实 Key 提交到 GitHub；公共仓库泄露后请立即作废并更换。

## 4. 一键启动（开箱即用）

在仓库根目录执行：

```bash
docker compose up -d --build
```

查看状态与日志：

```bash
docker compose ps
docker compose logs -f nonebot
docker compose logs -f napcat
```

端口（默认）：
- NapCat WebUI：`http://localhost:6099`
- NoneBot：`http://localhost:8080`

## 5. 记忆/数据库（SQLite）

机器人“记忆/状态”默认存储在：
- `bot/plugins/companion_core/data.db`

`docker-compose.yml` 已把该文件单独挂载进容器（重建容器不会丢数据）。建议把它当作运行时数据做备份，而不是长期提交到 Git 历史里。

## 6. 使用 Docker Hub 镜像运行（可选）

你也可以不本地构建，直接拉取镜像（你发布到 Docker Hub 的 `latest`）：

```bash
docker pull xiaobendaoke/xiao_a:latest
NONEBOT_IMAGE=xiaobendaoke/xiao_a:latest docker compose up -d --no-build nonebot
```

更新镜像：

```bash
docker pull xiaobendaoke/xiao_a:latest
NONEBOT_IMAGE=xiaobendaoke/xiao_a:latest docker compose up -d --no-build nonebot
```

## 7. 常见问题

### 7.1 Docker Hub 推送/登录网络失败

如果你在国内网络环境，需要让 Docker daemon 走代理（例如 Clash `127.0.0.1:7890`）。示例（Ubuntu/systemd）：

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d

sudo tee /etc/systemd/system/docker.service.d/proxy.conf >/dev/null <<'EOF'
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7890"
Environment="HTTPS_PROXY=http://127.0.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1,::1,host.docker.internal,napcat,nonebot,172.16.0.0/12,192.168.0.0/16,10.0.0.0/8"
EOF

sudo systemctl daemon-reload
sudo systemctl restart docker
```

### 7.2 `authentication required - access token has insufficient scopes`

你使用的 Docker Hub Token 权限不够。请创建带 `Read & Write` 权限的 PAT，或使用账号密码登录后再 `docker push`。

## 8. 语音对话（QQ 语音 → ASR → 文本 → TTS → QQ 语音）

当前仅支持私聊语音：你给小a发语音，小a会“听写→理解→语音回复”。

需要在 `bot/.env` 里配置：
- `DASHSCOPE_API_KEY`：百炼 DashScope Key（注意不要泄露）
- `DASHSCOPE_REGION`：`cn`（北京）或 `intl`（新加坡）
- `QWEN_TTS_VOICE`：你用 `scripts/qwen_voice_clone.py` 得到的 `output.voice`
- `QWEN_TTS_MODEL`：默认 `qwen3-tts-vc-realtime-2025-11-27`
- `DASHSCOPE_ASR_MODEL`：默认 `paraformer-realtime-v2`

可选：让语音更自然的参数（不填就用默认）
- `QWEN_TTS_SPEECH_RATE`：语速（0.5~2.0），例如 `0.95`
- `QWEN_TTS_PITCH_RATE`：音高（0.5~2.0），例如 `1.05`
- `QWEN_TTS_VOLUME`：音量（0~100），例如 `55`
- `QWEN_TTS_ENABLE_TN`：文本规范化（`1/0`）
- `QWEN_TTS_LANGUAGE_TYPE`：语种（如 `zh/en/auto`）

说明：语音回复会自动清理括号动作/旁白（如“（戳戳屏幕）”）再送进 TTS，避免读出这些内容。

改完后重启：
```bash
docker compose up -d --build nonebot
```
