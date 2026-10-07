"""
状态固化引擎 v3.0 — 深度集成到Bot
每次关键操作后自动save，下次对话秒恢复
project create → 自动建state.md
project switch → 自动load注入上下文
攻击操作   → 自动snapshot
"状态"/"继续" → 秒读恢复
"""
import os, json, time, re
from pathlib import Path
from datetime import datetime

BASE = Path("/opt/deepseek-bot/projects")
BASE.mkdir(exist_ok=True)

# ==================== 模板 ====================
TEMPLATE = """# 📊 {name} — 状态快照
> 最后更新: {time}
> 项目ID: {pid} | 目标: {target}

## 💰 余额/资产
| 类型 | 数量 | 备注 |
|:--|:--|:--|
{balances}

## 🔑 有效凭证
| 类型 | 值 | 状态 |
|:--|:--|:--|
{credentials}

## ⚔️ 攻击历史
| 时间 | 操作 | 目标 | 结果 |
|:--|:--|:--|:--|
{attacks}

## 🚧 当前堵点
{blocks}

## 🎯 下一步
{next_steps}

## 📝 笔记
{notes}
"""

# ==================== 状态文件路径 ====================
def _safe_name(project_name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_\-一-鿿]', '_', project_name)

def _state_path(project_name: str, uid: str = "") -> Path:
    """获取项目的state.md路径; uid传入时按uid隔离(projects/{uid}_{name}/)"""
    safe_name = _safe_name(project_name)
    p = BASE / (f"{uid}_{safe_name}" if uid else safe_name) / "state.md"
    # 兼容旧数据: 新路径不存在且旧路径(无uid前缀)存在 → 沿用旧文件
    if uid and not p.exists():
        old = BASE / safe_name / "state.md"
        if old.exists():
            return old
    return p

def _ensure_dir(project_name: str, uid: str = ""):
    safe_name = _safe_name(project_name)
    (BASE / (f"{uid}_{safe_name}" if uid else safe_name)).mkdir(parents=True, exist_ok=True)

# ==================== 初始化 ====================
def init(project_name: str, pid: int = 0, target: str = "", uid: str = "") -> str:
    """创建项目时自动初始化state.md"""
    _ensure_dir(project_name, uid)
    fpath = _state_path(project_name, uid)

    content = TEMPLATE.format(
        name=project_name,
        time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        pid=pid,
        target=target or "待定",
        balances="| - | - | - |",
        credentials="| - | - | - |",
        attacks="| - | - | - | - |",
        blocks="（无）",
        next_steps="（待规划）",
        notes=""
    )
    fpath.write_text(content, encoding="utf-8")
    return str(fpath)

# ==================== 加载 ====================
def load(project_name: str, uid: str = "") -> str:
    """加载项目状态——返回完整state.md内容"""
    fpath = _state_path(project_name, uid)
    if not fpath.exists():
        # 尝试搜索相似名称
        for d in BASE.iterdir():
            if d.is_dir() and project_name.lower() in d.name.lower():
                sf = d / "state.md"
                if sf.exists():
                    return sf.read_text(encoding="utf-8")
        return f"❌ 项目「{project_name}」无状态记录"
    return fpath.read_text(encoding="utf-8")

