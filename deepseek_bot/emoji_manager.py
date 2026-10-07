"""Emoji Manager - stub"""
import json, os, re
EMOJI_MAP = {}
EMOJI_KW = {}

def load_emoji(path=None):
    try:
        if not path: path = "/opt/deepseek-bot/emoji_map.json"
        if os.path.exists(path):
            with open(path) as f: EMOJI_MAP.update(json.load(f))
    except: pass
    try:
        kw = "/opt/deepseek-bot/emoji_kw_index.json"
        if os.path.exists(kw):
            with open(kw) as f: EMOJI_KW.update(json.load(f))
    except: pass

def render_text(text):
    if not EMOJI_MAP: load_emoji()
    for emoji_char, eid in list(EMOJI_MAP.items())[:200]:
        if emoji_char in text:
            text = text.replace(emoji_char, f'<tg-emoji emoji-id="{eid}">{emoji_char}</tg-emoji>')
    return text

def search(kw, limit=10): return []
def get_emoji_id(c): return EMOJI_MAP.get(c, "")
def list_by_keyword(kw): return search(kw)
