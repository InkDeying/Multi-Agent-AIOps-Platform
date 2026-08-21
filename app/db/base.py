"""裸 asyncpg Repository 层的共享底座.

在此之前, ``incidents`` / ``evidence`` / ``orchestration`` / approvals 四个
Repository 各自抄了一份 JSONB 解码器, 并且每个方法都重复
``pool = await get_pool()`` + ``async with pool.acquire()`` 这三行开场。

注意: 两份解码器**语义并不相同**, 这是行为差异而不是风格差异 ——
incidents 只在字符串以 ``[`` 或 ``{`` 开头时才尝试 ``json.loads``,
所以文本列里的 ``"123"`` 会保持字符串; 另外三个则会解析成整数 ``123``。
``strict_prefix`` 把两种语义都保留下来, 这样合并调用点时不会改变返回值。
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterable, Sequence

from app.db.postgres import get_pool


# ============================================================
# ID / 序列化 (原 db/utils.py)
# ============================================================
def json_dump(data: Any) -> str:
    """安全序列化为 JSONB 写入用的字符串.

    显式判 ``None``, 不写 ``data or {}`` —— 否则空 list ``[]``
    会被 falsy 化成 ``{}``, 破坏 JSONB 数组列。
    """
    if data is None:
        data = {}
    return json.dumps(data, ensure_ascii=False, default=str)


def new_id(prefix: str) -> str:
    """生成 ``<prefix>_<uuid4 hex>`` 形式的实体 id."""
    return f"{prefix}_{uuid.uuid4().hex}"


def loads_if_json(value: Any, *, strict_prefix: bool = False) -> Any:
    """解码 asyncpg 以 ``str`` 形式返回的 JSONB 列.

    参数:
        value: 列的原始值; 非字符串原样返回。
        strict_prefix: 只有看起来像 JSON 对象/数组时才尝试解码。
            incidents Repository 必须开启 —— 它的列混放 JSON 与纯文本。
    """
    if not isinstance(value, str):
        return value
    if strict_prefix and (not value or value[0] not in "[{"):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def row_to_dict(
    row: Any | None,
    json_keys: Sequence[str],
    *,
    strict_prefix: bool = False,
) -> dict[str, Any] | None:
    """把一条 asyncpg record 转成 dict, 并解码指定的 JSONB 列."""
    if row is None:
        return None
    item = dict(row)
    for key in json_keys:
        if key in item:
            item[key] = loads_if_json(item[key], strict_prefix=strict_prefix)
    return item


def rows_to_dicts(
    rows: Iterable[Any],
    json_keys: Sequence[str],
    *,
    strict_prefix: bool = False,
) -> list[dict[str, Any]]:
    """对结果集逐行做 ``row_to_dict``, 不丢弃任何一行."""
    return [
        row_to_dict(row, json_keys, strict_prefix=strict_prefix) or {} for row in rows
    ]


@asynccontextmanager
async def acquire() -> AsyncIterator[Any]:
    """从全局连接池借一条连接.

    多语句写入仍然在调用点显式写事务:
    ``async with acquire() as conn: async with conn.transaction():``,
    这样事务边界在 Repository 方法里依然是看得见的。
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn
