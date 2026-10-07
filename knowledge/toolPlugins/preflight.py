#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开工前技能体检 (preflight) —— 抄自 dsh-redteam-mode 的 skill-availability.js。

原项目一句话: "技能能列出来 ≠ 能跑。缺 FOFA_KEY、工具没落到本机、VPS 还是占位符，
都要等真正动手才发现，那时候人已经在靶场里了。"

本鱼的体检维度:
  ① 工具链是否在 PATH / 磁盘上（nmap/sqlmap/ffuf/hydra/john/nuclei/msf 等）
  ② 必需环境变量是否设置（FOFA_KEY / VPS / proxy 等）
  ③ 知识库关键文件是否存在（pentest.md / redteam-作战.md / SPECTRE / src-hunting）
  ④ 外部基础设施占位符是否还没填（<你的VPS_IP> TARGET HOST 之类）
输出: 每项 available / broken + 缺什么 + 怎么办（降级方案）
"""
import sys, json, os, shutil

HOME = os.environ.get("DSH_HOME") or "/opt/deepseek-bot"
KB = os.path.join(HOME, "knowledge")

TOOLS = {
    "nmap": "端口与服务识别", "masscan": "大网段高速扫描", "sqlmap": "SQL注入", "ffuf": "目录/参数爆破",
    "gobuster": "目录爆破", "dirsearch": "目录爆破", "nuclei": "模板化漏洞扫描", "hydra": "在线爆破",
    "john": "离线哈希破解", "hashcat": "GPU哈希破解", "whatweb": "Web指纹", "nikto": "Web服务器体检",
    "httpx": "存活/标题探测", "katana": "爬虫", "subfinder": "子域枚举", "fscan": "内网综合扫描",
    "gogo": "内网铺面", "chisel": "隧道", "msfconsole": "Metasploit", "impacket-smbexec": "横向(impacket)",
    "evil-winrm": "WinRM横向", "frida": "动态插桩", "ghidra": "逆向", "python3": "脚本运行时",
    "curl": "HTTP", "git": "拉源码",
}
ENV_NEEDS = {
    "FOFA_KEY": "FOFA 资产测绘（缺了就只能靠免费源+url工具）",
    "DSH_HOME": "工作区根目录（缺了默认 /opt/deepseek-bot）",
}
KB_FILES = [
    "pentest.md", "redteam-作战.md", "SPECTRE-全谱攻击面作战总纲.md",
    "src-hunting-总纲.md", "quick-payloads.md", "react-nextjs-rce-family.md",
    "redteam-deep-exploitation-ch9.md",
]
KB_DIRS = ["src-arsenal", "secatlas", "CyberSecurity-Skills", "reverse-skill", "gh-mine", "self_learned"]


def main():
    a = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    skill = a.get("skill", "")  # 可选: 指定技能名，额外判定该技能正文里的 $VAR / 路径 / 占位符

    tools, missing_tools = {}, []
    for t, desc in TOOLS.items():
        p = shutil.which(t)
        tools[t] = {"path": p, "desc": desc, "status": "available" if p else "missing"}
        if not p:
            missing_tools.append(t)

    envs = {k: {"set": bool(os.environ.get(k)), "hint": v,
                "status": "available" if os.environ.get(k) else "broken"} for k, v in ENV_NEEDS.items()}

    kbf = []
    for f in KB_FILES:
        p = os.path.join(KB, f)
        kbf.append({"file": f, "exists": os.path.exists(p),
                    "size": os.path.getsize(p) if os.path.exists(p) else 0,
                    "status": "available" if os.path.exists(p) else "broken"})
    kbd = [{"dir": d, "exists": os.path.isdir(os.path.join(KB, d)),
            "status": "available" if os.path.isdir(os.path.join(KB, d)) else "broken"} for d in KB_DIRS]

    problems, downgrade = [], []
    if missing_tools:
        problems.append(f"缺工具: {', '.join(missing_tools)}")
        for t in missing_tools[:8]:
            downgrade.append(f"{t} 缺失 → 用 {TOOLS[t]} 的替代方案或本机其他工具顶")
    broken_envs = [k for k, v in envs.items() if v["status"] == "broken"]
    if broken_envs:
        problems.append(f"缺环境变量: {', '.join(broken_envs)}")
        for k in broken_envs:
            downgrade.append(f"{k} 未设置 → {ENV_NEEDS[k]}")

    skill_check = None
    if skill:
        # 按名字在知识库里找技能正文
        hits = []
        for base in (KB, os.path.join(KB, "src-arsenal", "skills", "skill", "知识库"),
                     os.path.join(KB, "CyberSecurity-Skills")):
            for root, _, files in os.walk(base):
                if skill in root or skill in os.path.basename(root):
                    for fn in files:
                        if fn.endswith(".md") and skill.split("/")[-1][:6] in fn:
                            hits.append(os.path.join(root, fn))
        need_env, ph, paths = [], [], []
        for h in hits[:3]:
            try:
                body = open(h, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            import re
            for m in re.findall(r"\$\{?([A-Z][A-Z0-9_]{2,})\}?", body):
                if m not in need_env:
                    need_env.append(m)
            for m in re.findall(r"[<【]([^<>【】]{2,20}(?:IP|地址|域名|VPS|TARGET|HOST)[^<>【】]{0,10})[>】]", body):
                ph.append(m)
            paths += re.findall(r"/(?:opt|tmp|home)[\w./-]{3,60}", body)[:10]
        skill_check = {"matched_files": hits[:5], "required_vars": need_env,
                       "unfilled_placeholders": sorted(set(ph))[:10],
                       "referenced_paths": sorted(set(paths))[:10],
                       "missing_vars": [v for v in need_env if not os.environ.get(v)],
                       "missing_paths": [p for p in sorted(set(paths)) if not os.path.exists(p)][:10]}
        if skill_check["missing_vars"]:
            problems.append(f"技能 {skill} 必需变量未设: {', '.join(skill_check['missing_vars'])}")
        if skill_check["missing_paths"]:
            problems.append(f"技能 {skill} 引用的路径不存在: {', '.join(skill_check['missing_paths'][:5])}")
        if skill_check["unfilled_placeholders"]:
            problems.append(f"技能 {skill} 占位符未填: {', '.join(skill_check['unfilled_placeholders'][:5])}")

    avail = sum(1 for v in tools.values() if v["status"] == "available")
    print(json.dumps({
        "verdict": "ready" if not problems else "degraded",
        "summary": f"{avail}/{len(TOOLS)} 工具就绪 · 知识库 {sum(1 for x in kbf if x['exists'])}/{len(kbf)} 关键文件在位",
        "tools": tools, "missing_tools": missing_tools,
        "env": envs, "kb_files": kbf, "kb_dirs": kbd,
        "problems": problems, "downgrade": downgrade,
        "skill": skill_check,
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
