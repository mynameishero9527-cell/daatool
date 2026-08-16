"""数据源基础设施：HTTP 会话（连接复用）、健康度统计、熔断与故障切换。"""
import threading
import time
from typing import Callable, Optional

import httpx

from ..config import (
    CIRCUIT_FAIL_THRESHOLD,
    CIRCUIT_OPEN_SECONDS,
    HTTP_TIMEOUT,
)

_client: Optional[httpx.Client] = None
_client_lock = threading.Lock()


def http_client() -> httpx.Client:
    """全局 HTTP 客户端：keep-alive 连接池 + gzip。"""
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                timeout=HTTP_TIMEOUT,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                    "Accept-Encoding": "gzip, deflate",
                },
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                follow_redirects=True,
            )
        return _client


class SourceHealth:
    """单个数据源的健康度：成功率、平均延迟、熔断状态。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.enabled = True
        self.success = 0
        self.failure = 0
        self.consecutive_failures = 0
        self.total_latency_ms = 0.0
        self.circuit_open_until = 0.0
        self.last_error = ""
        self._lock = threading.Lock()

    def available(self) -> bool:
        with self._lock:
            return self.enabled and time.time() >= self.circuit_open_until

    def record_success(self, latency_ms: float) -> None:
        with self._lock:
            self.success += 1
            self.consecutive_failures = 0
            self.total_latency_ms += latency_ms

    def record_failure(self, err: str) -> None:
        with self._lock:
            self.failure += 1
            self.consecutive_failures += 1
            self.last_error = err[:200]
            if self.consecutive_failures >= CIRCUIT_FAIL_THRESHOLD:
                self.circuit_open_until = time.time() + CIRCUIT_OPEN_SECONDS
                self.consecutive_failures = 0

    def stats(self) -> dict:
        with self._lock:
            total = self.success + self.failure
            return {
                "name": self.name,
                "enabled": self.enabled,
                "success": self.success,
                "failure": self.failure,
                "success_rate": round(self.success / total, 4) if total else None,
                "avg_latency_ms": round(self.total_latency_ms / self.success, 1) if self.success else None,
                "circuit_open": time.time() < self.circuit_open_until,
                "last_error": self.last_error,
            }


HEALTH: dict[str, SourceHealth] = {}


def get_health(name: str) -> SourceHealth:
    if name not in HEALTH:
        HEALTH[name] = SourceHealth(name)
    return HEALTH[name]


def tracked_get(source: str, url: str, **kwargs) -> httpx.Response:
    """带健康度统计的 GET 请求。"""
    health = get_health(source)
    start = time.time()
    try:
        resp = http_client().get(url, **kwargs)
        resp.raise_for_status()
        health.record_success((time.time() - start) * 1000)
        return resp
    except Exception as exc:
        health.record_failure(str(exc))
        raise


def with_failover(sources: list[tuple[str, Callable]], context: str = ""):
    """按优先级依次尝试数据源，全部失败抛出最后一个异常。

    sources: [(源名, 无参调用函数)]，跳过熔断/禁用的源。
    """
    last_exc: Exception | None = None
    for name, fn in sources:
        if not get_health(name).available():
            continue
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - 故障切换需捕获一切异常
            last_exc = exc
    if last_exc:
        raise last_exc
    raise RuntimeError(f"无可用数据源: {context}")
