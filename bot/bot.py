"""NoneBot 启动入口（运行主进程）。

职责：
- 初始化 NoneBot 框架与驱动（`nonebot.init()`）。
- 注册 OneBot v11 适配器（用于对接 QQ/OneBot 协议端）。
- 加载 `nonebot_plugin_apscheduler`（为定时任务/主动互动提供调度器）。
- 扫描并加载 `plugins/` 下的插件模块（本项目主要是 `companion_core`）。

副作用：
- 导入并启动时会触发插件的模块导入（插件可能在 import 时注册 handler / scheduler）。
"""

import os
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11_Adapter

# 某些环境会在 env_file 里写 `HTTP_PROXY=`（空字符串），这会导致 httpx 认为启用了代理，
# 但代理地址为空，从而出现所有外网请求超时且异常信息为空（如 `ConnectTimeout('')`）。
for _k in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
):
    if _k in os.environ and not (os.environ.get(_k) or "").strip():
        os.environ.pop(_k, None)

# 1. 初始化 NoneBot
nonebot.init()

# 2. 注册适配器
driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11_Adapter)

# 2.1 加载定时任务插件（主动互动/定时任务）
nonebot.require("nonebot_plugin_apscheduler")

# 3. 加载插件 (关键！)
# 这行会告诉 NoneBot 去 plugins 文件夹下找你写的 companion_core
nonebot.load_plugins("plugins")

if __name__ == "__main__":
    nonebot.run()
