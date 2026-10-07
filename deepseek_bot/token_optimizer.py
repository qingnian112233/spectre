"""
Token智能优化器 v1.0 — 大输出自动摘要 + 关键信息提取
───────────────────────────────────────────
特性:
  • 扫描结果自动摘要（保留漏洞/端口/关键信息）
  • 去噪：移除重复行、无意义输出
  • 优先级标记：高价值信息前置
  • 智能截断：保留最有用的N条

用法:
  from .token_optimizer import optimize_output
  short = optimize_output(nmap_output, "nmap", max_chars=2000)
"""

import re
from typing import List, Tuple

# 每个工具的最大输出字符数（发送给AI之前）
TOOL_MAX_CHARS = {
    "nmap":     3000,
    "nuclei":   5000,
    "sqlmap":   3000,
    "ffuf":     2000,
    "subfinder": 1500,
    "amass":    1500,
    "httpx":    2000,
    "whatweb":  1500,
    "wafw00f":  1000,
    "nikto":    3000,
    "wapiti":   3000,
    "commix":   2000,
    "xsstrike": 2000,
    "gobuster": 2000,
    "feroxbuster": 2000,
    "dirsearch": 2000,
    "dnsx":     1500,
    "arjun":    1500,
    "testssl":  3000,
    "wpscan":   3000,
    "default":  2000,
}


def optimize_output(output: str, tool: str, max_chars: int = None) -> str:
    """
    智能优化工具输出：
    1. 去噪去重
    2. 优先保留高价值信息
    3. 按max_chars截断
    """
    if not output or not output.strip():
        return output

    max_chars = max_chars or TOOL_MAX_CHARS.get(tool, TOOL_MAX_CHARS["default"])

    # 已经在限制以内就直接返回
    if len(output) <= max_chars:
        return output

    # 按工具类型做不同优化
    if tool in ("nmap", "masscan"):
        return _optimize_nmap(output, max_chars)
    elif tool in ("nuclei"):
        return _optimize_nuclei(output, max_chars)
    elif tool in ("sqlmap"):
        return _optimize_sqlmap(output, max_chars)
    elif tool in ("ffuf", "gobuster", "feroxbuster", "dirsearch"):
        return _optimize_fuzzer(output, max_chars)
    elif tool in ("subfinder", "amass", "dnsx"):
        return _optimize_dns(output, max_chars)
    elif tool in ("httpx"):
        return _optimize_httpx(output, max_chars)
    else:
        return _optimize_generic(output, max_chars)


def _optimize_nmap(output: str, max_chars: int) -> str:
    """Nmap优化：保留端口、服务、OS"""
    lines = output.split("\n")
    result = []
    port_section = False
    os_section = False
    for line in lines:
        # 保留端口行
        if re.search(r'\d+/tcp\s+(open|filtered)', line):
            port_section = True
            result.append(line)
        elif port_section and line.strip() and not line.startswith("Nmap"):
            result.append(line)
        elif line.startswith("Nmap done") or line.startswith("Service detection"):
            port_section = False
        # OS检测
        if "OS details:" in line or "Aggressive OS guesses:" in line:
            os_section = True
        if os_section and line.strip():
            result.append(line)
            if not line.strip().endswith(","):
                os_section = False
        # 保留头部
        if line.startswith("Nmap scan report for"):
            result.append(line)

    return _trim(result, max_chars)


def _optimize_nuclei(output: str, max_chars: int) -> str:
    """Nuclei优化：保留漏洞行"""
    lines = output.split("\n")
    result = []
    for line in lines:
        # 保留漏洞发现行
        if any(kw in line for kw in ["[critical]", "[high]", "[medium]", "[low]",
                                       "CVE-", "EXPLOITABLE", "MATCH"]):
            result.append(line)
    # 如果太少就返回原输出尾部
    if len("\n".join(result)) < 200:
        return output[-max_chars:]
    return _trim(result, max_chars)


