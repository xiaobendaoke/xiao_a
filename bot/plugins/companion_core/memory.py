"""记忆层薄封装（对话历史读写）。

这里保持“最薄一层”：
- `get_chat_history()`：读取最近 N 条历史（当前固定为 10 条）。
- `add_memory()`：追加一条 user/assistant 消息到持久化存储。

底层实现完全委托给 `db.py`（SQLite 表 `chat_history`）。
"""

from .db import save_chat, load_chats

def get_chat_history(user_id: str):
    # 直接从数据库获取最近 10 条
    return load_chats(user_id, limit=10)

def add_memory(user_id: str, role: str, content: str):
    # 直接存入数据库
    save_chat(user_id, role, content)
