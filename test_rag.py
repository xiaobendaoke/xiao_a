"""RAG 功能验证脚本
请在 NoneBot 容器内运行，或确保本地已安装 chromadb 和相关依赖。
运行方式：
  docker-compose exec bot python test_rag.py (推荐)
  python test_rag.py (如果本地环境就绪)

前置条件：
  必须配置了 SILICONFLOW_API_KEY (或 EMBEDDING_MODEL 对应平台的 Key)
"""
import sys
import os
import asyncio

# 添加项目根目录到 path，以便 import bot
sys.path.insert(0, os.getcwd())

# 简单 mock logger 避免报错
from nonebot import logger
logger.add(sys.stderr, level="INFO")

# 尝试导入 rag_core
try:
    from bot.plugins.companion_core import rag_core
except ImportError as e:
    print(f"Import Error: {e}")
    print("请确保已安装 chromadb (pip install chromadb>=0.4.0)")
    sys.exit(1)

async def main():
    print("=== 开始 RAG 功能测试 ===")
    
    # 1. 测试初始化
    print("\n[1] 初始化 ChromaDB...")
    coll = rag_core.get_collection()
    if not coll:
        print("❌ 初始化失败")
        return
    print(f"✅ 初始化成功，当前文档数: {coll.count()}")
    
    # 2. 测试写入 (Ingest)
    test_text = "小a最喜欢的饮料是草莓味的波子汽水。"
    print(f"\n[2] 尝试写入记忆: {test_text}")
    print("    正在调用 Embedding API (可能需要几秒)...")
    success = await rag_core.add_document(
        test_text, 
        metadata={"source": "test_script", "user_id": "test_user"}
    )
    if success:
        print("✅ 写入成功")
    else:
        print("❌ 写入失败 (请检查 API Key 和网络)")
        return

    # 3. 测试检索 (Retrieve)
    query = "小a喜欢喝什么？"
    print(f"\n[3] 尝试检索: {query}")
    results = await rag_core.search_documents(query, n_results=1)
    
    print("    检索结果:")
    if results:
        for idx, r in enumerate(results):
            print(f"    - [{idx+1}] {r}")
        
        if "草莓" in str(results):
            print("✅ 测试通过！成功找回记忆。")
        else:
            print("⚠️ 检索成功但内容不匹配 (可能 Embedding 模型语义偏差)")
    else:
        print("❌ 未检索到任何内容")

if __name__ == "__main__":
    asyncio.run(main())
