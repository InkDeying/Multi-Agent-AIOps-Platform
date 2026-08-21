"""LLM Wiki 文件存储路径。

路径定义属于存储适配层，Wiki ingest、召回和只读服务共享同一份定义。
"""

from pathlib import Path

WIKI_DIR = Path(__file__).resolve().parents[2] / "data" / "wiki"
WIKI_SERVICES_DIR = WIKI_DIR / "services"
WIKI_PATTERNS_DIR = WIKI_DIR / "patterns"
WIKI_INDEX_FILE = WIKI_DIR / "index.md"
WIKI_LOG_FILE = WIKI_DIR / "log.md"
WIKI_LOCK_FILE = WIKI_DIR / ".write.lock"