def _optimize_sqlmap(output: str, max_chars: int) -> str:
    """SQLMap优化：保留关键发现"""
    lines = output.split("\n")
    result = []
    for line in lines:
        if any(kw in line.lower() for kw in [
            "injectable", "parameter", "payload", "back-end",
            "database:", "table:", "column:", "dump", "cracked",
            "vulnerable", "identified", "type:",
        ]):
            result.append(line)
    return _trim(result, max_chars)


def _optimize_fuzzer(output: str, max_chars: int) -> str:
    """Fuzzer优化：保留有结果的路径（非404）"""
    lines = output.split("\n")
    result = []
    for line in lines:
        # 保留非404的结果
        if re.search(r'\[2\d\d\]|\[3\d\d\]|\[4[01]\d\]|\[5\d\d\]', line):
            result.append(line)
    # 去重
    seen = set()
    deduped = []
    for l in result:
        if l not in seen:
            seen.add(l)
            deduped.append(l)
    return _trim(deduped, max_chars)


def _optimize_dns(output: str, max_chars: int) -> str:
    """DNS工具优化：保留域名/IP行"""
    lines = output.split("\n")
    result = []
    for line in lines:
        # 保留域名和IP
        if re.search(r'[a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,}', line):
            result.append(line)
    return _trim(result, max_chars)


def _optimize_httpx(output: str, max_chars: int) -> str:
    """HTTPx优化：保留URL+状态码"""
    lines = output.split("\n")
    result = []
    for line in lines:
        if re.search(r'https?://', line) and re.search(r'\[2\d\d\]|\[3\d\d\]|\[4\d\d\]|\[5\d\d\]', line):
            result.append(line)
    return _trim(result, max_chars)


def _optimize_generic(output: str, max_chars: int) -> str:
    """通用优化：去空行+去重+截断"""
    lines = [l for l in output.split("\n") if l.strip()]
    seen = set()
    deduped = []
    for l in lines:
        if l not in seen:
            seen.add(l)
            deduped.append(l)
    return _trim(deduped, max_chars)


def _trim(lines: List[str], max_chars: int) -> str:
    """智能截断：保留开头200字符+末尾"""
    result_lines = []
    total = 0
    for line in lines:
        if total + len(line) + 1 > max_chars:
            break
        result_lines.append(line)
        total += len(line) + 1

    text = "\n".join(result_lines)
    if len(text) < len("\n".join(lines)) and len(lines) > len(result_lines):
        remaining = len(lines) - len(result_lines)
        text += f"\n\n... [{remaining} more lines truncated]"

    return text


def estimate_tokens(text: str) -> int:
    """估算token数（粗略：1 token ≈ 2中文字符 或 4英文字符）"""
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    english = len(text) - chinese
    return int(chinese / 1.5 + english / 4)


def token_report(outputs: List[Tuple[str, str, str]]) -> str:
    """
    生成token消耗报告
    inputs: [(tool_name, original_output, optimized_output), ...]
    """
    lines = ["## 📊 Token优化报告\n", "| 工具 | 原始 | 优化后 | 节省 |"]
    lines.append("|:--|:--|:--|:--|")
    total_orig = 0
    total_opt = 0
    for tool, orig, opt in outputs:
        orig_t = estimate_tokens(orig)
        opt_t = estimate_tokens(opt)
        saved = orig_t - opt_t
        total_orig += orig_t
        total_opt += opt_t
        pct = f"{saved/orig_t*100:.0f}%" if orig_t > 0 else "0%"
        lines.append(f"| {tool} | {orig_t}t | {opt_t}t | {saved}t ({pct}) |")
    total_saved = total_orig - total_opt
    lines.append(f"\n**总计: {total_orig}t → {total_opt}t, 节省 {total_saved}t ({total_saved/total_orig*100:.0f}%)**")
    return "\n".join(lines)
