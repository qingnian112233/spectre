"""
xAI Grok Build 断路器模块 (Rust → Python 移植)
来源: github.com/xai-org/grok-build/crates/common/xai-circuit-breaker

滑动窗口 + 最小样本数算法:
  断路器在 sample_count >= min_samples AND error_rate >= error_rate_threshold 时跳闸
"""

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Callable


# ── 状态枚举 ──

class BreakerState(IntEnum):
    Closed = 0
    Open = 1
    HalfOpen = 2


class Outcome(IntEnum):
    Success = 0
    Failure = 1


class Disposition(IntEnum):
    Retryable = 0
    AuthRefresh = 1
    Terminal = 2


class BreakerOpen(Exception):
    """断路器开启时抛出"""
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"circuit breaker open; retry after {retry_after:.1f}s")


# ── 滑动窗口 ──

MAX_WINDOW_ENTRIES = 10_000


class SlidingWindow:
    """有界滑动窗口, O(1) 错误率计算"""

    def __init__(self):
        self.entries: deque[tuple[float, bool]] = deque()
        self.failures: int = 0

    def push(self, is_failure: bool, timestamp: float = None):
        if timestamp is None:
            timestamp = time.monotonic()

        if len(self.entries) >= MAX_WINDOW_ENTRIES:
            _, was_failure = self.entries.popleft()
            if was_failure:
                self.failures -= 1

        self.entries.append((timestamp, is_failure))
        if is_failure:
            self.failures += 1

    def evict(self, window_secs: float, now: float = None):
        if now is None:
            now = time.monotonic()
        cutoff = now - window_secs

        while self.entries and self.entries[0][0] < cutoff:
            _, was_failure = self.entries.popleft()
            if was_failure:
                self.failures -= 1

    def error_rate(self) -> float:
        if not self.entries:
            return 0.0
        return self.failures / len(self.entries)

    def sample_count(self) -> int:
        return len(self.entries)

    def clear(self):
        self.entries.clear()
        self.failures = 0


# ── 断路器配置 ──

@dataclass
class BreakerConfig:
    window_duration: float = 60.0       # 窗口时长 (秒)
    min_samples: int = 10               # 最小样本数
    error_rate_threshold: float = 0.5   # 错误率阈值
    open_duration: float = 10.0         # 开路持续时长 (秒)
    half_open_max_probes: int = 1       # 半开最大探测数
    failure_codes: set[int] = field(default_factory=lambda: {429, 500, 502, 503, 504})
    enabled: bool = True

    @classmethod
    def server(cls) -> "BreakerConfig":
        """服务端预设: min_samples=10, error_rate=0.5, 60s窗口, 10s开路"""
        return cls(
            window_duration=60.0,
            min_samples=10,
            error_rate_threshold=0.5,
            open_duration=10.0,
            half_open_max_probes=1,
            failure_codes={429, 500, 502, 503, 504},
            enabled=True,
        )

    @classmethod
    def client(cls) -> "BreakerConfig":
        """客户端预设: min_samples=5, error_rate=0.5, 60s窗口, 60s开路, 只看401"""
        return cls(
            window_duration=60.0,
            min_samples=5,
            error_rate_threshold=0.5,
            open_duration=60.0,
            half_open_max_probes=1,
            failure_codes={401},
            enabled=True,
        )


# ── 重试策略 ──

class RetryPolicy:
    """HTTP 状态码 → 重试策略"""

    def __init__(
        self,
        retryable: list[int] = None,
        auth_refresh: list[int] = None,
        terminal: list[int] = None,
        default: Disposition = Disposition.Terminal,
    ):
        self.retryable = set(retryable or [])
        self.auth_refresh = set(auth_refresh or [])
        self.terminal = set(terminal or [])
        self.default = default

    @classmethod
    def server(cls) -> "RetryPolicy":
        """服务端: 429 + 5xx 可重试, 其余终止"""
        return cls(
            retryable=[429],
            default=Disposition.Terminal,
        )

    @classmethod
    def client_storage(cls) -> "RetryPolicy":
        """客户端存储: 400/403/404 终止, 401 刷新鉴权, 其余重试"""
        return cls(
            auth_refresh=[401],
            terminal=[400, 403, 404],
            default=Disposition.Retryable,
        )

    def classify(self, status: int) -> Optional[Disposition]:
        if 200 <= status < 300:
            return None  # 成功
        if status in self.auth_refresh:
            return Disposition.AuthRefresh
        if status in self.terminal:
            return Disposition.Terminal
        if status in self.retryable or 500 <= status < 600:
            return Disposition.Retryable
        return self.default

    def should_retry(self, status: int) -> bool:
        return self.classify(status) == Disposition.Retryable


# ── 断路器核心 ──

