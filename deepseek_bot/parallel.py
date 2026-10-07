"""
多目标并行调度引擎 v1.0
───────────────────────────────────────────
特性:
  • ThreadPoolExecutor 并发执行 playbook
  • 可配置并发数 (默认5, 最大20)
  • 单目标进度实时追踪
  • 结果聚合 + auto-verify 批量对接
  • 支持纯扫描模式 / 全流程模式
  • 失败自动隔离，单目标崩溃不影响其他

用法:
  from .parallel import ParallelScheduler, batch_playbook

  # 快捷调用
  results = batch_playbook(uid, project_ids, targets, mode="full")
"""

import time, json, os, threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from typing import Callable, Optional, List, Dict, Any

from .playbook import Playbook, run_full, run_recon, run_port_scan, run_web_scan, run_vuln_scan
from . import db

OUT = Path("/opt/deepseek-bot/playbook_output")

# ==================== 数据模型 ====================

@dataclass
class TargetJob:
    """单个目标的任务描述"""
    uid: int
    project_id: int
    target: str
    mode: str = "full"      # full/recon/ports/web/vuln
    phases: str = "all"     # 仅 full 模式有效
    project_name: str = ""

@dataclass
class JobResult:
    """单个目标的执行结果"""
    project_id: int
    target: str
    mode: str
    success: bool
    elapsed: float
    error: str = ""
    summary: Dict[str, Any] = field(default_factory=dict)
    findings_count: int = 0
    verified_vulns: int = 0

@dataclass
class BatchResult:
    """批量执行汇总"""
    total: int
    success: int
    failed: int
    elapsed_total: float
    jobs: List[JobResult] = field(default_factory=list)
    
    @property
    def success_rate(self) -> float:
        return self.success / max(self.total, 1) * 100
    
    def summary_table(self) -> str:
        """生成汇总表格"""
        lines = [
            f"总目标: {self.total} | 成功: {self.success} | 失败: {self.failed}",
            f"成功率: {self.success_rate:.0f}% | 总耗时: {self.elapsed_total:.0f}s",
            f"{'─'*60}",
            f"{'目标':<25} {'模式':<8} {'状态':<6} {'耗时':<8} {'发现':<6} {'已验证':<6}",
            f"{'─'*60}",
        ]
        for j in self.jobs:
            status = "✅" if j.success else "❌"
            lines.append(
                f"{j.target:<25} {j.mode:<8} {status:<6} {j.elapsed:.0f}s{'':<3} "
                f"{j.findings_count:<6} {j.verified_vulns:<6}"
            )
        return "\n".join(lines)


# ==================== 进度追踪器 ====================

class ProgressTracker:
    """全局进度追踪，所有线程共享"""
    
    def __init__(self, total: int):
        self.total = total
        self.completed = 0
        self._lock = threading.Lock()
        self._progress: Dict[int, Dict] = {}  # project_id → {phase, status, msg}
        self._callbacks: List[Callable] = []
    
    def update(self, project_id: int, phase: str, status: str, msg: str = ""):
        with self._lock:
            self._progress[project_id] = {
                "phase": phase, "status": status, "msg": msg, "ts": time.time()
            }
    
    def mark_done(self, project_id: int):
        with self._lock:
            self.completed += 1
    
    @property
    def snapshot(self) -> Dict:
        with self._lock:
            return {
                "completed": self.completed,
                "total": self.total,
                "progress": dict(self._progress)
            }


# ==================== 并行调度器 ====================

