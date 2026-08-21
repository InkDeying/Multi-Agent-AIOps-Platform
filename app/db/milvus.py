"""Milvus 存储适配器与连接管理.

职责划分:
  - 本模块: 底层连接管理 + 健康检查 + Collection 元数据管理, 以及唯一的
    "MilvusClient 内部 alias 注册进 ORM 连接表" 入口 (``connect_orm_alias``);
  - harness/rag/vector_store.py: 高层向量操作 (用 langchain_milvus.Milvus 包装);
  - harness/rag/retrieval.py: Parent-Child context 构造与 RAG 检索编排.

为什么分两层?
  - 底层用 pymilvus, 提供精细控制 (健康检查、维度校验、强制重建)
  - 高层用 langchain_milvus, 与 LangChain 生态无缝衔接 (RAG / Retriever)
"""

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from pymilvus import Collection, MilvusClient, MilvusException, connections, utility

from app.config import settings
from app.exceptions import VectorStoreError

# Milvus 单次 query 的行数上限 (16384 是服务端默认 offset+limit 上限).
_CHUNK_QUERY_LIMIT = 16384


def milvus_uri() -> str:
    """MilvusClient 只接受 uri, 这里是唯一的拼接点."""
    return f"http://{settings.milvus_host}:{settings.milvus_port}"


def connect_orm_alias() -> Tuple[Any, str]:
    """新建 MilvusClient, 并把它内部的 alias 注册进 pymilvus ORM 连接表.

    背景: langchain_milvus 0.3+ 走 MilvusClient (新 API), 但它的
    ``_extract_fields()`` 内部又用 ``pymilvus.orm.Collection`` (旧 API)。
    两套 API 各有一份连接注册表, 不打通就会抛 ConnectionNotExistException。

    这段桥接原先在 ``vector_store.get_vector_store`` 和
    ``hybrid_retriever._load_all_chunks_from_milvus`` 各写了一遍, 现在只此一处。

    Returns:
        (client, alias): client 可直接用于新 API 调用, alias 用于 ORM Collection。
    """
    uri = milvus_uri()
    client = MilvusClient(uri=uri)
    alias = client._using
    if alias not in [c[0] for c in connections.list_connections()]:
        connections.connect(alias=alias, uri=uri)
    return client, alias


def get_collection(
    name: Optional[str] = None,
    *,
    load: bool = False,
) -> Collection:
    """获取 ORM Collection，统一处理 MilvusClient/ORM alias 桥接。"""
    _, alias = connect_orm_alias()
    collection = Collection(name or settings.milvus_collection, using=alias)
    if load:
        collection.load()
    return collection


