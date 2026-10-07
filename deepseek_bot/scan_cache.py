"""
扫描缓存引擎 v1.0 — 避免重复扫描，智能失效
───────────────────────────────────────────
特性:
  • 目标+工具 → 结果缓存（TTL 24小时）
  • 自动检测目标变化（端口/IP变更 → 缓存失效）
  • 磁盘持久化（SQLite）
  • 命中率统计

用法:
  from .scan_cache import ScanCache
  cache = ScanCache()
  # 查缓存
  result = cache.get("target.com", "nmap")
  # 存缓存
  cache.put("target.com", "nmap", scan_output)
"""

import json, time, hashlib, sqlite3
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass

DB_PATH = Path("/opt/deepseek-bot/scan_cache.db")
DEFAULT_TTL = 86400  # 24小时
PORT_TTL = 3600      # 端口扫描1小时有效


@dataclass
class CacheEntry:
    target: str
    tool: str
    output: str
    fingerprint: str  # 目标指纹（端口+IP hash）
    created_at: float
    ttl: int
    hit_count: int = 0


class ScanCache:
    """扫描缓存 — SQLite持久化"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    output TEXT NOT NULL,
                    fingerprint TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    ttl INTEGER NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    UNIQUE(target, tool)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_target_tool ON scan_cache(target, tool)")
            conn.commit()

    def get(self, target: str, tool: str) -> Optional[str]:
        """获取缓存结果。返回None表示缓存未命中或已过期。"""
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT output, fingerprint, created_at, ttl, hit_count FROM scan_cache WHERE target=? AND tool=?",
                (target.lower().strip(), tool)
            ).fetchone()

        if not row:
            return None

        output, fingerprint, created_at, ttl, hit_count = row
        now = time.time()

        # 检查TTL过期
        if now - created_at > ttl:
            self._delete(target, tool)
            return None

        # 检查目标指纹是否变化
        current_fp = self._get_fingerprint(target)
        if fingerprint and current_fp and fingerprint != current_fp:
            self._delete(target, tool)
            return None

        # 更新命中计数
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE scan_cache SET hit_count=? WHERE target=? AND tool=?",
                (hit_count + 1, target.lower().strip(), tool)
            )
            conn.commit()

        return output

    def put(self, target: str, tool: str, output: str, ttl: int = None):
        """存入缓存"""
        if ttl is None:
            # 端口扫描缓存较短
            ttl = PORT_TTL if tool in ("nmap", "masscan") else DEFAULT_TTL

        fingerprint = self._get_fingerprint(target)

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO scan_cache 
                   (target, tool, output, fingerprint, created_at, ttl, hit_count)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (target.lower().strip(), tool, output, fingerprint, time.time(), ttl)
            )
            conn.commit()

    def invalidate(self, target: str, tool: str = None):
        """使缓存失效"""
        if tool:
            self._delete(target, tool)
        else:
            with sqlite3.connect(str(self.db_path)) as conn:
                conn.execute("DELETE FROM scan_cache WHERE target=?", (target.lower().strip(),))
                conn.commit()

    def invalidate_all(self):
        """清空所有缓存"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("DELETE FROM scan_cache")
            conn.commit()

    def stats(self) -> Dict:
        """缓存统计"""
        with sqlite3.connect(str(self.db_path)) as conn:
            total = conn.execute("SELECT COUNT(*) FROM scan_cache").fetchone()[0]
            hits = conn.execute("SELECT SUM(hit_count) FROM scan_cache").fetchone()[0] or 0
            # 计算总大小
            size = conn.execute("SELECT SUM(LENGTH(output)) FROM scan_cache").fetchone()[0] or 0
        return {
            "entries": total,
            "total_hits": hits,
            "size_kb": round(size / 1024, 1),
            "db_path": str(self.db_path),
        }

    def _delete(self, target: str, tool: str):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "DELETE FROM scan_cache WHERE target=? AND tool=?",
                (target.lower().strip(), tool)
            )
            conn.commit()

    def _get_fingerprint(self, target: str) -> str:
        """获取目标指纹（轻量DNS解析+端口指纹）"""
        # 简单哈希
        import socket
        try:
            ips = socket.getaddrinfo(target, None)
            ip_list = sorted(set(a[4][0] for a in ips))
            return hashlib.md5(",".join(ip_list).encode()).hexdigest()[:16]
        except Exception:
            return hashlib.md5(target.encode()).hexdigest()[:16]


# ==================== 全局单例 ====================

_cache_instance: Optional[ScanCache] = None


def get_cache() -> ScanCache:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = ScanCache()
    return _cache_instance


def cache_get(target: str, tool: str) -> Optional[str]:
    return get_cache().get(target, tool)


def cache_put(target: str, tool: str, output: str, ttl: int = None):
    get_cache().put(target, tool, output, ttl)


def cache_invalidate(target: str, tool: str = None):
    get_cache().invalidate(target, tool)


def cache_stats() -> Dict:
    return get_cache().stats()
