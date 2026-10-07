"""
工具性能分析器 v1.0 — 自动学习各工具耗时 + 动态超时
───────────────────────────────────────────
特性:
  • 记录每个工具的每次运行耗时
  • 自动计算中位数/均值/P95
  • 动态调整timeout = P95 * 1.5
  • 识别慢工具 + 优化建议

用法:
  from .tool_profiler import ToolProfiler, profile_tool
  profiler = ToolProfiler()
  profiler.record("nmap", 45.2)
  p95 = profiler.get_timeout("nmap")  # → 动态超时
"""

import time, json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

PROFILE_FILE = Path("/opt/deepseek-bot/.tool_profiles.json")

# 默认超时（秒）
DEFAULT_TIMEOUTS = {
    "nmap":       600,
    "nuclei":     900,
    "sqlmap":     600,
    "ffuf":       300,
    "subfinder":  120,
    "amass":      300,
    "httpx":      120,
    "whatweb":    60,
    "wafw00f":    60,
    "nikto":      600,
    "wapiti":     600,
    "commix":     300,
    "xsstrike":   300,
    "gobuster":   300,
    "feroxbuster": 300,
    "dirsearch":  300,
    "dnsx":       120,
    "arjun":      300,
    "testssl":    300,
    "wpscan":     600,
    "default":    300,
}


class ToolProfiler:
    """工具性能分析器"""

    def __init__(self, profile_file: Path = PROFILE_FILE):
        self.profile_file = profile_file
        self.runtimes: Dict[str, List[float]] = defaultdict(list)
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        if self.profile_file.exists():
            try:
                data = json.loads(self.profile_file.read_text())
                for tool, times in data.items():
                    self.runtimes[tool] = times
            except Exception:
                pass
        self._loaded = True

    def _save(self):
        self.profile_file.write_text(json.dumps(
            {k: v[-100:] for k, v in self.runtimes.items()},  # 只保留最近100次
            ensure_ascii=False
        ))

    def record(self, tool: str, duration: float):
        """记录一次运行时间"""
        self._load()
        self.runtimes[tool].append(duration)
        # 只保留最近200次
        if len(self.runtimes[tool]) > 200:
            self.runtimes[tool] = self.runtimes[tool][-100:]
        self._save()

    def get_timeout(self, tool: str) -> int:
        """
        动态计算超时时间
        = P95 * 1.5，但不低于默认值
        """
        self._load()
        default = DEFAULT_TIMEOUTS.get(tool, DEFAULT_TIMEOUTS["default"])
        times = self.runtimes.get(tool, [])
        if len(times) < 5:
            return default  # 数据不足，用默认

        sorted_times = sorted(times)
        p95_idx = int(len(sorted_times) * 0.95)
        p95 = sorted_times[min(p95_idx, len(sorted_times) - 1)]

        dynamic = int(p95 * 1.5)
        return max(dynamic, default)

    def stats(self, tool: str = None) -> Dict:
        """获取性能统计"""
        self._load()
        if tool:
            return self._stats_for(tool)

        result = {}
        for t in sorted(self.runtimes.keys()):
            result[t] = self._stats_for(t)
        return result

    def _stats_for(self, tool: str) -> Dict:
        times = self.runtimes.get(tool, [])
        if not times:
            return {"count": 0, "avg": 0, "median": 0, "p95": 0, "min": 0, "max": 0}

        sorted_times = sorted(times)
        n = len(sorted_times)
        return {
            "count": n,
            "avg": round(sum(times) / n, 2),
            "median": round(sorted_times[n // 2], 2),
            "p95": round(sorted_times[int(n * 0.95)], 2),
            "min": round(sorted_times[0], 2),
            "max": round(sorted_times[-1], 2),
            "dynamic_timeout": self.get_timeout(tool),
        }

    def slowest_tools(self, top_n: int = 5) -> List[Tuple[str, float]]:
        """返回最慢的工具"""
        self._load()
        avgs = [(t, sum(ts)/len(ts)) for t, ts in self.runtimes.items() if ts]
        avgs.sort(key=lambda x: x[1], reverse=True)
        return avgs[:top_n]

    def recommendations(self) -> List[str]:
        """生成优化建议"""
        tips = []
        stats = self.stats()
        for tool, s in stats.items():
            if s["count"] < 2:
                continue
            if s["p95"] > DEFAULT_TIMEOUTS.get(tool, 300) * 0.8:
                tips.append(f"⚠️ {tool}: P95={s['p95']}s接近超时{DEFAULT_TIMEOUTS.get(tool, 300)}s，建议增大timeout")
            if s["count"] > 10 and s["avg"] > 60:
                tips.append(f"💡 {tool}: 平均{s['avg']}s较慢，考虑并行或限缩范围")
        if not tips:
            tips.append("✅ 所有工具性能正常")
        return tips


# ==================== 全局单例 ====================

_profiler: Optional[ToolProfiler] = None


def get_profiler() -> ToolProfiler:
    global _profiler
    if _profiler is None:
        _profiler = ToolProfiler()
    return _profiler


def profile_record(tool: str, duration: float):
    get_profiler().record(tool, duration)


def profile_timeout(tool: str) -> int:
    return get_profiler().get_timeout(tool)


def profile_stats(tool: str = None) -> Dict:
    return get_profiler().stats(tool)
