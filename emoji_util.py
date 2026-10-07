# -*- coding: utf-8 -*-
"""emoji_util.py — 给**独立进程**(插件、脚本)用的"普通 emoji → 自定义动画表情"增强。

为什么要单独一份: bot.py 里的 `_enhance_emoji` 只在 bot 进程内存里有效;
而收款卡这类插件是**另起的 python 进程**(subprocess)发消息的, 拿不到那份内存,
所以插件发出去的正文一直是普通 emoji —— 老板 2026-09-18 实拍:
「🐠💔 卖惨加强版 · 15u … 这个自定义没有替换」。

本模块只依赖磁盘上的三个文件(和 bot.py 同一份), 两个进程行为一致:
  /opt/deepseek-bot/premium_emoji.json   字符 → 候选自定义表情 id(可以多个)
  /opt/deepseek-bot/good_emoji_ids.json  真发成功过的 id(动画最稳)
  /opt/deepseek-bot/bad_emoji_ids.json   被 Telegram 拒过的 id(永不使用)

用法:
    import sys; sys.path.insert(0, "/opt/deepseek-bot")
    from emoji_util import enhance
    html = enhance("🐠 卖惨加强版 · 15u 🙏")      # → 带 <tg-emoji> 标签的 HTML
注意: 结果只能进 `parse_mode="HTML"` 的**正文**, 不能进按钮文字/给模型看的纯文本。
"""
import json
import os
import re

POOL_F = "/opt/deepseek-bot/premium_emoji.json"
OK_F = "/opt/deepseek-bot/good_emoji_ids.json"
BAD_F = "/opt/deepseek-bot/bad_emoji_ids.json"
SUS_F = "/opt/deepseek-bot/emoji_suspect_ids.json"

# 和 bot.py 里的 _EMOJI_CHARS 保持一致(改了要一起改)
EMOJI_RE = re.compile(r'[\U0001F300-\U0001FAFF☀-➿⭐❤️]')
# code/pre 里塞标签会把整条 HTML 拧坏 → 这两段不增强(和 bot.py 同款处理)
SEG_RE = re.compile(r'(<pre\b[^>]*>.*?</pre\s*>|<code\b[^>]*>.*?</code\s*>)', re.S)

_pool = None
_ok = set()
_bad = set()
_sus = set()


def _jset(path):
    try:
        with open(path, encoding="utf-8") as f:
            return set(str(x) for x in json.load(f))
    except Exception:
        return set()


def load(force=False):
    """载入池子/白名单(一次就够; force=True 可热更新)"""
    global _pool, _ok, _bad, _sus
    if _pool is not None and not force:
        return _pool
    try:
        with open(POOL_F, encoding="utf-8") as f:
            _pool = json.load(f) or {}
    except Exception:
        _pool = {}
    _ok = _jset(OK_F)
    _bad = _jset(BAD_F)
    _sus = _jset(SUS_F)
    return _pool


def _pick(ch, cands, seed=0):
    """三级挑选(和 bot.py 一致): ①白名单 ②没被拒过的新 id ③可疑的尽量不冒险"""
    cands = [str(x) for x in (cands if isinstance(cands, list) else [cands]) if x]
    if _bad:
        cands = [x for x in cands if x not in _bad]
    if not cands:
        return None
    prev = [x for x in cands if x in _ok]
    mid = [x for x in cands if x not in _ok and x not in _sus]
    if prev:
        cands = prev
    elif mid:
        cands = mid
    elif (seed % 8):
        return None            # 只剩可疑 id: 大部分情况不冒险(免得整条被拒)
    return cands[seed % len(cands)]


def enhance(text, ratio=1.0, seed=0):
    """把正文里的 emoji 换成自定义动画表情(池子里没有的字符保持普通 emoji)"""
    if not text:
        return text
    load()
    if not _pool:
        return text
    s = str(text)
    # 模型/历史里可能带着伪标签 → 先清掉, 由本层重新加(和 bot.py 一样)
    s = re.sub(r'&lt;tg-emoji[^&]*?&gt;|&lt;/tg-emoji&gt;', '', s)
    s = re.sub(r'<tg-emoji[^>]*>|</tg-emoji>', '', s)
    _len_cache = len(s)

    def _rep(m):
        ch = m.group(0)
        chq = ch.replace("\ufe0f", "")
        ids = _pool.get(ch) or (_pool.get(chq) if chq != ch else None)
        if chq != ch and ids:
            ch = chq
        if not ids:
            return ch
        if (sum(ord(c) for c in ch) + _len_cache) % 10 >= ratio * 10:
            return ch                       # ratio<1 时按比例跳过一部分
        got = _pick(ch, ids, seed + sum(ord(c) for c in ch))
        return f'<tg-emoji emoji-id="{got}">{ch}</tg-emoji>' if got else ch

    def _outer(seg):
        if "<" not in seg:
            return EMOJI_RE.sub(_rep, seg)
        out, p = [], 0
        for tm in re.finditer(r"<[^>]+>", seg):
            out.append(EMOJI_RE.sub(_rep, seg[p:tm.start()]))
            out.append(tm.group(0))
            p = tm.end()
        out.append(EMOJI_RE.sub(_rep, seg[p:]))
        return "".join(out)

    def _seg(seg):
        out, last = [], 0
        for sm in SEG_RE.finditer(seg):
            out.append(_outer(seg[last:sm.start()]))
            out.append(sm.group(0))
            last = sm.end()
        out.append(_outer(seg[last:]))
        return "".join(out)

    return _seg(s)


def strip(text):
    """标签 → 普通 emoji(服务端拒收时的兜底)"""
    try:
        return re.sub(r'<tg-emoji emoji-id="\d+">([^<]*)</tg-emoji>', r"\1", str(text))
    except Exception:
        return str(text)


if __name__ == "__main__":       # 自测: python3 emoji_util.py "🐠 卖惨 15u 🙏"
    import sys
    print(enhance(sys.argv[1] if len(sys.argv) > 1 else "🐠💔 卖惨加强版 · 15u 🙏🥺"))
