#!/usr/bin/env python3
"""
obscura_render tool plugin.
用本机 Obscura 无头浏览器(Rust)渲染抓取动态/JS 页面。
当 action=text|html|markdown|links 默认走 --dump;action=cookies 走 --dump cookies;
action=screenshot 走 -s。 URL 必填;eval 可选传 JS 表达式。
用法: 由 bot 框架以 JSON argv[1] 调用。
"""
import json, sys, subprocess, os

def main():
    try:
        argv = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except Exception:
        argv = {}
    url = argv.get("url") or argv.get("URL")
    if not url:
        print("需要 url")
        return
    action = argv.get("action", "text")  # text/html/markdown/links/cookies/screenshot/original/assets
    eval_js = argv.get("eval", "")
    out_png = argv.get("out", "/tmp/obscura_render.png")

    allowed = {"text","html","markdown","links","cookies","original","assets"}
    if action not in allowed and action != "screenshot":
        action = "text"

    cmd = ["obscura", "--stealth", "--allow-private-network", "fetch", url]
    if action == "screenshot":
        cmd += ["-s", out_png]
    elif action in allowed:
        cmd += ["--dump", action]
    if eval_js:
        cmd += ["--eval", eval_js]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=int(argv.get("timeout", 90)))
        print(r.stdout[-6000:] if r.stdout else r.stderr[-2000:])
    except subprocess.TimeoutExpired:
        print("obscura 超时")

if __name__ == "__main__":
    main()
