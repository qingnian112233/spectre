"""渗透测试报告生成: Markdown + PDF"""
import json, time, os
from pathlib import Path
from datetime import datetime
from typing import Optional

from . import db

OUT = Path("/opt/deepseek-bot/reports")
OUT.mkdir(exist_ok=True)


def _severity_icon(sev: str) -> str:
    return {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "info": "🔵"}.get(sev, "⚪")


def _severity_sort(sev: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(sev, 5)


def generate_md(project_id: int, uid: int) -> str:
    """生成 Markdown 格式渗透报告"""
    # 获取项目信息
    projects = db.project_list(uid)
    proj = next((p for p in projects if p["id"] == project_id), None)
    if not proj:
        return "❌ 项目不存在"

    findings = db.finding_list(project_id)
    scans = db.scan_list(project_id)
    stats = db.finding_stats(project_id)

    findings.sort(key=lambda f: _severity_sort(f["severity"]))

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    md = f"""# 📋 渗透测试报告

---

## 📌 基本信息

| 项目 | 详情 |
|------|------|
| **项目名称** | {proj['name']} |
| **目标** | `{proj['target'] or 'N/A'}` |
| **测试时间** | {datetime.fromtimestamp(proj['created_at']).strftime('%Y-%m-%d %H:%M')} - {now} |
| **报告生成** | {now} |
| **扫描次数** | {len(scans)} |

---

## 📊 漏洞统计

| 严重性 | 数量 |
|--------|------|
| 🔴 Critical | {stats.get('critical', 0)} |
| 🟠 High | {stats.get('high', 0)} |
| 🟡 Medium | {stats.get('medium', 0)} |
| 🟢 Low | {stats.get('low', 0)} |
| 🔵 Info | {stats.get('info', 0)} |
| **合计** | **{sum(stats.values())}** |

---

## 🔍 漏洞详情

"""

    # 分组
    sev_order = ["critical", "high", "medium", "low", "info"]
    by_sev = {}
    for f in findings:
        sev = f["severity"]
        by_sev.setdefault(sev, []).append(f)

    for sev in sev_order:
        items = by_sev.get(sev, [])
        if not items: continue
        md += f"### {_severity_icon(sev)} {sev.upper()} ({len(items)}个)\n\n"
        for i, f in enumerate(items, 1):
            md += f"""#### {i}. {f['title']}

- **目标**: `{f.get('target', 'N/A')}`
- **描述**: {f.get('description', '无描述')}
- **证据**:
```
{f.get('evidence', '无')[:500]}
```

"""
        md += "---\n\n"

    # 扫描历史
    md += "## 📜 扫描记录\n\n"
    md += "| 工具 | 目标 | 状态 | 时间 |\n"
    md += "|------|------|------|------|\n"
    for s in scans[:20]:
        started = datetime.fromtimestamp(s['started_at']).strftime("%m-%d %H:%M") if s['started_at'] else "-"
        md += f"| {s['tool']} | {s['target'][:40]} | {s['status']} | {started} |\n"

    md += f"\n---\n*报告由 NFTNB Agent 自动生成 · {now}*"

    return md


def generate_pdf(project_id: int, uid: int) -> Optional[Path]:
    """生成 PDF 报告，返回文件路径"""
    md_content = generate_md(project_id, uid)
    if md_content.startswith("❌"):
        return None

    filename = f"pentest_report_{project_id}_{int(time.time())}.pdf"
    filepath = OUT / filename

    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.lib.units import mm

        # 中文支持: reportlab 内置 CID 中文字体(无ttf依赖), Helvetica画中文=空壳的根因
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))

        c = canvas.Canvas(str(filepath), pagesize=A4)
        width, height = A4

        y = height - 30 * mm
        left_margin = 20 * mm
        max_width = width - 40 * mm

        c.setFont("STSong-Light", 16)
        c.drawString(left_margin, y, "渗透测试报告")
        c.drawString(left_margin + 90 * mm, y, f"(project {project_id})")
        y -= 10 * mm

        for line in md_content.split('\n'):
            if y < 15 * mm:
                c.showPage()
                y = height - 25 * mm
            # 截断过长行(按中文字符宽度)
            display = line[:120]
            if line.startswith('#'):
                c.setFont("STSong-Light", 12 if not line.startswith('##') else 10)
                display = line.lstrip('#').strip()[:100]
            elif line.strip().startswith('|'):
                c.setFont("STSong-Light", 8)
            else:
                c.setFont("STSong-Light", 9)
                display = line[:100]
            # 空行也保留间距
            c.drawString(left_margin, y, display)
            y -= 13

        c.save()
        return filepath
    except Exception as e:
        # 如果 PDF 生成失败，保存 MD
        md_path = OUT / f"pentest_report_{project_id}_{int(time.time())}.md"
        md_path.write_text(md_content, encoding="utf-8")
        return md_path


def generate_summary(project_id: int, uid: int) -> str:
    """生成简要摘要"""
    projects = db.project_list(uid)
    proj = next((p for p in projects if p["id"] == project_id), None)
    if not proj: return "❌ 项目不存在"

    stats = db.finding_stats(project_id)
    findings = db.finding_list(project_id)
    scans = db.scan_list(project_id)

    lines = [
        f"📋 **{proj['name']}** - 渗透测试摘要",
        f"🎯 目标: `{proj['target'] or '未指定'}`",
        f"📊 发现: {sum(stats.values())} 个漏洞 | 扫描 {len(scans)} 次",
    ]

    sev_parts = []
    for sev in ["critical", "high", "medium", "low"]:
        n = stats.get(sev, 0)
        if n: sev_parts.append(f"{_severity_icon(sev)} {n}")
    if sev_parts:
        lines.append(f"⚠️ 严重: {' '.join(sev_parts)}")
    else:
        lines.append("✅ 未发现高危漏洞")

    # Top 3 findings
    findings.sort(key=lambda f: _severity_sort(f["severity"]))
    if findings:
        lines.append("\n**Top 发现:**")
        for f in findings[:5]:
            lines.append(f"  {_severity_icon(f['severity'])} {f['title'][:60]}")

    return "\n".join(lines)


def export_project(project_id: int, uid: int, fmt: str = "md") -> str:
    """导出项目，返回文件路径"""
    if fmt == "pdf":
        path = generate_pdf(project_id, uid)
        return str(path) if path else "生成失败"
    elif fmt == "md":
        md = generate_md(project_id, uid)
        path = OUT / f"pentest_report_{project_id}_{int(time.time())}.md"
        path.write_text(md, encoding="utf-8")
        return str(path)
    elif fmt == "summary":
        return generate_summary(project_id, uid)
    else:
        return f"不支持的格式: {fmt} (支持 md, pdf, summary)"
