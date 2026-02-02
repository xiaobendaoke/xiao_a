"""RAG Core Module - 长期记忆与知识库管理
封装 ChromaDB 操作，提供语义存储和检索能力。
"""
import os
import time
import uuid
import chromadb
from chromadb.config import Settings
from nonebot import logger
from .llm_client import get_text_embedding

# 设置 ChromaDB 存储路径
DB_DIR = os.path.join(os.path.dirname(__file__), "chroma_db")
COLLECTION_NAME = "xiaaa_memories"

_client = None
_collection = None

def get_collection():
    global _client, _collection
    if _collection:
        return _collection
    
    try:
        if not os.path.exists(DB_DIR):
            os.makedirs(DB_DIR, exist_ok=True)
            
        _client = chromadb.PersistentClient(path=DB_DIR)
        
        # 获取或创建集合
        # 这里的 metadata 是 collection 级别的配置
        _collection = _client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}  # 使用余弦相似度
        )
        logger.info(f"[RAG] ChromaDB init success, path={DB_DIR}, items={_collection.count()}")
        return _collection
    except Exception as e:
        logger.error(f"[RAG] ChromaDB init failed: {e}")
        return None

async def add_document(text: str, metadata: dict = None) -> bool:
    """添加文档/记忆片段到向量库
    
    Args:
        text: 文本内容
        metadata: 元数据，如 {"source": "chat", "user_id": "123", "type": "memory"}
    """
    if not text or not text.strip():
        return False
        
    coll = get_collection()
    if not coll:
        return False

    try:
        # 1. 获取向量
        embedding = await get_text_embedding(text)
        if not embedding:
            logger.warning("[RAG] Failed to get embedding for text")
            return False
            
        # 2. 生成 ID 和完整 metadata
        doc_id = str(uuid.uuid4())
        meta = metadata or {}
        meta["timestamp"] = int(time.time())
        meta["created_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        
        # 3. 存入 ChromaDB
        coll.add(
            documents=[text],
            embeddings=[embedding],
            metadatas=[meta],
            ids=[doc_id]
        )
        logger.debug(f"[RAG] Added doc: {text[:20]}...")
        return True
    except Exception as e:
        logger.error(f"[RAG] Add doc failed: {e}")
        return False

async def search_documents(query: str, n_results: int = 3, filter_meta: dict = None) -> list[str]:
    """语义搜索
    
    Args:
        query: 查询语句
        n_results: 返回条数
        filter_meta: 元数据过滤条件 (ChromaDB where 语法)
    """
    if not query or not query.strip():
        return []
        
    coll = get_collection()
    if not coll:
        return []

    try:
        # 1. 查询词向量化
        embedding = await get_text_embedding(query)
        if not embedding:
            return []
            
        # 2. 检索
        results = coll.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=filter_meta  # 例如 {"user_id": "123"}
        )
        
        # results['documents'] 是 list of list
        docs = results.get("documents", [])
        if not docs or not docs[0]:
            return []
            
        return docs[0]
    except Exception as e:
        logger.error(f"[RAG] Search failed: {e}")
        return []

async def count_documents() -> int:
    coll = get_collection()
    return coll.count() if coll else 0