class CircuitBreaker:
    """线程安全断路器: 滑动窗口 + 三态机"""

    def __init__(self, config: BreakerConfig = None):
        self.config = config or BreakerConfig.server()
        self.config.half_open_max_probes = max(1, self.config.half_open_max_probes)

        self._lock = threading.Lock()
        self._state = BreakerState.Closed
        self._baseline = time.monotonic()
        self._opened_at: float = 0.0       # 相对 baseline 的偏移
        self._half_open_probes: int = 0
        self._probe_claimed_at: float = 0.0
        self._window = SlidingWindow()
        self._observer: Optional[Callable] = None

    # ── 快速路径: lock-free is_open() ──
    def is_open(self) -> bool:
        return self._state == BreakerState.Open

    def state(self) -> BreakerState:
        with self._lock:
            return self._state

    def check(self):
        """
        请求前调用. 如果断路器开路则抛出 BreakerOpen,
        如果半开且探测槽已满也抛出.
        成功则返回 (允许放行).
        """
        if not self.config.enabled:
            return

        with self._lock:
            self._transition_state()
            state = self._state

            if state == BreakerState.Closed:
                return

            if state == BreakerState.Open:
                elapsed = (time.monotonic() - self._baseline) - self._opened_at
                retry_after = max(0.0, self.config.open_duration - elapsed)
                raise BreakerOpen(retry_after)

            # HalfOpen: 尝试获取探测槽
            if state == BreakerState.HalfOpen:
                self._abandon_stale_probe()
                if self._half_open_probes >= self.config.half_open_max_probes:
                    elapsed_since_open = (time.monotonic() - self._baseline) - self._opened_at
                    retry_after = max(0.0, self.config.open_duration - elapsed_since_open)
                    raise BreakerOpen(retry_after)

                self._half_open_probes += 1
                self._probe_claimed_at = time.monotonic() - self._baseline

    def record(self, outcome: Outcome):
        """请求完成后调用, 反馈成功/失败"""
        with self._lock:
            self._window.evict(self.config.window_duration)
            self._window.push(outcome == Outcome.Failure)

            if outcome == Outcome.Success and self._state == BreakerState.HalfOpen:
                self._window.clear()
                self._half_open_probes = 0
                self._set_state_locked(BreakerState.Closed)
                return

            if outcome == Outcome.Failure and self._state == BreakerState.HalfOpen:
                self._half_open_probes = 0
                self._open_circuit_locked()
                return

    def record_http(self, status_code: int):
        """便捷方法: 根据 HTTP 状态码自动判断 Outcome"""
        if 200 <= status_code < 300:
            self.record(Outcome.Success)
        else:
            self.record(Outcome.Failure)

    def force_half_open(self):
        """(测试用) 强制进入半开状态"""
        with self._lock:
            self._window.clear()
            self._half_open_probes = 0
            self._probe_claimed_at = 0.0
            self._set_state_locked(BreakerState.HalfOpen)
            if self._observer:
                self._observer("forced_half_open")

    def stats(self) -> dict:
        """返回断路器统计信息"""
        with self._lock:
            self._window.evict(self.config.window_duration)
            return {
                "state": self._state.name,
                "sample_count": self._window.sample_count(),
                "error_rate": round(self._window.error_rate(), 4),
                "half_open_probes": self._half_open_probes,
                "opened_seconds_ago": (
                    (time.monotonic() - self._baseline) - self._opened_at
                    if self._state == BreakerState.Open
                    else None
                ),
            }

    # ── 内部状态转换 ──

    def _transition_state(self):
        """基于窗口数据决定状态转换"""
        now = time.monotonic()
        state = self._state

        if state == BreakerState.Open:
            elapsed = (now - self._baseline) - self._opened_at
            if elapsed >= self.config.open_duration:
                self._window.clear()
                self._half_open_probes = 0
                self._probe_claimed_at = 0.0
                self._set_state_locked(BreakerState.HalfOpen)
                if self._observer:
                    self._observer("open→half_open")
            return

        if state == BreakerState.Closed:
            self._window.evict(self.config.window_duration)
            sc = self._window.sample_count()
            er = self._window.error_rate()
            if sc >= self.config.min_samples and er >= self.config.error_rate_threshold:
                self._open_circuit_locked()
                if self._observer:
                    self._observer(f"closed→open (samples={sc}, error_rate={er:.3f})")

    def _open_circuit_locked(self):
        self._set_state_locked(BreakerState.Open)
        self._opened_at = time.monotonic() - self._baseline
        self._half_open_probes = 0

    def _set_state_locked(self, new_state: BreakerState):
        self._state = new_state

    def _abandon_stale_probe(self):
        """回收超时的探测槽"""
        if self._half_open_probes == 0:
            return
        elapsed = (time.monotonic() - self._baseline) - self._probe_claimed_at
        if elapsed >= self.config.open_duration and self._half_open_probes > 0:
            self._half_open_probes -= 1

    def on_state_change(self, callback: Callable):
        """注册状态变化观察者"""
        self._observer = callback
