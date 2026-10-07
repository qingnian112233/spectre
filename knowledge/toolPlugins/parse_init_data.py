#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 自扩展生成 2026-10-05 16:38 | 解析Telegram WebApp initData字符串
# 用法: 参数以 JSON 从 sys.argv[1] 传入, print 的内容即工具返回
import sys, json, re
args = sys.argv[1] if len(sys.argv) > 1 else '{}'
try:
    p = json.loads(args)
except:
    p = {'init_data': args}
init = p.get('init_data', '')
parts = init.split('&')
d = {}
for kv in parts:
    if '=' in kv:
        k, v = kv.split('=', 1)
        d[k] = v
h = d.get('hash', '')
data_str = '\n'.join(f'{k}={v}' for k, v in d.items() if k != 'hash')
print(json.dumps({'data': data_str, 'hash': h, 'fields': d}, ensure_ascii=False))