#!/usr/bin/env python3
"""技能创建器: 把新能力包装成"可注册技能" — 工具型(plugin 声明, 自动注册生效) 或 知识型(TRIGGER 知识)
用法(插件工具): 参数 kind=tool|knowledge, name 必填
"""
import sys, json, os, subprocess, time
from pathlib import Path

KB = Path("/opt/deepseek-bot/knowledge")
PLUG = KB / "toolPlugins"

try:
    a = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
except Exception:
    print("err: 参数JSON缺失或损坏")
    sys.exit(0)

kind = str(a.get("kind", "tool")).lower()
name = str(a.get("name", "")).strip()
if not name:
    print("err: 需要 name（技能名）")
    sys.exit(0)

if kind == "tool":
    desc = str(a.get("description", name))
    schema = a.get("schema", {"type": "object", "properties": {}})
    code = str(a.get("code", ""))
    execpath = str(a.get("exec_path", f"/opt/deepseek-bot/knowledge/toolPlugins/{name}.py"))
    if not code:
        print("err: 工具型技能需要 code（python 脚本内容）")
        sys.exit(0)
    py = Path(execpath)
    py.write_text(code, encoding="utf-8")
    os.chmod(py, 0o755)
    r = subprocess.run(["/opt/deepseek-bot/.venv/bin/python", "-m", "py_compile", str(py)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"err: 语法检查失败: {r.stderr.strip()[:200]}")
        sys.exit(0)
    cfg = {"name": name, "description": desc, "schema": schema, "exec": execpath,
           "admin_only": bool(a.get("admin_only", True)), "timeout": int(a.get("timeout", 60))}
    (PLUG / f"{name}.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ok: 工具技能[{name}]已创建（声明+脚本，语法通过）；重启后生效；下次会话模型可调用")
elif kind == "knowledge":
    trig = str(a.get("trigger", ""))
    content = str(a.get("content", ""))
    if not trig:
        print("err: 知识型技能需要 trigger（TRIGGER 场景关键词）")
        sys.exit(0)
    sfp = KB / "self_learned" / f"skill-{name.replace(' ', '_')}.md"
    head = f"# 🔧 技能: {name}\n\n<!-- TRIGGER: {trig} -->\n\n> 由技能创建器生成 {time.strftime('%Y-%m-%d %H:%M')}\n\n"
    sfp.write_text(head + content, encoding="utf-8")
    print(f"ok: 知识技能[{name}]已入库, 场景触发词: {trig}")
else:
    print("err: kind 只支持 tool|knowledge")