def query_collection(
    *,
    expr: str,
    output_fields: List[str],
    limit: int = _CHUNK_QUERY_LIMIT,
    name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """执行 Collection 查询，隐藏底层 ORM 连接细节。"""
    collection = get_collection(name, load=True)
    return list(
        collection.query(
            expr=expr,
            output_fields=output_fields,
            limit=limit,
        )
    )


def delete_collection_expr(expr: str, *, name: Optional[str] = None) -> None:
    """按表达式删除 Collection 记录并 flush。"""
    collection = get_collection(name)
    collection.delete(expr=expr)
    collection.flush()


class MilvusManager:
    """Milvus 连接管理器 (单例).

    通过 lifespan 钩子在应用启动时 connect(), 关闭时 disconnect().

    Examples:
        # main.py lifespan
        milvus_manager.connect()
        ...
        milvus_manager.disconnect()

        # health check
        if not milvus_manager.is_alive():
            raise ServiceError("Milvus down")
    """

    DEFAULT_ALIAS = "default"

    def __init__(self) -> None:
        self._connected = False

    # ==================== 连接管理 ====================

    def connect(self) -> None:
        """建立 Milvus 连接 (幂等)."""
        if self._connected:
            logger.debug("Milvus 已连接, 跳过")
            return

        host = settings.milvus_host
        port = settings.milvus_port
        timeout = settings.milvus_timeout_ms / 1000

        logger.info(f"连接 Milvus: {host}:{port} (timeout={timeout}s)")
        try:
            connections.connect(
                alias=self.DEFAULT_ALIAS,
                host=host,
                port=str(port),
                timeout=timeout,
            )
            self._connected = True
            logger.info(f"Milvus 连接成功 | 已有 collections: {self.list_collections()}")
        except MilvusException as e:
            self._connected = False
            raise VectorStoreError(
                f"Milvus 连接失败 ({host}:{port}): {e}",
                detail={"host": host, "port": port},
            ) from e
        except Exception as e:
            self._connected = False
            raise VectorStoreError(
                f"Milvus 连接异常: {e}",
                detail={"host": host, "port": port},
            ) from e

    def disconnect(self) -> None:
        """断开连接 (幂等)."""
        if not self._connected:
            return
        try:
            connections.disconnect(self.DEFAULT_ALIAS)
            logger.info("Milvus 连接已断开")
        except Exception as e:
            logger.warning(f"断开 Milvus 失败 (忽略): {e}")
        finally:
            self._connected = False

    # ==================== 健康检查 ====================

    def is_alive(self) -> bool:
        """快速健康检查 (用于 readiness probe).

        Returns:
            bool: True = 连接活跃, False = 不可用
        """
        if not self._connected:
            return False
        try:
            # 试着获取连接地址, 失败说明连接已掉
            addr = connections.get_connection_addr(self.DEFAULT_ALIAS)
            return bool(addr)
        except Exception as e:
            logger.warning(f"Milvus health check 失败: {e}")
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ==================== Collection 管理 ====================

    def list_collections(self) -> List[str]:
        """列出所有 collection."""
        if not self._connected:
            return []
        try:
            return utility.list_collections(using=self.DEFAULT_ALIAS)
        except Exception as e:
            logger.warning(f"list_collections 失败: {e}")
            return []

    def has_collection(self, name: Optional[str] = None) -> bool:
        """检查 collection 是否存在."""
        col = name or settings.milvus_collection
        try:
            return utility.has_collection(collection_name=col, using=self.DEFAULT_ALIAS)
        except Exception as e:
            logger.warning(f"has_collection({col}) 失败: {e}")
            return False

    def drop_collection(self, name: Optional[str] = None) -> None:
        """删除 collection (危险操作!).

        会同时删除所有数据和索引, 不可恢复.
        通常仅在维度变更或开发期重置时使用.
        """
        col = name or settings.milvus_collection
        try:
            utility.drop_collection(col, using=self.DEFAULT_ALIAS)
            logger.warning(f"已删除 collection: {col}")
        except Exception as e:
            raise VectorStoreError(f"删除 collection 失败: {e}") from e


# ============================================================
# 知识库 collection 的元数据原语 (文档管理用)
# ============================================================

def count_chunks_by_source(collection: Optional[str] = None) -> Dict[str, int]:
    """按 source 聚合各文档的 chunk 数 (source -> chunk_count).

    走 ORM Collection (default alias, 由 milvus_manager.connect 注册)。
    查询失败返回空 dict, 调用方按"无文档"处理。
    """
    name = collection or settings.milvus_collection
    try:
        rows = query_collection(
            expr="pk >= 0",  # 全表, 只要 source 字段
            output_fields=["source"],
            limit=_CHUNK_QUERY_LIMIT,
            name=name,
        )
    except Exception as e:
        logger.warning(f"count_chunks_by_source({name}) 查询失败: {e}")
        return {}
    counter: Dict[str, int] = {}
    for row in rows:
        source = row.get("source") or "unknown"
        counter[source] = counter.get(source, 0) + 1
    return counter


def delete_chunks_by_source(source: str, collection: Optional[str] = None) -> int:
    """按 source 删除该文档的全部 chunks, 返回删除的 chunk 数.

    先 query 出主键再用 pk 列表删除 (比 expr 直接删更精确); 异常向上抛,
    由调用方包装为 VectorStoreError。
    """
    name = collection or settings.milvus_collection
    rows = query_collection(
        expr=f'source == "{source}"',
        output_fields=["pk"],
        limit=_CHUNK_QUERY_LIMIT,
        name=name,
    )
    if not rows:
        return 0
    pks = [r["pk"] for r in rows]
    delete_collection_expr(f"pk in {pks}", name=name)
    return len(pks)


# ============================================================
# 全局单例
# ============================================================
milvus_manager = MilvusManager()
