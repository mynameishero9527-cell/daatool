"""进程内 TTL 缓存中间件。

规则：所有写路径「先写数据库、后刷缓存」；读路径缓存未命中时回源。
提供命中率统计，供设置页展示。
"""
import threading
import time
from typing import Any, Callable, Optional


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                self.misses += 1
                return None
            expire_at, value = item
            if expire_at < time.time():
                del self._store[key]
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._store[key] = (time.time() + ttl, value)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        with self._lock:
            total = self.hits + self.misses
            return {
                "entries": len(self._store),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else 0.0,
            }


cache = TTLCache()


def cached(key: str, ttl: float, loader: Callable[[], Any]) -> Any:
    """读缓存，未命中执行 loader 回源并写入缓存。loader 返回 None 时不缓存。"""
    value = cache.get(key)
    if value is not None:
        return value
    value = loader()
    if value is not None:
        cache.set(key, value, ttl)
    return value
