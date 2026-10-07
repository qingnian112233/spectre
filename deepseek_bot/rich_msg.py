"""Rich Message via Bot API 10.2 — tables, headings, collages, math, maps, details, etc."""
import httpx, json, re

import os
BOT_API = f"https://api.telegram.org/bot{os.getenv('DEEPSEEK_BOT_TOKEN','')}"  # 2026-09-02: 去掉硬编码token fallback(token只从.env读, 防泄露)

async def send_rich_html(chat_id, text):
    """Alias for try_send_rich — kept for compatibility"""
    return await try_send_rich(chat_id, text)

async def try_send_rich(chat_id, text):
    """HTML发送 — markdown转TG兼容HTML：表格转文本行，标题转粗体，去掉#/|"""
    html = _md_to_html(text)
    html = html[:4000]
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            resp = await cl.post(f"{BOT_API}/sendMessage", json={
                "chat_id": chat_id, "text": html, "parse_mode": "html"
            })
            if resp.status_code == 200:
                return True
            # HTML 被拒时降级纯文本（去掉#和表格符号）
            plain = _md_to_plain(text)[:4000]
            resp2 = await cl.post(f"{BOT_API}/sendMessage", json={
                "chat_id": chat_id, "text": plain
            })
            return resp2.status_code == 200
    except Exception:
        pass
    return False

def _md_to_plain(text: str) -> str:
    """Markdown → 纯可读文本：去掉 #、|、** 等标记"""
    text = _strip_model_html(text)
    lines = text.split('\n')
    out = []
    for line in lines:
        s = line.strip()
        if not s:
            out.append(''); continue
        # 表格分隔行跳过
        if re.match(r'^\|[-:\s|]+\|$', s):
            continue
        # 表格行 | a | b | → a · b
        if s.startswith('|') and s.endswith('|'):
            cells = [c.strip() for c in s[1:-1].split('|')]
            out.append('  ' + ' · '.join(cells))
            continue
        # 标题 # ## ### → 粗体语义（纯文本下直接去#）
        s = re.sub(r'^#{1,6}\s+', '', s)
        # 列表
        if s.startswith('- '):
            s = '  • ' + s[2:]
        # 去标记
        s = s.replace('**', '').replace('*', '').replace('`', '')
        s = re.sub(r'~~([^~]+?)~~', r'\1', s)          # 删除线
        s = re.sub(r'(?<!_)\b__([^_]+?)__\b', r'\1', s)  # 下划线
        s = re.sub(r'\|\|([^|]+?)\|\|', r'\1', s)        # 剧透
        s = re.sub(r'\[([^\]]+?)\]\((https?://[^)]+)\)', r'\1(\2)', s)  # 链接保留文字
        s = re.sub(r'<tg-spoiler>|</tg-spoiler>', '', s)  # 原生剧透标签
        s = re.sub(r'<blockquote>|</blockquote>', '', s)  # 引用标签
        out.append(s)
    return '\n'.join(out)

def _strip_model_html(text: str) -> str:
    """剥掉模型自写的HTML标签(它学坏了会输出<b>/<tg-emoji>原文), 只留md语法。
    不剥则被转义成&lt;b&gt;显示为字面标签(裸标签根因)
    tg-spoiler为Telegram合法标签, 保留不剥(适配全量富文本)"""
    t = re.sub(r'<tg-emoji[^>]*>|</tg-emoji>', '', text)
    t = re.sub(r'</?(?:b|i|u|code|pre|s|a|span|blockquote)[^>]*>', '', t)
    t = re.sub(r'&lt;(?:tg-emoji|/tg-emoji|/?b|/?i|/?u|/?code|/?pre|/?s|/?a|/?span|/?blockquote)[^&]*?&gt;', '', t)
    return t