class ParallelScheduler:
    """多目标并行调度器 — 纯串行换并发，无其他依赖"""
    
    MODES = {
        "full":     run_full,
        "recon":    run_recon,
        "ports":    run_port_scan,
        "web":      run_web_scan,
        "vuln":     run_vuln_scan,
    }
    
    def __init__(self, max_workers: int = 5, progress_callback: Optional[Callable] = None):
        """
        Args:
            max_workers: 最大并发数 (1-20, 默认5)
            progress_callback: 全局进度回调 fn(phase, target, status, msg)
        """
        self.max_workers = min(max(max_workers, 1), 20)
        self.progress_callback = progress_callback or (lambda *a: None)
    
    def run(self, jobs: List[TargetJob]) -> BatchResult:
        """
        并行执行多个目标。
        
        Args:
            jobs: 目标列表
        
        Returns:
            BatchResult: 汇总结果
        """
        t0 = time.time()
        tracker = ProgressTracker(len(jobs))
        results: List[JobResult] = []
        
        self.progress_callback("batch", f"{len(jobs)}个目标", "running", 
                               f"并行度={self.max_workers}")
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_map: Dict[Future, TargetJob] = {}
            
            for job in jobs:
                f = executor.submit(self._run_one, job, tracker)
                future_map[f] = job
            
            for future in as_completed(future_map):
                job = future_map[future]
                try:
                    result = future.result(timeout=3600)  # 单目标最多1小时
                    results.append(result)
                except Exception as e:
                    results.append(JobResult(
                        project_id=job.project_id,
                        target=job.target,
                        mode=job.mode,
                        success=False,
                        elapsed=0,
                        error=str(e)
                    ))
        
        elapsed = time.time() - t0
        
        batch = BatchResult(
            total=len(jobs),
            success=sum(1 for r in results if r.success),
            failed=sum(1 for r in results if not r.success),
            elapsed_total=elapsed,
            jobs=results
        )
        
        self.progress_callback("batch", f"{batch.success}/{batch.total}", "done",
                               f"耗时{elapsed:.0f}s 成功率{batch.success_rate:.0f}%")
        
        return batch
    
    def _run_one(self, job: TargetJob, tracker: ProgressTracker) -> JobResult:
        """执行单个目标的 playbook（在独立线程中运行）"""
        t0 = time.time()
        
        def local_cb(tool: str, target: str, status: str, msg: str):
            tracker.update(job.project_id, tool, status, msg)
        
        try:
            mode_fn = self.MODES.get(job.mode)
            if not mode_fn:
                raise ValueError(f"未知模式: {job.mode}")
            
            tracker.update(job.project_id, "init", "running", f"{job.mode}:{job.target}")
            
            if job.mode == "full":
                result = mode_fn(job.uid, job.project_id, job.target, job.phases, local_cb)
            else:
                result = mode_fn(job.uid, job.project_id, job.target, local_cb)
            
            elapsed = time.time() - t0
            
            # 提取发现数
            findings = db.finding_list(job.project_id)
            
            # 提取 auto-verify 结果
            av_file = OUT / f"av_{job.project_id}.json"
            verified = 0
            if av_file.exists():
                try:
                    av_data = json.loads(av_file.read_text())
                    verified = av_data.get("verified", 0)
                except (json.JSONDecodeError, KeyError):
                    pass
            
            tracker.mark_done(job.project_id)
            
            return JobResult(
                project_id=job.project_id,
                target=job.target,
                mode=job.mode,
                success=True,
                elapsed=elapsed,
                summary={"tools_ran": list(result.keys())},
                findings_count=len(findings),
                verified_vulns=verified,
            )
            
        except Exception as e:
            elapsed = time.time() - t0
            tracker.mark_done(job.project_id)
            tracker.update(job.project_id, "error", "error", str(e))
            
            return JobResult(
                project_id=job.project_id,
                target=job.target,
                mode=job.mode,
                success=False,
                elapsed=elapsed,
                error=str(e)[:200],
            )


# ==================== 便捷函数 ====================

def batch_playbook(
    uid: int,
    targets: List[Dict[str, Any]],
    mode: str = "full",
    phases: str = "all",
    max_workers: int = 5,
    progress_callback: Optional[Callable] = None,
) -> BatchResult:
    """
    批量并行执行 playbook。
    
    Args:
        uid: 用户ID
        targets: [
            {"project_id": 5, "target": "example.com"},
            {"project_id": 6, "target": "test.com", "mode": "recon"},
        ]
        mode: 默认模式 (full/recon/ports/web/vuln)
        phases: full模式下的阶段
        max_workers: 最大并发数
        progress_callback: 进度回调
    
    Returns:
        BatchResult
    """
    jobs = []
    for t in targets:
        jobs.append(TargetJob(
            uid=uid,
            project_id=t["project_id"],
            target=t["target"],
            mode=t.get("mode", mode),
            phases=t.get("phases", phases),
            project_name=t.get("name", t["target"]),
        ))
    
    scheduler = ParallelScheduler(
        max_workers=max_workers,
        progress_callback=progress_callback
    )
    
    return scheduler.run(jobs)


def batch_from_project_ids(
    uid: int,
    project_ids: List[int],
    mode: str = "full",
    max_workers: int = 5,
    progress_callback: Optional[Callable] = None,
) -> BatchResult:
    """
    从项目ID列表批量执行（自动查询项目target）。
    
    Args:
        uid: 用户ID
        project_ids: 项目ID列表
        mode: 默认模式
        max_workers: 并发数
        progress_callback: 进度回调
    """
    targets = []
    all_projects = {p["id"]: p for p in db.project_list(uid)}
    for pid in project_ids:
        proj = all_projects.get(pid)
        if proj and proj.get("target"):
            targets.append({
                "project_id": pid,
                "target": proj["target"],
                "name": proj.get("name", proj["target"]),
                "mode": mode,
            })
    
    if not targets:
        raise ValueError("没有找到有效的项目/目标")
    
    return batch_playbook(uid, targets, mode, max_workers=max_workers,
                          progress_callback=progress_callback)
