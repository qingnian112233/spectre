"""
漏洞优先级排序引擎 v1.0 — CVSS评分 + 利用难度 + 影响范围
───────────────────────────────────────────
特性:
  • 自动CVSS 3.1评分（无外部API依赖）
  • 利用难度评估（自动化可利用 > 需认证 > 需用户交互）
  • 影响范围排序（RCE > 数据泄露 > 信息泄露）
  • 攻击优先级矩阵输出
  • 与nuclei输出无缝对接

用法:
  from .vuln_prioritizer import VulnPrioritizer
  vp = VulnPrioritizer()
  ranked = vp.rank(nuclei_output_json)
"""

import re, json
from typing import List, Dict, Optional
from dataclasses import dataclass, field


# ==================== CVSS 3.1 简易评分 ====================

@dataclass
class CVSSVector:
    AV: str = "N"  # Attack Vector: N/A/L/P
    AC: str = "L"  # Attack Complexity: L/H
    PR: str = "N"  # Privileges Required: N/L/H
    UI: str = "N"  # User Interaction: N/R
    S: str = "U"   # Scope: U/C
    C: str = "H"   # Confidentiality: N/L/H
    I: str = "H"   # Integrity: N/L/H
    A: str = "H"   # Availability: N/L/H

    def to_score(self) -> float:
        """简易CVSS 3.1计算"""
        # 攻击向量权重
        av_map = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
        ac_map = {"L": 0.77, "H": 0.44}
        pr_map_u = {"N": 0.85, "L": 0.62, "H": 0.27}
        pr_map_c = {"N": 0.85, "L": 0.68, "H": 0.50}
        ui_map = {"N": 0.85, "R": 0.62}

        # Impact
        c_map = {"N": 0, "L": 0.22, "H": 0.56}
        i_map = {"N": 0, "L": 0.22, "H": 0.56}
        a_map = {"N": 0, "L": 0.22, "H": 0.56}

        iss = 1 - ((1 - c_map[self.C]) * (1 - i_map[self.I]) * (1 - a_map[self.A]))
        impact = 6.42 * iss if self.S == "U" else 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15

        pr_val = pr_map_u[self.PR] if self.S == "U" else pr_map_c[self.PR]
        exploitability = 8.22 * av_map[self.AV] * ac_map[self.AC] * pr_val * ui_map[self.UI]

        if impact <= 0:
            return 0.0

        if self.S == "U":
            base = min(impact + exploitability, 10)
        else:
            base = min(1.08 * (impact + exploitability), 10)

        return round(base, 1)


# ==================== 漏洞优先级 ====================

@dataclass
class RankedVuln:
    name: str
    severity: str          # critical/high/medium/low
    cvss_score: float
    exploitability: str    # auto/semi/manual
    impact_type: str       # rce/data_leak/info_leak/dos
    endpoint: str
    priority: int          # 1-10 攻击优先级
    rationale: str


