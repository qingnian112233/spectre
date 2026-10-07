#!/usr/bin/env python3
"""示例插件: 计算文本sha256 — 演示 bot 自装工具链路(声明式插件, 参数JSON作为argv[1])"""
import sys, json, hashlib
try:
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    text = str(args.get("text", ""))
    print(hashlib.sha256(text.encode("utf-8")).hexdigest())
except Exception as e:
    print(f"usage err: {e}")
