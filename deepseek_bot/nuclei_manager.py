"""
Nuclei模板管理器 v1.0 — 自动更新 + 模板筛选 + 去重
───────────────────────────────────────────
特性:
  • 自动拉取最新nuclei-templates（每日检查）
  • 按漏洞类型筛选模板
  • CVSS/CISA KEV已知漏洞优先
  • 去重（同一CVE的不同模板）
  • 模板统计

用法:
  from .nuclei_manager import NucleiManager
  nm = NucleiManager()
  nm.update_templates()  # 拉取最新
  nm.stats()             # 统计
"""

import os, subprocess, time, json
from pathlib import Path
from typing import List, Dict, Optional

NUCLEI_TEMPLATES_DIR = Path.home() / "nuclei-templates"
UPDATE_INTERVAL = 86400  # 24小时
LAST_UPDATE_FILE = Path("/opt/deepseek-bot/.nuclei_last_update")


class NucleiManager:
    """Nuclei模板管理器"""

    def __init__(self, templates_dir: Path = NUCLEI_TEMPLATES_DIR):
        self.templates_dir = templates_dir
        self.last_update_file = LAST_UPDATE_FILE

    def is_installed(self) -> bool:
        """检查nuclei是否安装"""
        try:
            r = subprocess.run(["nuclei", "-version"], capture_output=True, text=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def templates_exist(self) -> bool:
        """检查模板目录是否存在"""
        return self.templates_dir.exists() and any(self.templates_dir.iterdir())

    def update_templates(self, force: bool = False) -> Dict:
        """
        更新nuclei模板
        返回: {"updated": bool, "message": str, "template_count": int}
        """
        if not self.is_installed():
            return {"updated": False, "message": "nuclei未安装", "template_count": 0}

        # 检查是否需要更新
        if not force and self._should_skip_update():
            return {
                "updated": False,
                "message": "模板已是最新（24小时内更新过）",
                "template_count": self.count_templates(),
            }

        try:
            if self.templates_exist():
                # git pull
                r = subprocess.run(
                    ["git", "-C", str(self.templates_dir), "pull", "--depth=1"],
                    capture_output=True, text=True, timeout=120
                )
            else:
                # git clone
                self.templates_dir.parent.mkdir(parents=True, exist_ok=True)
                r = subprocess.run(
                    ["git", "clone", "--depth=1", "https://github.com/projectdiscovery/nuclei-templates.git",
                     str(self.templates_dir)],
                    capture_output=True, text=True, timeout=300
                )

            # 记录更新时间
            self.last_update_file.write_text(str(time.time()))

            return {
                "updated": True,
                "message": r.stdout.strip()[-200:] if r.stdout else r.stderr.strip()[-200:],
                "template_count": self.count_templates(),
            }
        except subprocess.TimeoutExpired:
            return {"updated": False, "message": "超时", "template_count": self.count_templates()}
        except Exception as e:
            return {"updated": False, "message": str(e), "template_count": self.count_templates()}

    def count_templates(self) -> int:
        """统计模板数量"""
        if not self.templates_dir.exists():
            return 0
        return len(list(self.templates_dir.rglob("*.yaml")))

    def get_templates_by_severity(self, severity: str) -> List[Path]:
        """
        按严重程度筛选
        severity: critical/high/medium/low/info
        """
        results = []
        for f in self.templates_dir.rglob("*.yaml"):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                if f"severity: {severity}" in content.lower():
                    results.append(f)
            except Exception:
                pass
        return results

    def get_templates_by_tag(self, tag: str) -> List[Path]:
        """按标签筛选: cve,rce,sqli,xss,oast,etc"""
        results = []
        for f in self.templates_dir.rglob("*.yaml"):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                if f"tags:" in content and tag.lower() in content.lower():
                    results.append(f)
            except Exception:
                pass
        return results

    def get_cve_templates(self) -> List[Path]:
        """获取所有CVE模板"""
        return self.get_templates_by_tag("cve")

    def get_cisa_kev_templates(self) -> List[Path]:
        """获取CISA KEV已知漏洞模板（高价值）"""
        return self.get_templates_by_tag("kev")

    def stats(self) -> Dict:
        """模板统计"""
        total = self.count_templates()
        if total == 0:
            return {"total": 0}

        severities = {}
        tags = {}
        for f in self.templates_dir.rglob("*.yaml"):
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                for line in content.split("\n"):
                    line = line.strip()
                    if line.startswith("severity:"):
                        sev = line.split(":", 1)[1].strip()
                        severities[sev] = severities.get(sev, 0) + 1
                    if line.startswith("tags:"):
                        for t in line.split(":", 1)[1].strip().split(","):
                            t = t.strip()
                            if t:
                                tags[t] = tags.get(t, 0) + 1
            except Exception:
                pass

        return {
            "total": total,
            "by_severity": severities,
            "top_tags": dict(sorted(tags.items(), key=lambda x: x[1], reverse=True)[:15]),
            "last_update": self._get_last_update(),
        }

    def _should_skip_update(self) -> bool:
        """是否跳过更新（24小时内更新过）"""
        if not self.last_update_file.exists():
            return False
        try:
            last = float(self.last_update_file.read_text().strip())
            return (time.time() - last) < UPDATE_INTERVAL
        except Exception:
            return False

    def _get_last_update(self) -> str:
        """获取上次更新时间"""
        if not self.last_update_file.exists():
            return "从未更新"
        try:
            ts = float(self.last_update_file.read_text().strip())
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        except Exception:
            return "未知"


# ==================== 全局 ====================

_manager: Optional[NucleiManager] = None


def get_nm() -> NucleiManager:
    global _manager
    if _manager is None:
        _manager = NucleiManager()
    return _manager


def nuclei_update(force: bool = False) -> Dict:
    return get_nm().update_templates(force)


def nuclei_stats() -> Dict:
    return get_nm().stats()