# ==================== 保存 ====================
def save(project_name: str, section: str, key: str, value: str, uid: str = "") -> bool:
    """保存一条记录到指定section"""
    fpath = _state_path(project_name, uid)
    if not fpath.exists():
        init(project_name, uid=uid)

    content = fpath.read_text(encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    now_short = datetime.now().strftime("%m-%d %H:%M")

    # 更新时间戳
    content = re.sub(r'> 最后更新: .*', f'> 最后更新: {now}', content)

    if section == "balance":
        # 替换余额表格：| USDT | 10.00 | 商户10010 |
        new_row = f"| {key} | {value} | {now_short} |"
        # 找到余额表格并替换第一个"-"行
        content = _replace_first_in_section(content, "💰 余额/资产", "| - | - | - |", new_row)

    elif section == "credential":
        new_row = f"| {key} | {value} | ✅ {now_short} |"
        content = _replace_first_in_section(content, "🔑 有效凭证", "| - | - | - |", new_row)

    elif section == "attack":
        # key=操作, value=结果
        new_row = f"| {now_short} | {key} | {value} |"
        content = _replace_first_in_section(content, "⚔️ 攻击历史", "| - | - | - | - |", new_row)

    elif section == "block":
        # 追加到堵点区域
        content = content.replace("（无）\n\n## 🎯", f"{key}: {value}\n（无）\n\n## 🎯", 1)
        # 或者追加到已有堵点
        blocks_section = _get_section_text(content, "🚧 当前堵点")
        if blocks_section and "（无）" not in blocks_section:
            content = content.replace(
                f"\n\n## 🎯 下一步",
                f"\n- **{now_short}**: {key} → {value}\n\n## 🎯 下一步"
            )

    elif section == "next":
        content = content.replace("（待规划）", f"{key}: {value}")
        next_section = _get_section_text(content, "🎯 下一步")
        if next_section and "（待规划）" not in next_section:
            content = content.replace(
                f"\n\n## 📝 笔记",
                f"\n- **{now_short}**: {key} → {value}\n\n## 📝 笔记"
            )

    elif section == "note":
        content = content.replace(
            "\n## 📝 笔记\n",
            f"\n## 📝 笔记\n- **{now_short}**: {key}: {value}\n"
        )

    else:
        # 通用：追加到笔记区
        content = content.replace(
            "\n## 📝 笔记\n",
            f"\n## 📝 笔记\n- **{now_short}**: [{section}] {key}: {value}\n"
        )

    fpath.write_text(content, encoding="utf-8")
    return True

def _replace_first_in_section(content: str, section_header: str, old: str, new: str) -> str:
    """在指定section中替换第一个匹配"""
    parts = content.split(section_header)
    if len(parts) < 2:
        return content
    # parts[0] 是section之前，parts[1] 是section标题之后
    section_content = parts[1]
    # 找到下一个 ## 的位置作为section结束
    next_section = re.search(r'\n## ', section_content)
    if next_section:
        section_body = section_content[:next_section.start()]
        after = section_content[next_section.start():]
    else:
        section_body = section_content
        after = ""

    section_body = section_body.replace(old, new, 1)
    return parts[0] + section_header + section_body + after

def _get_section_text(content: str, section_header: str) -> str:
    """获取指定section的文本"""
    parts = content.split(section_header)
    if len(parts) < 2:
        return ""
    section_content = parts[1]
    next_section = re.search(r'\n## ', section_content)
    if next_section:
        return section_content[:next_section.start()].strip()
    return section_content.strip()

# ==================== 快照（从工具输出自动解析） ====================
def snapshot(project_name: str, event_type: str, data: str, uid: str = "") -> bool:
    """自动快照——从工具输出解析并保存

    event_type:
        balance|余额: data = "USDT|10.00|商户10010"
        credential|密钥: data = "API密钥|sk-xxx|有效"
        transfer|转账: data = "转出9U→TX5f...|成功(审核中)"
        scan|扫描: data = "nmap|开放22,80,443"
        vuln|漏洞: data = "SQL注入|api/login.php"
        block|堵点: data = "余额不足|需要充值"
        next|下一步: data = "社工客服|套API文档"
        note|笔记: data = "备注内容"
    """
    type_map = {
        "balance": "balance", "余额": "balance",
        "credential": "credential", "密钥": "credential", "token": "credential",
        "transfer": "attack", "转账": "attack", "attack": "attack",
        "scan": "attack", "扫描": "attack",
        "vuln": "attack", "漏洞": "attack",
        "block": "block", "堵点": "block",
        "next": "next", "下一步": "next",
        "note": "note", "笔记": "note",
    }
    section = type_map.get(event_type, "note")

    if "|" in data:
        parts = data.split("|", 1)
        key, value = parts[0], parts[1] if len(parts) > 1 else ""
    else:
        key = event_type
        value = data

    return save(project_name, section, key.strip(), value.strip(), uid)

# ==================== 批量操作 ====================
def save_batch(project_name: str, items: list, uid: str = "") -> int:
    """批量保存: [(section, key, value), ...]"""
    count = 0
    for section, key, value in items:
        if save(project_name, section, key, value, uid):
            count += 1
    return count

# ==================== 列表所有项目 ====================
def list_all() -> str:
    """列出所有已固化的项目"""
    lines = []
    for d in sorted(BASE.iterdir(), key=lambda x: x.name):
        if d.is_dir():
            sf = d / "state.md"
            if sf.exists():
                content = sf.read_text(encoding="utf-8")
                # 提取最后更新时间
                m = re.search(r'> 最后更新: (.+)', content)
                t = m.group(1) if m else "未知"
                # 提取余额
                bal_m = re.search(r'\| (USDT\|[^\n]+)', content)
                bal = bal_m.group(1).strip() if bal_m else "-"
                lines.append(f"● **{d.name}** | {t} | {bal}")
    return "\n".join(lines) if lines else "（无固化项目）"

# ==================== 导出为JSON ====================
def export_json(project_name: str, uid: str = "") -> dict:
    """导出项目状态为结构化JSON"""
    content = load(project_name, uid)
    if content.startswith("❌"):
        return {"error": content}

    result = {
        "project": project_name,
        "last_update": "",
        "balances": [],
        "credentials": [],
        "attacks": [],
        "blocks": [],
        "next_steps": [],
        "notes": []
    }

    # 解析时间
    m = re.search(r'> 最后更新: (.+)', content)
    if m: result["last_update"] = m.group(1)

    # 解析余额表格
    result["balances"] = _parse_table(content, "💰 余额/资产")
    result["credentials"] = _parse_table(content, "🔑 有效凭证")
    result["attacks"] = _parse_table(content, "⚔️ 攻击历史")

    return result

def _parse_table(content: str, section_header: str) -> list:
    """解析markdown表格"""
    section = _get_section_text(content, section_header)
    if not section: return []
    rows = []
    for line in section.split("\n"):
        if line.startswith("|") and "---" not in line and ":-" not in line:
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if cols and cols[0] not in ("类型", "时间", ""):
                rows.append(cols)
    return rows

# ==================== 自动注入上下文 ====================
def inject_context(project_name: str, uid: str = "") -> str:
    """生成注入到AI上下文的摘要——2秒恢复现场"""
    content = load(project_name, uid)
    if content.startswith("❌"):
        return content

    # 提取关键信息
    m_time = re.search(r'> 最后更新: (.+)', content)
    m_balance = re.search(r'\| (USDT[^\n]+)', content)
    m_block = re.search(r'## 🚧 当前堵点\n(.+?)(?:\n## |$)', content, re.DOTALL)
    m_next = re.search(r'## 🎯 下一步\n(.+?)(?:\n## |$)', content, re.DOTALL)

    ctx = f"""⚡ **{project_name}** 状态恢复 (最后更新: {m_time.group(1) if m_time else '未知'})
💰 余额: {m_balance.group(1).strip() if m_balance else '未知'}
🚧 堵点: {m_block.group(1).strip()[:200] if m_block else '无'}
🎯 下一步: {m_next.group(1).strip()[:200] if m_next else '未规划'}
---
"""
    return ctx

# ==================== CLI ====================
def delete_state(project_name: str, uid: str = "") -> bool:
    """删除项目的state.md"""
    import shutil
    fpath = _state_path(project_name, uid)
    if fpath.exists():
        fpath.unlink()
        # 也尝试删除空目录
        pdir = fpath.parent
        if pdir.exists() and not any(pdir.iterdir()):
            shutil.rmtree(pdir, ignore_errors=True)
        return True
    return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: state_engine.py <init|load|save|snapshot|list|context> [参数...]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "init":
        name = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        pid = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        target = sys.argv[4] if len(sys.argv) > 4 else ""
        print(init(name, pid, target))

    elif cmd == "load":
        name = sys.argv[2] if len(sys.argv) > 2 else ""
        print(load(name))

    elif cmd == "save":
        name, section, key, value = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
        print(save(name, section, key, value))

    elif cmd == "snapshot":
        name, event_type, data = sys.argv[2], sys.argv[3], sys.argv[4]
        print(snapshot(name, event_type, data))

    elif cmd == "list":
        print(list_all())

    elif cmd == "context":
        name = sys.argv[2] if len(sys.argv) > 2 else ""
        print(inject_context(name))
