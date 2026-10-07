# -*- coding: utf-8 -*-
"""提示词热编辑(prompts) —— 2026-10-03 从 bot.py 拆出来的第一个模块。

老板「我可以在机器人里面改改他的提示词么？然后bot文件大了 可以分开出来」:
  · 注入类提示词放在 knowledge/ 下的 .md, 由 bot 每轮**读文件**注入 → 改完立刻生效, 不用重启、不用改代码。
  · 本模块只负责"文件读写 + 清单 + 面板文案", 不碰 Telegram API; 命令/按钮在 bot.py 侧薄薄一层。
  · 以后要加"可热改的提示词", 只在这里的 FILES 里加一行即可。

三段的位置(模型对提示词"开头和结尾最敏感, 中间最弱"):
    extra  → 系统提示词**最末尾**(权重最高, 用来覆盖任何规则)
    exec   → 中段靠后(接到活怎么干)
    inject → 中段(逐字注入的固定素材)
"""
import os

KNOW = "/opt/deepseek-bot/knowledge"

# key -> (文件名, 一句话, 落在哪, 拿来干什么, 上限字数)
FILES = {
    "extra": ("prompt-extra.md", "追加段", "系统提示词**最末尾**(模型最在意的位置)",
              "改语气口癖 / 加临时禁令 / 指定输出格式 / 塞当前项目背景 —— 日常改规矩就用它", 6000),
    "inject": ("prompt-inject.md", "逐字注入段", "系统提示词中段, 原样拼进去, 每轮都注入",
               "固定素材: 名词口径、称呼、必须遵守的硬规矩", 4000),
    "exec": ("exec-protocol.md", "执行协议", "紧跟在 inject 之后",
             "接到活怎么干: 先做什么、什么时候问、怎么收尾、不许怎么干", 4000),
    # 2026-10-06 老板「机器人提示词设置哪里 我怎么没看见」→ 把人设/称呼段单独登记成一段。
    #   注意: 它是**只有管理员**才注入的(普通用户那份不用那个称呼), 所以走 bot.py 的 _is_admin 分支,
    #   不长在给所有人共用的 inject/exec/extra 里。
    "yandere": ("prompt-yandere.md", "人设·风格(可自定义)", "系统提示词里, 只对管理员注入",
                "人设 / 风格 / 措辞, 可热改。只管怎么说, 不影响做什么", 3000),
}

INTRO = (
    "这三段都是**每轮现读文件**再拼进模型的系统提示词。"
    "所以: 改完**下一句话就生效**, 不用重启、不用改代码。"
)


def path_of(key):
    _f = FILES.get(str(key))
    return os.path.join(KNOW, _f[0]) if _f else ""


def info(key):
    """返回 dict: key/file/one/where/use/path/exists/chars/limit"""
    _k = str(key)
    if _k not in FILES:
        return None
    _fn, _one, _where, _use, _lim = FILES[_k]
    _p = os.path.join(KNOW, _fn)
    try:
        _n = len(open(_p, encoding="utf-8", errors="replace").read()) if os.path.exists(_p) else 0
    except Exception:
        _n = 0
    return {"key": _k, "file": _fn, "one": _one, "where": _where, "use": _use,
            "path": _p, "exists": os.path.exists(_p), "chars": _n, "limit": _lim}


def all_info():
    return [info(_k) for _k in FILES]


def read(key, limit=20000):
    _p = path_of(key)
    if not _p or not os.path.exists(_p):
        return ""
    try:
        return open(_p, encoding="utf-8", errors="replace").read()[:limit]
    except Exception:
        return ""


def write(key, text):
    """整段替换。返回 (ok, 说明)"""
    _p = path_of(key)
    if not _p:
        return False, "未知提示词段"
    _t = str(text or "")
    _lim = FILES[str(key)][4]
    if len(_t) > _lim:
        return False, f"太长({len(_t)} 字 > 上限 {_lim}) —— 精简后再发, 或分段写"
    try:
        os.makedirs(KNOW, exist_ok=True)
        if os.path.exists(_p):
            try:
                import shutil
                import time as _t2
                shutil.copy(_p, _p + ".bak_" + _t2.strftime("%m%d_%H%M%S"))
            except Exception:
                pass
        open(_p, "w", encoding="utf-8", newline="").write(_t)
        return True, f"已写入 {os.path.basename(_p)}({len(_t)} 字) · 下一轮消息立刻生效"
    except Exception as _e:
        return False, f"写入失败: {str(_e)[:80]}"


def clear(key):
    return write(key, "")


def append(key, text):
    _old = read(key)
    _sep = "\n\n" if _old.strip() else ""
    return write(key, (_old.rstrip() + _sep + str(text or "")).strip())


def panel_lines():
    """面板正文(纯文本行, 由 bot.py 侧拼 HTML)。错误键 __err__ 单独返回。"""
    _L = ["📝 可热改的提示词 —— 改完下一轮立刻生效, 不用重启", "", INTRO, ""]
    for _it in all_info():
        _L.append(f"▎{_it['key']} · {_it['one']}   ({_it['chars']}/{_it['limit']} 字)")
        _L.append(f"   落在: {_it['where']}")
        _L.append(f"   用途: {_it['use']}")
        _L.append(f"   文件: {_it['file']}" + ("" if _it["exists"] else "  (还没建, 点「✏️ 改」即新建)"))
        _L.append("")
    _L += [
        "━━ 怎么用 ━━",
        "· 点「✏️ 改」→ 把**整段新内容**当普通消息发过来(整段替换, 不是追加)",
        "· 点「👁 看」先看现在写了什么, 再决定怎么改",
        "· 命令也行: /prompt put extra 内容 · /prompt get extra · /prompt clear extra",
        "· 只在**私聊**里生效; 群里说的话不会被当成提示词内容",
        "· 每次写入都会自动留一份 .bak, 改坏了能翻回来",
    ]
    return _L