class VulnPrioritizer:
    """漏洞优先级排序器"""

    # 漏洞类型 → 默认CVSS向量
    VULN_VECTORS = {
        "sql-injection": CVSSVector(C="H", I="H", A="N"),
        "rce": CVSSVector(C="H", I="H", A="H"),
        "command-injection": CVSSVector(C="H", I="H", A="H"),
        "file-upload": CVSSVector(C="H", I="H", A="H"),
        "xxe": CVSSVector(C="H", I="N", A="N"),
        "ssrf": CVSSVector(C="H", I="N", A="N", AV="A"),
        "lfi": CVSSVector(C="H", I="N", A="N"),
        "xss": CVSSVector(C="L", I="L", A="N", UI="R"),
        "csrf": CVSSVector(C="L", I="L", A="N", UI="R"),
        "open-redirect": CVSSVector(C="N", I="L", A="N", UI="R"),
        "idor": CVSSVector(C="H", I="N", A="N"),
        "auth-bypass": CVSSVector(C="H", I="H", A="N"),
    }

    # 影响类型 → 优先级权重
    IMPACT_WEIGHTS = {
        "rce":             10,
        "command-injection": 10,
        "sql-injection":   9,
        "auth-bypass":     9,
        "file-upload":     8,
        "idor":            8,
        "xxe":             7,
        "ssrf":            7,
        "lfi":             6,
        "xss":             4,
        "csrf":            3,
        "open-redirect":   2,
        "info-leak":       3,
    }

    def rank(self, nuclei_output: str = "", vuln_list: List[Dict] = None) -> List[RankedVuln]:
        """
        对漏洞列表排序
        输入: nuclei JSON输出 或 漏洞dict列表
        返回: 按优先级排序的漏洞列表
        """
        vulns = vuln_list or []
        if not vulns and nuclei_output:
            vulns = self._parse_nuclei(nuclei_output)

        ranked = []
        for v in vulns:
            r = self._rank_single(v)
            ranked.append(r)

        ranked.sort(key=lambda x: (x.priority, x.cvss_score), reverse=True)
        return ranked

    def _rank_single(self, vuln: Dict) -> RankedVuln:
        """对单个漏洞评分"""
        name = vuln.get("name", vuln.get("template", "unknown"))
        severity = vuln.get("severity", vuln.get("info", {}).get("severity", "medium")).lower()
        endpoint = vuln.get("host", vuln.get("matched-at", vuln.get("url", "")))

        # 识别漏洞类型
        vtype = self._classify(name.lower())

        # CVSS评分
        vector = self.VULN_VECTORS.get(vtype, CVSSVector())
        cvss = vector.to_score()

        # 利用难度
        exploitability = self._assess_exploitability(vtype, vuln)

        # 影响类型
        impact_type = self._assess_impact(vtype)

        # 优先级 (1-10)
        impact_weight = self.IMPACT_WEIGHTS.get(vtype, 5)
        auto_bonus = 2 if exploitability == "auto" else 0
        priority = min(10, impact_weight + auto_bonus)

        return RankedVuln(
            name=name,
            severity=severity,
            cvss_score=cvss,
            exploitability=exploitability,
            impact_type=impact_type,
            endpoint=endpoint,
            priority=priority,
            rationale=f"{vtype} | CVSS:{cvss} | 利用:{exploitability} | 影响:{impact_type}",
        )

    def _classify(self, name: str) -> str:
        """分类漏洞类型"""
        patterns = [
            (r"sql.?inject|sqli|blind.?sql", "sql-injection"),
            (r"rce|remote.?code.?exec|command.?inject|cmd.?inject|os.?command", "rce"),
            (r"xss|cross.?site.?script", "xss"),
            (r"xxe|xml.?external", "xxe"),
            (r"ssrf|server.?side.?request", "ssrf"),
            (r"lfi|local.?file.?includ|path.?travers|directory.?travers", "lfi"),
            (r"file.?upload", "file-upload"),
            (r"csrf|cross.?site.?request", "csrf"),
            (r"idor|insecure.?direct.?object|broken.?object.?level", "idor"),
            (r"auth.?bypass|broken.?auth|credential|login.?bypass", "auth-bypass"),
            (r"open.?redirect", "open-redirect"),
            (r"expos|disclos|leak|dump|enumerat", "info-leak"),
        ]
        for pattern, vtype in patterns:
            if re.search(pattern, name, re.IGNORECASE):
                return vtype
        return "info-leak"

    def _assess_exploitability(self, vtype: str, vuln: Dict) -> str:
        """评估利用难度"""
        # 自动可利用
        auto_types = {"sql-injection", "rce", "command-injection", "xxe", "ssrf", "lfi", "idor"}
        if vtype in auto_types:
            return "auto"
        # 半自动
        semi_types = {"xss", "file-upload", "auth-bypass", "csrf"}
        if vtype in semi_types:
            return "semi"
        return "manual"

    def _assess_impact(self, vtype: str) -> str:
        """评估影响类型"""
        rce_types = {"rce", "command-injection", "file-upload"}
        data_leak = {"sql-injection", "xxe", "ssrf", "lfi", "idor", "auth-bypass"}
        if vtype in rce_types:
            return "rce"
        if vtype in data_leak:
            return "data_leak"
        return "info_leak"

    def _parse_nuclei(self, output: str) -> List[Dict]:
        """解析nuclei JSON输出"""
        vulns = []
        try:
            # 尝试JSONL格式
            for line in output.strip().split("\n"):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    v = json.loads(line)
                    vulns.append(v)
        except Exception:
            pass
        return vulns

    def format_ranked(self, ranked: List[RankedVuln], top_n: int = 10) -> str:
        """格式化输出优先级列表"""
        if not ranked:
            return "无漏洞发现"

        lines = ["## 🔥 攻击优先级 TOP{}\n".format(min(top_n, len(ranked)))]
        lines.append("| # | 优先级 | 漏洞 | CVSS | 利用 | 端点 |")
        lines.append("|:--|:--:|------|:--:|:--:|------|")

        for i, v in enumerate(ranked[:top_n], 1):
            emoji = "🔴" if v.priority >= 8 else "🟡" if v.priority >= 5 else "🟢"
            lines.append(
                f"| {i} | {emoji} **{v.priority}** | {v.name[:50]} | {v.cvss_score} | {v.exploitability} | {v.endpoint[:40]} |"
            )

        return "\n".join(lines)


# ==================== 全局 ====================

_prioritizer: Optional[VulnPrioritizer] = None


def get_prioritizer() -> VulnPrioritizer:
    global _prioritizer
    if _prioritizer is None:
        _prioritizer = VulnPrioritizer()
    return _prioritizer


def rank_vulns(nuclei_output: str = "", vuln_list: List[Dict] = None) -> List[RankedVuln]:
    return get_prioritizer().rank(nuclei_output, vuln_list)
