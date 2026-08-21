"""LLM Wiki 的跨进程写锁.

锁分两层:
  - ``asyncio.Lock`` 串行化当前 Python 进程内的并发诊断;
  - 文件锁串行化多个 API / Worker 进程对同一 Wiki 目录的写入。

Windows 的 ``msvcrt.locking`` 锁字节区间, 因此锁文件必须至少保留 1 个字节;
macOS / Linux 则使用 ``fcntl.flock``。获取锁的阻塞操作放进线程,
避免卡住 asyncio 事件循环。
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, TextIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_FILE_LOCK_TIMEOUT_SEC = 30.0
_write_lock = asyncio.Lock()


def _acquire_file_lock(handle: TextIO) -> None:
    """获取与当前操作系统兼容的排他文件锁."""
    if os.name != "nt":
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return

    # msvcrt 锁定字节区间。锁文件至少保留 1 个字节; 其他进程持锁时,
    # LK_NBLCK 会立即失败, 因此这里按 100ms 间隔重试直到超时。
    handle.seek(0)
    if not handle.read(1):
        handle.seek(0)
        handle.write("0")
        handle.flush()

    deadline = time.monotonic() + _FILE_LOCK_TIMEOUT_SEC
    while True:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError("等待 LLM Wiki 写锁超时")
            time.sleep(0.1)


def _release_file_lock(handle: TextIO) -> None:
    """释放由 ``_acquire_file_lock`` 获取的锁."""
    if os.name != "nt":
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return

    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


@asynccontextmanager
async def wiki_write_guard(wiki_dir: Path, lock_file: Path) -> AsyncIterator[None]:
    """跨 API 进程和 Worker 串行化 Wiki 的读取、合并与写入."""
    handle: TextIO | None = None
    async with _write_lock:
        try:
            wiki_dir.mkdir(parents=True, exist_ok=True)
            handle = await asyncio.to_thread(lock_file.open, "a+", encoding="utf-8")
            await asyncio.to_thread(_acquire_file_lock, handle)
            yield
        finally:
            if handle is not None:
                try:
                    await asyncio.to_thread(_release_file_lock, handle)
                finally:
                    handle.close()
