"""简易 Cron 调度器 - 零依赖"""
import time, threading, re
from datetime import datetime
from typing import Callable, Optional

from . import db

# cron 字段: min hour day month weekday
# 支持: * /N N 和逗号分隔

def _match_cron_field(value: str, current: int) -> bool:
    """匹配单个 cron 字段"""
    if value == "*":
        return True
    for part in value.split(","):
        part = part.strip()
        if not part: continue
        if part.startswith("*/"):
            step = int(part[2:])
            if current % step == 0:
                return True
        elif "-" in part:
            lo, hi = part.split("-")
            if int(lo) <= current <= int(hi):
                return True
        else:
            try:
                if int(part) == current:
                    return True
            except:
                pass
    return False


def cron_match(cron_expr: str, dt: datetime = None) -> bool:
    """检查 cron 表达式是否匹配当前时间"""
    if dt is None: dt = datetime.now()
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return False

    checks = [
        (parts[0], dt.minute),
        (parts[1], dt.hour),
        (parts[2], dt.day),
        (parts[3], dt.month),
        (parts[4], dt.isoweekday() % 7),  # 0=Sunday
    ]
    return all(_match_cron_field(field, cur) for field, cur in checks)


class CronScheduler:
    """简易调度器，每分钟检查一次"""

    def __init__(self):
        self._running = False
        self._thread = None
        self._callback: Optional[Callable] = None

    def set_callback(self, cb: Callable):
        """设置回调: cb(uid, project_id, action, target)"""
        self._callback = cb

    def start(self):
        if self._running: return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print("[Scheduler] 已启动")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                print(f"[Scheduler] 错误: {e}")
            time.sleep(60)  # 每分钟检查

    def _tick(self):
        now = datetime.now()
        schedules = db._get().execute(
            "SELECT * FROM schedules WHERE enabled=1"
        ).fetchall()

        for s in schedules:
            s = dict(s)
            if not cron_match(s["cron_expr"], now):
                continue

            # 避免一分钟内重复执行
            last = s.get("last_run")
            if last and (time.time() - last) < 120:
                continue

            print(f"[Scheduler] 触发: {s['name']} (uid={s['uid']}, action={s['action']})")
            db.schedule_update_run(s["id"])

            if self._callback:
                try:
                    self._callback(
                        uid=s["uid"],
                        project_id=s["project_id"],
                        action=s["action"],
                        target=s["target"],
                    )
                except Exception as e:
                    print(f"[Scheduler] 回调错误: {e}")


# 全局调度器实例
_scheduler = CronScheduler()

def start_scheduler(callback: Callable = None):
    if callback:
        _scheduler.set_callback(callback)
    _scheduler.start()

def stop_scheduler():
    _scheduler.stop()


def schedule_notify(uid: int, text: str):
    """调度器回调通知 - 这里做占位，实际由 bot 注入"""
    print(f"[Scheduler Notify] uid={uid}: {text[:200]}")