def _md_to_html(text: str) -> str:
    """Quick markdown to HTML for fallback: 表格转文本行，标题转粗体。"""
    text = _strip_model_html(text)
    # 2026-09-02: 模型手写非TG标准标签归一(em→i / del→s / strike→s), 否则解析异常或源码裸奔
    text = re.sub(r'<em[^>]*>', '<i>', text)
    text = re.sub(r'</em>', '</i>', text)
    text = re.sub(r'<del[^>]*>', '<s>', text)
    text = re.sub(r'</del>', '</s>', text)
    text = re.sub(r'<strike[^>]*>', '<s>', text)
    text = re.sub(r'</strike>', '</s>', text)
    lines = text.split('\n')
    out = []
    in_table = False
    in_code = False
    for line in lines:
        s = line.strip()
        # 代码块 ``` ... ``` (monospace)
        if s.startswith('```'):
            if in_code:
                out.append('</pre>')
                in_code = False
            else:
                out.append('<pre>')
                in_code = True
            continue
        if in_code:
            out.append(line)
            continue
        # 表格: | col | col | (排除 ||剧透|| 语法)
        if s.startswith('|') and s.endswith('|') and not s.startswith('||'):
            # 分隔行 |---|---|
            if re.match(r'^\|[-:\s|]+\|$', s):
                continue
            cells = [c.strip().replace('**', '') for c in s[1:-1].split('|')]
            # 2026-09-11 表格单元格里的行内标记以前不处理 → 反引号/粗体原样漏给用户(实测: "scanme.nmap.org (45.33.32.156)`")
            cells = [re.sub(r'`([^`\n]+)`', r'<code>\1</code>',
                            re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', c)).replace('`', '') for c in cells]
            # 表头行加粗
            if not in_table:
                cells = [f'<b>{c}</b>' if c else c for c in cells]
                in_table = True
            line_out = '  ' + ' · '.join(cells)
            out.append(line_out)
            continue
        else:
            in_table = False
        # 删除线 ~~x~~ (先于其他, 避免与**/__冲突)
        line = re.sub(r'~~([^~\n]+?)~~', r'<s>\1</s>', line)
        # 下划线 __x__
        line = re.sub(r'(?<!_)\b__([^_\n]+?)__\b', r'<u>\1</u>', line)
        # 剧透 ||x|| (TG原生) — 模型也可能直接写<tg-spoiler>, 保留之
        line = re.sub(r'\|\|([^|\n]+?)\|\|', r'<tg-spoiler>\1</tg-spoiler>', line)
        # 链接 [文字](url)
        line = re.sub(r'\[([^\]]+?)\]\((https?://[^)\s]+)\)', r'<a href="\2">\1</a>', line)
        # @提及: @用户名 TG自动识别, 无需转换
        # Bold
        line = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', line)
        # Italic
        line = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)', r'<i>\1</i>', line)
        # Inline code
        line = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', line)
        # Headings
        if line.startswith('### '): line = f'<b><u>{line[4:]}</u></b>'
        elif line.startswith('## '): line = f'<b>{line[3:]}</b>'
        elif line.startswith('# '): line = f'<b>{line[2:]}</b>'
        # 引用 > xxx → blockquote
        elif line.strip().startswith('> '):
            line = f'<blockquote>{line.strip()[2:]}</blockquote>'
        # List
        elif line.strip().startswith('- '): line = f'  • {line.strip()[2:]}'
        # 2026-09-11 兜底: 行内代码已在上一步转成 <code>, 此时还剩下的反引号必然是落单的 → 去掉(否则用户看到裸 `)
        if '`' in line:
            line = line.replace('`', '')
        out.append(line)
    if in_code:
        out.append('</pre>')  # 未闭合代码块兜底
    _h = '\n'.join(out)
    # 转义裸< >(代码/比较式如a<b会导致TG拒收html), 再还原本函数生成的合法标签
    import html as _html
    _h = _html.escape(_h)
    # 先解属性里的 &quot;(&lt;a href=&quot;...&quot;&gt;还原), 再还原标签
    _h = _h.replace('&quot;', chr(34)).replace('&#39;', chr(39))
    _h = re.sub(r'&lt;(/?(?:b|i|u|code|pre|s|a|blockquote|tg-spoiler)\b[^&]*?)&gt;', r'<\1>', _h)
    # 还原模型自带的HTML实体(它学会了自己写&quot;等, 二次转义会显示乱码)
    _h = _h.replace('&amp;quot;', '&quot;').replace('&amp;gt;', '&gt;').replace('&amp;lt;', '&lt;').replace('&amp;amp;', '&amp;').replace('&quot;', chr(34)).replace('&#39;', chr(39))
    return _h


def _md_segments(text: str) -> list:
    """行内md → rich-text段: **粗体** / `代码` 拆成segments"""
    segs=[]; i=0
    for m in re.finditer(r'\*\*(.+?)\*\*|`([^`\n]+)`', text):
        if m.start()>i: segs.append(text[i:m.start()])
        if m.group(1): segs.append({"type":"bold","text":m.group(1)})
        else: segs.append({"type":"code","text":m.group(2)})
        i=m.end()
    if i<len(text): segs.append(text[i:])
    return segs or [text]

def send_rich_markdown(chat_id, md_text, reply_to=None, reply_markup=None, thread=None):
    """2026-09-11 Bot API 10.x: 直接发 markdown, 让 Telegram 原生渲染(表格/标题/代码块/引用/列表/分隔线)。

    实测 {"rich_message": {"markdown": "..."}} → 200 ✓(自建 blocks 结构容易踩 "can't parse InputRichBlock")。
    比旧 HTML+entities 路径强在: **表格是真表格**(不再降级成 "a · b" 文本行), 且不受实体 offset 那套复杂度影响。
    reply_markup: 可直接挂 inline 键盘(操作按钮跟结果同一条消息, 不用再发"👇"那种多余消息)。
    2026-09-14 加 thread: 私聊话题号 → 富文本回答也要发进对应工作台(否则掉进"全部对话")。
    失败返回 False → 调用方回退原 HTML 路径。
    """
    import httpx as _hxr
    try:
        _p = {"chat_id": chat_id, "rich_message": {"markdown": str(md_text)[:8000]}}
        if thread:
            _p["message_thread_id"] = int(thread)
        if reply_to:
            # 2026-09-16: 引用的消息被删时 Telegram 报 "message to be replied not found" 整条 400 →
            # 富文本回答直接消失(只剩心跳)。带 allow_sending_without_reply, 引用不到也照发。
            _p["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        if reply_markup:
            _p["reply_markup"] = reply_markup
        r = _hxr.post(f"{BOT_API}/sendRichMessage", json=_p, timeout=25)
        if r.status_code != 200 and reply_to:
            # 仍然失败 → 去掉引用再试一次(宁可没有引用, 也不能没有结果)
            try:
                _p2 = {_k: _v for _k, _v in _p.items() if _k != "reply_parameters"}
                r = _hxr.post(f"{BOT_API}/sendRichMessage", json=_p2, timeout=25)
            except Exception:
                pass
        if r.status_code == 200:
            return True
        try:
            _d = (r.json() or {}).get("description", "")
        except Exception:
            _d = (r.text or "")[:120]
        print(f"[rich] markdown 发送失败({r.status_code}): {str(_d)[:150]}", flush=True)
        return False
    except Exception as _e:
        print(f"[rich] markdown 异常: {_e}", flush=True)
        return False


def rich_to_text(rich, _depth=0):
    """2026-09-11 Rich Message → 可读文本(markdown 风格)。

    背景: 富消息(用户或 bot 自己发的)在 Telethon 里 `message.text` 是**空的**, 内容全在
    `message.rich_message`(RichMessage.blocks, 元素形如 PageBlockParagraph/TextPlain/TextBold/
    TextCustomEmoji(document_id, alt)/PageBlockTable/PageBlockPreformatted…)。
    不做这个转换的话, 收到富消息会当成"没内容"——用户体验就是消息被吞。
    """
    if rich is None:
        return ""
    if _depth > 6:
        return ""
    try:
        # RichMessage → blocks
        if hasattr(rich, "blocks"):
            _out = []
            for b in (rich.blocks or []):
                _t = _rich_block_text(b, _depth + 1)
                if _t:
                    _out.append(_t)
            return "\n\n".join(_out).strip()
        # 以下为递归处理块/文本节点
        return _rich_block_text(rich, _depth + 1)
    except Exception:
        return ""


def _rich_block_text(b, _depth=0):
    if b is None or _depth > 8:
        return ""
    try:
        cn = type(b).__name__
        # 纯文本节点
        if cn == "TextPlain":
            return getattr(b, "text", "") or ""
        if cn in ("TextBold", "TextItalic", "TextUnderline", "TextStrikethrough", "TextCode",
                  "TextSpoiler", "TextMarked", "TextSubscript", "TextSuperscript", "TextAnchor"):
            inner = _rich_block_text(getattr(b, "text", None), _depth + 1) or \
                    _rich_join(getattr(b, "texts", None), _depth)
            mark = {"TextBold": "**", "TextItalic": "*", "TextUnderline": "__", "TextStrikethrough": "~~",
                    "TextCode": "`", "TextSpoiler": "||", "TextMarked": "=="}.get(cn, "")
            return f"{mark}{inner}{mark}" if mark else inner
        # 自定义表情: 用它的替代文本(用户看到的就是这个字符)
        if cn == "TextCustomEmoji":
            return getattr(b, "alt", "") or "🙂"
        if cn in ("TextUrl", "TextEmailAddress", "TextPhoneNumber", "TextMention", "TextHashtag",
                  "TextCashtag", "TextBotCommand", "TextTextMention"):
            inner = _rich_block_text(getattr(b, "text", None), _depth + 1)
            _u = getattr(b, "url", None)
            return f"[{inner}]({_u})" if _u else inner
        if cn == "TextConcat":
            return _rich_join(getattr(b, "texts", None), _depth)
        if cn in ("TextEmpty", "TextImage", "TextMath", "TextBankCardNumber"):
            return ""
        # 容器: 有 texts 就拼, 有 text 就递归, 有 blocks 就逐块
        if hasattr(b, "texts") and getattr(b, "texts", None):
            return _rich_join(b.texts, _depth)
        if hasattr(b, "text") and getattr(b, "text", None) is not None:
            return _rich_block_text(b.text, _depth + 1)
        if hasattr(b, "blocks") and getattr(b, "blocks", None):
            return "\n".join(x for x in (_rich_block_text(k, _depth + 1) for k in b.blocks) if x)
        # 表格: 行 → markdown 表格
        if cn == "PageBlockTable":
            rows = getattr(b, "rows", None) or []
            lines = []
            for i, r in enumerate(rows):
                cells = getattr(r, "cells", None) or []
                vals = [(_rich_block_text(c, _depth + 1) or "").replace("\n", " ") for c in cells]
                lines.append("| " + " | ".join(vals) + " |")
                if i == 0:
                    lines.append("|" + "---|" * len(vals))
            return "\n".join(lines)
        if cn in ("PageBlockList", "PageBlockOrderedList"):
            items = getattr(b, "items", None) or []
            out = []
            for i, it in enumerate(items):
                blks = getattr(it, "blocks", None) or []
                t = " ".join(x for x in (_rich_block_text(k, _depth + 1) for k in blks) if x)
                if t:
                    out.append(f"{i+1}. {t}" if cn == "PageBlockOrderedList" else f"- {t}")
            return "\n".join(out)
        if cn in ("PageBlockPreformatted",):
            inner = _rich_block_text(getattr(b, "text", None), _depth + 1)
            return f"```\n{inner}\n```"
        if cn in ("PageBlockBlockquote", "PageBlockPullquote", "PageBlockExpandableBlockquote"):
            inner = _rich_join(getattr(b, "texts", None), _depth) or _rich_block_text(getattr(b, "text", None), _depth + 1)
            return "\n".join("> " + l for l in (inner or "").split("\n"))
        if cn in ("PageBlockHeading", "PageBlockSectionHeading", "PageBlockHeader", "PageBlockSubheader"):
            inner = _rich_join(getattr(b, "texts", None), _depth) or _rich_block_text(getattr(b, "text", None), _depth + 1)
            lvl = 1 if cn in ("PageBlockHeading", "PageBlockHeader") else 2
            return "#" * lvl + " " + (inner or "")
        if cn in ("PageBlockDivider",):
            return "---"
        # 兜底: 递归所有可能字段
        for _f in ("text", "texts", "blocks", "caption", "rows", "items", "title"):
            v = getattr(b, _f, None)
            if v:
                t = _rich_block_text(v, _depth + 1) if not isinstance(v, list) else _rich_join(v, _depth)
                if t:
                    return t
        return ""
    except Exception:
        return ""


def _rich_join(lst, _depth=0):
    if not lst:
        return ""
    return "".join(_rich_block_text(x, _depth + 1) for x in lst)


def msg_text(msg):
    """取一条消息的可读文本: 普通文本优先, 富消息则解析 rich_message(2026-09-11)"""
    try:
        t = msg.text or ""
        if t:
            return t
        r = getattr(msg, "rich_message", None)
        if r is not None:
            return rich_to_text(r)
        return ""
    except Exception:
        return ""


def send_rich(chat_id, text):
    """Bot API 10.x 结构化富消息: md→blocks→sendRichMessage; 失败返回False(调用方回退旧HTML)"""
    import httpx as _hxr
    try:
        blocks=_parse_rich_blocks(text) or []
        out=[]
        for b in blocks:
            t=b.get("type")
            if t=="heading":
                out.append({"type":"section_heading","text":[{"type":"bold","text":b.get("text","")}]})
            elif t=="table":
                # 表格转文本行(富块table字段复杂, 保守处理为段落)
                rows=[(" · ".join(c.get("text","") for c in row)) for row in b.get("cells",[])]
                out.append({"type":"paragraph","text":"\n".join(rows)})
            elif t=="pre":
                out.append({"type":"preformatted","text":b.get("text","")})
            elif t=="pullquote":
                out.append({"type":"pull_quotation","text":_md_segments(b.get("text",""))})
            elif t=="details":
                inner=[{"type":"paragraph","text":sb.get("text","")} for sb in b.get("blocks",[])]
                out.append({"type":"details","heading":b.get("heading",""),"blocks":inner})
            else:  # paragraph
                out.append({"type":"paragraph","text":_md_segments(b.get("text",""))})
        if not out: return False
        payload={"chat_id":chat_id,"rich_message":{"blocks":out}}
        r=_hxr.post(f"{BOT_API}/sendRichMessage", json=payload, timeout=20)
        return r.status_code==200
    except Exception:
        return False

def _parse_rich_blocks(text: str) -> list:
    """Parse markdown into Bot API 7 rich block types."""
    lines = text.strip().split('\n')
    blocks = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1; continue

        # Heading
        if line.startswith('# ') or line.startswith('## ') or line.startswith('### '):
            level = len(line) - len(line.lstrip('#'))
            txt = line.lstrip('#').strip().replace('**', '')
            blocks.append({"type": "heading", "size": min(level, 3), "text": txt})
            i += 1; continue

        # Table: | col | col |
        if line.startswith('|') and line.endswith('|'):
            cells_rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                row_line = lines[i].strip()
                if re.match(r'^\|[-:\s|]+\|$', row_line):
                    i += 1; continue
                cells = [{"text": c.strip().replace('**','')} for c in row_line[1:-1].split('|')]
                cells_rows.append(cells)
                i += 1
            if cells_rows:
                blocks.append({"type": "table", "cells": cells_rows})
            continue

        # Code block: ``` ... ```
        if line.startswith('```'):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith('```'):
                code_lines.append(lines[i])
                i += 1
            i += 1
            if code_lines:
                blocks.append({"type": "pre", "text": '\n'.join(code_lines)})
            continue

        # Pullquote
        if line.startswith('> '):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith('> '):
                ql = lines[i].strip()[2:]
                ql = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', ql)
                quote_lines.append(ql)
                i += 1
            if quote_lines:
                blocks.append({"type": "pullquote", "text": '\n'.join(quote_lines)})
            continue

        # Details: ??? heading then content
        if line.startswith('??? '):
            heading = line[4:]
            i += 1
            detail_lines = []
            while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(('#', '|', '```', '> ', '??? ')):
                detail_lines.append(lines[i].strip())
                i += 1
            inner = _parse_rich_blocks('\n'.join(detail_lines)) if detail_lines else [{"type": "paragraph", "text": ""}]
            blocks.append({"type": "details", "heading": heading, "blocks": inner})
            continue

        # Divider: ---
        if line.startswith('---') or line == '---':
            # Don't add footer blocks between sections, skip dividers
            i += 1; continue

        # Paragraph — strip markdown, keep plain text
        para_lines = []
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(('#', '|', '```', '> ', '??? ')):
            pl = lines[i].strip()
            # Strip ** markers (rich blocks have their own formatting)
            pl = pl.replace('**', '')
            pl = re.sub(r'`([^`\n]+)`', r'\1', pl)
            para_lines.append(pl)
            i += 1
        if para_lines:
            blocks.append({"type": "paragraph", "text": '\n'.join(para_lines)})
        continue

    return blocks if blocks else None
