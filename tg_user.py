#!/usr/bin/env python3
"""
🔥 TG 双号控制面板 — 王老板 + 奶蛙
用法:
  python3 tg_user.py wang dialogs [数量]           王老板-最近对话
  python3 tg_user.py wang messages <群> [条数] [起始消息ID] [raw]     王老板-读群消息 (raw=输出完整JSON含reply_markup)
  python3 tg_user.py wang msg <群> <消息ID> [raw]    王老板-按消息ID读单条(含发送者/时间/媒体/按钮)
  python3 tg_user.py wang send <目标> <消息文本>    王老板-发消息
  python3 tg_user.py wang click <群> <消息ID> <行> <列>   王老板-点击消息按钮
  python3 tg_user.py wang buttons <群> <消息ID>      王老板-查看消息按钮
  python3 tg_user.py wang webapp <bot用户名>        王老板-直接打开bot的WebApp拿URL/initData
  python3 tg_user.py wang webapp <群> <消息ID>       王老板-从消息按钮打开小程序
  python3 tg_user.py wang initdata <bot_token>      王老板-构造WebApp initData
  python3 tg_user.py wang user <用户名/手机号/ID>   王老板-查用户
  python3 tg_user.py wang search <群> <关键词>      王老板-搜索消息
  python3 tg_user.py wang stats <群名> [条数] [起始消息ID]       王老板-群活跃统计
  python3 tg_user.py wang whois <群名> [条数] [起始消息ID]       王老板-发言排行
  python3 tg_user.py wang members <群名> [条数]     王老板-群成员列表
  python3 tg_user.py wang join <群链接/邀请码>       王老板-加群
  python3 tg_user.py wang react <群> <消息ID> <👍/❤️/🔥/😂/💩>  王老板-点赞/反应
  
  # 奶蛙同理: python3 tg_user.py naiwa ...

账号:
  wang   = AAA王老板 (+88801569347)   @leileyiqie
  naiwa  = 奶蛙 (+12489004182)       @saobidawang
"""

import os
import sys
import json
import asyncio
from datetime import datetime, timezone
from collections import Counter
from telethon import TelegramClient
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest, SendReactionRequest, RequestWebViewRequest
from telethon.tl.types import PeerUser
from telethon.tl.types import PeerChannel, PeerChat, ReactionEmoji
from telethon.errors import SessionPasswordNeededError

API_ID = 2047129
API_HASH = "c8c5c2f0e4f1e3a6b9d8f7e6a5b4c3d2"

ACCOUNTS = {
    "wang": {
        "session": "/opt/deepseek-bot/sessions/myqb_bot2.session",
        "name": "AAA王老板",
        "phone": "+88801569347",
        "username": "@leileyiqie",
        "tg_id": 0,
    },
    "naiwa": {
        "session": "/opt/deepseek-bot/sessions/naiwa_12489004182.session",
        "name": "奶蛙",
        "phone": "+12489004182",
        "username": "@saobidawang",
        "tg_id": 6519806131,
    },
}


def get_all_usernames(user):
    """获取用户所有用户名（普通 + Fragment/NFT）"""
    parts = []
    if user.username:
        parts.append(f"@{user.username}")
    if hasattr(user, 'usernames') and user.usernames:
        for un in user.usernames:
            ustr = f"@{un.username}"
            if ustr not in parts:
                tag = ustr if getattr(un, 'active', True) else f"{ustr}[非活跃]"
                parts.append(tag)
    return ' '.join(parts) if parts else ''


def extract_msg_text(m):
    """提取消息文本：优先 msg.message，空则从 to_dict() 的 rich_message.blocks 递归掏（新版富文本）"""
    if m is None: return ''
    t = getattr(m, 'message', '') or ''
    if t: return t
    try:
        md = m.to_dict()
    except:
        return ''
    rm = md.get('rich_message') or md.get('richMessage')
    if not rm: return ''
    out = []
    def walk(node):
        if isinstance(node, dict):
            for k in ('text', 'title', 'subtitle', 'caption', 'footer'):
                v = node.get(k)
                if isinstance(v, dict):
                    walk(v)
                elif isinstance(v, str) and v:
                    out.append(v)
            for k in ('blocks', 'items', 'rows', 'columns', 'cells', 'contents', 'texts'):
                v = node.get(k)
                if isinstance(v, list):
                    for item in v:
                        walk(item)
                elif isinstance(v, dict):
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(rm)
    return ''.join(out)


async def get_client(account_name):
    cfg = ACCOUNTS.get(account_name)
    if not cfg:
        print(f"ERROR: 未知账号 '{account_name}'，可用: wang / naiwa")
        return None, None
    client = TelegramClient(cfg["session"], API_ID, API_HASH)
    client.flood_sleep_threshold = 15  # 2026-09-08: 120→15, 防 FloodWait 静默干等120s(双号操作卡顿元凶之一)
    await client.connect()
    if not await client.is_user_authorized():
        print(f"ERROR: {cfg['name']} Session 未授权")
        return None, None
    return client, cfg


async def cmd_dialogs(client, cfg, limit=20):
    dialogs = await client.get_dialogs(limit=limit)
    print(f"\n📒 {cfg['name']} 最近 {len(dialogs)} 个对话:")
    print(f"{'='*70}")
    for i, d in enumerate(dialogs):
        etype = "👤" if d.is_user else ("👥" if d.is_group else ("📢" if d.is_channel else "?"))
        name = d.name[:50] if d.name else "(无名称)"
        eid = d.entity.id if hasattr(d.entity, 'id') else "?"
        print(f"[{i:2d}] {etype} {name:40s} | ID:{str(eid):>15s} | 未读:{d.unread_count}")


async def cmd_messages(client, cfg, target, limit=50, offset_id=None, raw=False):
    limit = min(int(limit), 500)
    try:
        entity = await client.get_entity(target)
    except:
        dialogs = await client.get_dialogs()
        found = None
        for d in dialogs:
            if d.name and target.lower() in d.name.lower():
                found = d.entity
                break
        if not found:
            try:
                entity = await client.get_entity(int(target))
            except:
                print(f"ERROR: 找不到 '{target}'")
                return
        else:
            entity = found

    name = getattr(entity, 'title', getattr(entity, 'first_name', '未知'))
    print(f"\n📋 {cfg['name']}  读取: {name}")
    print(f"{'='*70}")
    messages = await client.get_messages(entity, limit=limit, offset_id=offset_id) if offset_id else await client.get_messages(entity, limit=limit)
    # 🔥 批量获取发送者信息
    sender_cache = {}
    for m in messages:
        sid = m.sender_id
        if sid and sid not in sender_cache:
            try:
                se = await client.get_entity(sid)
                sname = getattr(se, 'first_name', '') or ''
                suname = get_all_usernames(se)
                sender_cache[sid] = f"{sname} {suname}".strip()
            except:
                sender_cache[sid] = str(sid)
    for m in reversed(messages):
        # 🅰️ raw 模式: 输出消息完整 JSON (含 reply_markup / web_app 原始结构)
        if raw:
            try:
                md = m.to_dict()
                import json as _json
                out = _json.dumps(md, ensure_ascii=False, default=str)
                print(f"  [id:{m.id} {sender_cache.get(m.sender_id,'?')[:20]}] {out[:1500]}")
                continue
            except Exception as ex:
                print(f"  [id:{m.id}] raw失败: {ex}")
                continue
        sid = m.sender_id
        if sid and sid in sender_cache:
            sender = sender_cache[sid][:25]
        else:
            sender = f"[{sid}]" if sid else "[?]"
        dt = m.date.astimezone(timezone.utc).strftime("%m-%d %H:%M") if m.date else "??"
        text = (extract_msg_text(m) or "")[:300] or (f"[媒体:{type(m.media).__name__}]" if m.media else "[空]")
        text = text.replace('\n', ' ')
        # 🔘 按钮信息: [行][列] 文本 (url/callback/web_app)
        btns = ""
        try:
            if m.buttons:
                for ri, row in enumerate(m.buttons):
                    for ci, b in enumerate(row):
                        btxt = b.text.replace('\n', ' ')[:60]
                        wa = getattr(b, 'web_app', None)
                        if wa and getattr(wa, 'url', None):
                            btxt += f" [WebApp]→{wa.url[:100]}"
                        elif getattr(b, 'url', None):
                            btxt += f" →{b.url[:80]}"
                        elif getattr(b, 'data', None):
                            btxt += " [cb]"
                        elif getattr(b, 'inline_query', None):
                            btxt += f" [iq:{b.inline_query[:30]}]"
                        btns += f"\n      🔘[{ri}][{ci}] {btxt}"
        except: pass
        print(f"  {dt} {sender:>25s} | {text}{btns}")
    print(f"\n--- 共 {len(messages)} 条 ---")


async def cmd_click(client, cfg, target, msg_id, row, col):
    """点击消息上的按钮; msg_id 省略=自动定位最近带按钮的消息"""
    try:
        entity = await client.get_entity(target)
    except Exception:
        # 数字ID 或 无username bot → 从 dialog 列表找 (resolve 数字ID需要缓存)
        entity = None
        try:
            _tid = int(str(target))
            async for _d in client.iter_dialogs():
                if getattr(_d.entity, 'id', None) == _tid:
                    entity = _d.entity
                    break
        except Exception:
            pass
        if entity is None:
            entity = target
    try:
        if not msg_id:
            _auto = None
            async for m in client.iter_messages(entity, limit=10):
                if m.buttons:
                    _auto = m
                    break
            if _auto is None:
                print("❌ 最近10条消息没有带按钮的(先send/dialogs查看)")
                return
            msg_id = _auto.id
            print(f"🆔 自动定位按钮消息: ID={msg_id}")
        msg = await client.get_messages(entity, ids=msg_id)
        if not msg:
            print("❌ 消息不存在")
            return
        if not msg.buttons:
            print("❌ 该消息没有按钮")
            return
        try:
            btn = msg.buttons[row][col]
        except:
            print(f"❌ 按钮行列越界: 实际 {len(msg.buttons)} 行")
            for ri, rowb in enumerate(msg.buttons):
                print(f"  行{ri}: {', '.join(b.text[:40] for b in rowb)}")
            return
        # 显示按钮类型
        if getattr(btn, 'url', None):
            print(f"🔗 URL按钮: {btn.text} → {btn.url}")
        elif getattr(btn, 'data', None):
            print(f"🔘 Callback按钮: {btn.text} (data={len(btn.data)}B)")
            try:
                result = await btn.click()
                if result:
                    text = getattr(result, 'text', None) or getattr(result, 'message', '')
                    print(f"✅ 点击成功，返回:\n{text[:500]}")
                else:
                    print("✅ 点击成功")
            except Exception as ex:
                print(f"⚠️ 点击异常: {ex}")
        else:
            print(f"🔘 按钮: {btn.text}")
            try:
                result = await btn.click()
                text = getattr(result, 'text', None) or getattr(result, 'message', '')
                print(f"✅ 点击成功，返回:\n{text[:500]}")
            except Exception as ex:
                print(f"⚠️ 点击异常: {ex}")
    except Exception as ex:
        print(f"❌ 点击失败: {ex}")


async def cmd_buttons(client, cfg, target, msg_id):
    """查看消息所有按钮; msg_id 省略=自动定位最近一条带按钮的消息(模型常漏先读回复拿ID)"""
    try:
        entity = await client.get_entity(target)
    except:
        entity = target
    try:
        if not msg_id:
            # 自动找最近带按钮的消息
            _auto = None
            async for m in client.iter_messages(entity, limit=10):
                if m.buttons:
                    _auto = m
                    break
            if _auto is None:
                print("❌ 最近10条消息里没有带按钮的(先send/dialogs看下)")
                return
            msg_id = _auto.id
            print(f"🆔 自动定位按钮消息: ID={msg_id} (最新一条带按钮)")
        msg = await client.get_messages(entity, ids=msg_id)
        if not msg:
            print("❌ 消息不存在")
            return
        if not msg.buttons:
            print("该消息没有按钮")
            return
        print(f"📋 消息 {msg_id} 按钮 ({len(msg.buttons)} 行):")
        for ri, row in enumerate(msg.buttons):
            parts = []
            for ci, b in enumerate(row):
                btxt = b.text[:50]
                _ty = ""
                if getattr(b, 'web_app', None):
                    _ty = "WebApp"  # ⌨️小程序按钮(inline/reply 均有此字段)
                    btxt += f" url={getattr(b.web_app, 'url', '')[:70]}"
                elif getattr(b, 'url', None):
                    _ty = "URL"; btxt += f" →{b.url[:60]}"
                elif getattr(b, 'data', None):
                    _ty = "回调"; btxt += " [cb]"
                elif getattr(b, 'switch_inline', None):
                    _ty = "内联切换"
                elif getattr(b, 'pay', None):
                    _ty = "支付"
                elif getattr(b, 'request_contact', None):
                    _ty = "索要联系"
                elif getattr(b, 'request_location', None):
                    _ty = "索要位置"
                elif getattr(b, 'request_poll', None):
                    _ty = "投票"
                parts.append(f"[{ri}][{ci}]({_ty}) {btxt}")
            print("  " + " | ".join(parts))
    except Exception as ex:
        print(f"❌ 读取失败: {ex}")


async def _discover_webview_url(client, bot_peer, bot_uname, _seen_data=None, _depth=0):
    """🔍 全自动通用探测 bot 的 WebView url — 四类入口全覆盖(递归穿透):
    1. 消息里现成 WebView/WebApp 按钮 url (KeyboardButtonWebView.url / .web_app.url)
    2. KeyboardButtonUrl 指向 t.me/本bot 的官方短链 → 直接可用
    3. 纯 callback 按钮 → 自动逐层点击穿透(点后 bot 弹 WebView/edit/回新消息; 新callback则递归再点)
    4. BotInfo.web_apps / menu_button
    返回可直接喂 requestWebView 的 url; 无则 None。
    _seen_data: 已点的 callback data 集合(防重复点击死循环)。
    _depth: 当前递归深度, 上限 _MAX_DEPTH 层。
    """
    import asyncio as _aio
    _MAX_DEPTH = 8
    _seen_data = _seen_data if _seen_data is not None else set()
    _depth = _depth or 0
    _ind = '  ' * (_depth + 1)

    # 判断一层按钮里是否含可开 WebView 的按键
    def _btn_weburl(_under):
        if _under is None:
            return None
        cls = _under.__class__.__name__
        if cls == 'KeyboardButtonWebView':
            return getattr(_under, 'url', None) or None        # 站外直链 h5 (菠菜/game)
        _tw = getattr(_under, 'web_app', None)                 # KeyboardButtonWebApp
        if _tw is not None:
            return getattr(_tw, 'url', None)
        if cls == 'KeyboardButtonUrl':                          # 官方短链 t.me/bot/xxx
            _u = getattr(_under, 'url', None) or ''
            # 严格匹配: t.me/<本bot>/<path> 才算 webapp 短链; 无path或指向他bot的跳过(那要点击让bot推菜单)
            _nu = _u.replace('https://', '').replace('http://', '').split('?')[0].rstrip('/')
            _seg = _nu.split('/')
            if len(_seg) >= 2 and _seg[0].lower() == (bot_uname or '').lstrip('@').lower():
                return _u
        return None

    # 入口关键词(点击优先级: 文本像"进入游戏/大厅/首页"的按钮先点)
    _ENTRY_KW = ('游戏', '大厅', '进入', '娱乐', '首页', '主页', 'h5', 'home', 'hall', 'game', 'play', 'login', '登入', '开始')
    def _kw_rank(_txt):
        _t = (_txt or '').lower()
        for _i, _k in enumerate(_ENTRY_KW):
            if _k.lower() in _t:
                return -_i
        return 999

    # --- 扫描: 翻遍最近消息; 遇 WebView 按钮直接return; 记下 callback/url 按钮供点击 ---
    _cb_hits = []          # (message_id, row, col, callback_data_b64, 预览text)
    try:
        async for _msg in client.iter_messages(bot_peer, limit=14):
            try:
                if not getattr(_msg, 'buttons', None):
                    continue
            except Exception:
                continue
            for _ri, _crow in enumerate(_msg.buttons):
                for _ci, _cbtn in enumerate(_crow):
                    _under = getattr(_cbtn, 'button', None)
                    _wu = _btn_weburl(_under)
                    # 1) 消息里直接带可开 WebView 的按钮 → 一击即中
                    if _wu:
                        return _wu
                    # 2) callback 型按钮(疑似入口)→ 记录待点击穿透
                    _cbtxt = getattr(_cbtn, 'text', '') or ''
                    if _under is not None and getattr(_under, 'data', None) is not None:
                        _cb = bytes(getattr(_under, 'data'))
                        if _cb not in _seen_data:
                            _cb_hits.append((_msg.id, _ri, _ci, _cb, _cbtxt))
                    # 3) 纯 url 按钮(非本bot短链, 可能跳广告/频道) → 跳过不点
    except Exception as ex:
        print(f"{_ind}⚠️ 消息扫描异常: {ex}")

    # 安全过滤: 只自动点「入口类」按钮(游戏/大厅/进入/首页等关键词),
    # 防止误触 充值/提现/客服/签到 等功能按钮(会触发真实业务动作)
    _safe_hits = [h for h in _cb_hits if _kw_rank(h[4]) != 999]
    # 无入口关键词按钮 → 不自动点(宁可漏, 不可误触充值/客服)
    _cb_hits = _safe_hits
    # 智能排序: 像入口的按钮先点
    _cb_hits.sort(key=lambda x: _kw_rank(x[4]))
    if _cb_hits and _depth == 0:
        _previews = ' | '.join(f"{h[4][:14]}" for h in _cb_hits[:6])
        print(f"{_ind}🎯 发现 {len(_cb_hits)} 个入口按钮, 开始逐层点击穿透: {_previews}")

    # --- 逐层点击穿透: 点 callback → 回抓 WebView; 新 callback 则递归 ---
    _clicked_any = False
    for (mid, row, col, rawdata, prev) in list(_cb_hits):
        if _depth >= _MAX_DEPTH:
            print(f"{_ind}⏹️ 已达最大递归深度 {_MAX_DEPTH}, 停止穿透")
            break
        try:
            _msg2 = await client.get_messages(bot_peer, ids=mid)
            if not _msg2 or not _msg2.buttons:
                continue
            if row >= len(_msg2.buttons) or col >= len(_msg2.buttons[row]):
                continue
            btn = _msg2.buttons[row][col]
            print(f"{_ind}🖱️ [深{_depth}] 点击 [{getattr(btn, 'text', '')[:18]}] (msg={mid})")
            try:
                res = await btn.click()
            except Exception as _ce:
                # UrlInvalidError → 按钮本身即 WebView 但缺前置值, 跳过
                if 'UrlInvalid' in str(_ce):
                    continue
                print(f"{_ind}   ↳ 点击异常: {type(_ce).__name__}: {str(_ce)[:100]}")
                continue
            _clicked_any = True
            if rawdata is not None:
                _seen_data.add(rawdata)
            await _aio.sleep(1.2)
            # 回抓A: 原消息可能已被 edit 出 WebView/新按钮 → 重读
            _m_after = await client.get_messages(bot_peer, ids=mid)
            _new_cbs = []
            if _m_after and getattr(_m_after, 'buttons', None):
                for _r2i, _crow2 in enumerate(_m_after.buttons):
                    for _c2i, _cb2 in enumerate(_crow2):
                        _u2 = _btn_weburl(getattr(_cb2, 'button', None))
                        if _u2:
                            return _u2
                        _b2 = getattr(_cb2, 'button', None)
                        if _b2 is not None and getattr(_b2, 'data', None) is not None:
                            _d2 = bytes(getattr(_b2, 'data'))
                            if _d2 not in _seen_data and _d2 != rawdata:
                                _new_cbs.append((mid, _r2i, _c2i, _d2, getattr(_cb2, 'text', '') or ''))
            # 回抓B: bot 新推消息(可能带 WebView/新 callback) 扫最新6条
            async for _nm in client.iter_messages(bot_peer, limit=6):
                if not getattr(_nm, 'buttons', None):
                    continue
                for _r3i, _crow3 in enumerate(_nm.buttons):
                    for _c3i, _cb3 in enumerate(_crow3):
                        _u3 = _btn_weburl(getattr(_cb3, 'button', None))
                        if _u3:
                            return _u3
                        _b3 = getattr(_cb3, 'button', None)
                        if _b3 is not None and getattr(_b3, 'data', None) is not None:
                            _d3 = bytes(getattr(_b3, 'data'))
                            if _d3 not in _seen_data and _d3 != rawdata:
                                _new_cbs.append((_nm.id, _r3i, _c3i, _d3, getattr(_cb3, 'text', '') or ''))
            # 递归: 点出新的 callback 菜单 → 深入下一层
            if _new_cbs:
                _uniq = {}
                for _nc in _new_cbs:
                    _uniq.setdefault(_nc[3], _nc)   # 按 data 去重
                _sub = sorted(_uniq.values(), key=lambda x: _kw_rank(x[4]))
                print(f"{_ind}  ↳ 弹出 {len(_sub)} 个新 callback 按钮, 继续下钻: {' | '.join(s[4][:12] for s in _sub[:5])}")
                for _sc in _sub:
                    _sub_msg = await client.get_messages(bot_peer, ids=_sc[0])
                    if not _sub_msg or not getattr(_sub_msg, 'buttons', None):
                        continue
                    _u = await _discover_webview_url(client, bot_peer, bot_uname,
                                                     _seen_data, _depth + 1)
                    if _u:
                        return _u
                    break   # 该按钮子树无果, 试下一个
        except Exception as _ex:
            print(f"{_ind}  ↳ 穿透异常: {type(_ex).__name__}: {str(_ex)[:100]}")
            continue

    # --- 兜底: BotInfo.web_apps / menu_button ---
    try:
        _bi = getattr(bot_peer, 'bot_info', None)
        if _bi:
            for _path in ('web_apps', 'native', 'webApp'):
                _items = getattr(_bi, _path, None)
                if not _items:
                    continue
                _ls = _items if isinstance(_items, list) else [_items]
                for _it in _ls:
                    _u = getattr(getattr(_it, 'web_app', None), 'url', None) or getattr(_it, 'url', None)
                    if _u:
                        return _u
            _mb = getattr(_bi, 'menu_button', None)
            _mbu = getattr(_mb, 'url', None)
            if _mbu:
                return _mbu
    except Exception:
        pass
    return None
async def relay_bot_webapp(client, bot_uname):
    """对单个 bot 全自动取 webapp_url + initData。成功返回 dict, 否则抛异常。"""
    try:
        from telethon.tl.functions.messages import RequestWebViewRequest as _RWV
        from telethon.tl.types import InputUser, InputPeerUser
        try:
            peer = await client.get_entity(bot_uname)
        except Exception:
            # 数字ID / 无username bot → 从 dialog 找实体
            peer = None
            try:
                _tid = int(str(bot_uname))
                async for _d in client.iter_dialogs():
                    if getattr(_d.entity, 'id', None) == _tid:
                        peer = _d.entity
                        break
            except Exception:
                pass
            if peer is None:
                raise
        bot_input = InputUser(user_id=peer.id, access_hash=getattr(peer, 'access_hash', 0))
        bot_peer = InputPeerUser(user_id=peer.id, access_hash=getattr(peer, 'access_hash', 0))
    except Exception as ex:
        raise RuntimeError(f"resolve @{bot_uname} 失败: {type(ex).__name__}({ex})")
    # 确保已 /start
    try:
        await client.send_message(bot_peer, "/start")
        await asyncio.sleep(1.0)
    except Exception:
        pass
    webapp_url = await _discover_webview_url(client, bot_peer, bot_uname.lstrip('@'))
    if not webapp_url:
        webapp_url = f"https://t.me/{bot_uname.lstrip('@')}/app"
        raise RuntimeError(f"@{bot_uname}: 三类入口均未发现 url, 连 fallback /app 也没探到 → 放弃")
    res = await client(_RWV(
        peer=bot_peer, bot=bot_input, platform='android',
        url=webapp_url, theme_params=None, from_bot_menu=False, start_param=None))
    wv_url = res.url if res else ''
    # 抠 initData (query / fragment)
    import urllib.parse as _up
    _u = _up.urlparse(wv_url)
    _frag = _u.fragment or ''
    init_data = ''
    for _k in ('tgWebAppData',):
        if _k in _u.query:
            init_data = _up.unquote(_up.parse_qs(_u.query).get(_k,[''])[0])
        elif f'{_k}=' in _frag:
            init_data = _up.unquote(_frag.split('=',1)[1])
    return {'bot': bot_uname, 'webapp_url': webapp_url, 'wv_url': wv_url,
            'init_data': init_data or None}

async def cmd_bulkwebapp(client, cfg, bot_list):
    """批量对多个 bot 取 webapp/initData, 结果落盘+打印。
    bot_list: 每行一个 @username / 空格分隔/完整 t.me 链接 均可"""
    import json, os, time as _t
    results = []
    for raw in bot_list:
        raw = raw.strip().lstrip('@')
        if not raw:
            continue
        if 't.me/' in raw:
            raw = raw.split('t.me/')[-1].split('/')[0].split('?')[0]
        print(f"\n===== @{raw} =====")
        try:
            r = await relay_bot_webapp(client, raw)
            ok = bool(r.get('init_data'))
            r['ok'] = ok
            results.append(r)
            if ok:
                print(f"  ✅ webapp_url = {r['webapp_url']}")
                print(f"  ✅ initData len={len(r['init_data'])}")
                print(f"     hash 前20: {_extract_hash(r['init_data'])}")
            else:
                print(f"  ❌ 拿到webview但没 initData")
        except Exception as ex:
            print(f"  ❌ {ex}")
            results.append({'bot': raw, 'ok': False, 'error': str(ex)[:200]})
        await asyncio.sleep(0.6)  # 防 flood
    # 落盘
    outdir = '/opt/deepseek-bot/webapp_harvest'
    os.makedirs(outdir, exist_ok=True)
    ts = _t.strftime('%Y%m%d_%H%M%S')
    fname = f"{outdir}/wallet_{cfg['name']}_{ts}.json"
    with open(fname, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    good = sum(1 for r in results if r.get('ok'))
    print(f"\n📦 完成 {len(results)} 目标, 成功 {good} → 已存 {fname}")
    print(f"   汇总行: {fname}")

def _extract_hash(init_data):
    import urllib.parse as _up
    try:
        q = _up.parse_qs(init_data)
        return (q.get('hash',[''])[0])[:24]
    except Exception:
        return ''

async def cmd_webapp(client, cfg, target, msg_id=None):
    """打开 WebApp 拿 webview URL + initData
    三种用法:
      webapp <bot用户名>          — 直接对 bot 调 requestWebView (不依赖消息)
      webapp <群> <消息ID>         — 从消息上的 WebApp 按钮打开
      webapp <完整链接>            — 从 t.me/bot/path?startapp=xxx 链接直接打开
    """
    import urllib.parse as _up
    # 🆕 纯数字ID / 无username bot → 交给 relay (内部含 dialog 回退 resolve)
    if msg_id is None and target.isdigit():
        try:
            await relay_bot_webapp(client, target)
        except Exception as ex:
            print(f"  ❌ 打开失败: {type(ex).__name__}: {ex}")
        return
    # 🆕 完整链接模式: t.me/bot/path?startapp=xxx
    if msg_id is None and (target.startswith('http') or 't.me/' in target):
        raw_url = target.strip()
        # 提取 bot 名: https://t.me/<bot>/<path>
        _m = raw_url.split('t.me/')[-1]  # bot/path?query
        _path_part = _m.split('?')[0]     # bot/path
        _seg = _path_part.split('/')
        bot_uname = _seg[0].lstrip('@')
        app_path = '/'.join(_seg[1:]) if len(_seg) > 1 else 'app'
        # 提取 startapp 参数
        start_param = None
        _q = _up.parse_qs(_up.urlparse(raw_url).query)
        if 'startapp' in _q:
            start_param = _q['startapp'][0]
        elif 'start' in _q:
            start_param = _q['start'][0]
        webapp_url = f"https://t.me/{bot_uname}/{app_path}"
        print(f"🔘 解析 WebApp 链接: bot=@{bot_uname} path=/{app_path} start_param={start_param[:40] if start_param else '无'}")
        try:
            peer = await client.get_entity(bot_uname)
            from telethon.tl.types import InputUser, InputPeerUser
            bot_input = InputUser(user_id=peer.id, access_hash=getattr(peer, 'access_hash', 0))
            bot_peer = InputPeerUser(user_id=peer.id, access_hash=getattr(peer, 'access_hash', 0))
            try:
                await client.send_message(bot_peer, "/start")
                print("  ✅ 已发送 /start 建立对话")
                await asyncio.sleep(1)
            except Exception as ex:
                print(f"  ⚠️ /start 发送失败: {ex}")
            res = await client(RequestWebViewRequest(
                peer=bot_peer,
                bot=bot_input,
                platform='android',
                url=webapp_url,
                theme_params=None,
                from_bot_menu=False,
                start_param=start_param,
            ))
            wv_url = res.url
            print(f"  ✅ WebView URL:\n  {wv_url[:800]}")
            _u2 = _up.urlparse(wv_url)
            qs2 = _up.parse_qs(_u2.query)
            init_data = None
            if 'tgWebAppData' in qs2:
                init_data = _up.unquote(qs2['tgWebAppData'][0])
            elif _u2.fragment and 'tgWebAppData=' in _u2.fragment:
                # fragment 模式: #tgWebAppData=xxx
                _frag = _u2.fragment
                _fq = _up.parse_qs(_frag)
                if 'tgWebAppData' in _fq:
                    init_data = _up.unquote(_fq['tgWebAppData'][0])
            if init_data:
                print(f"\n  ✅ initData:\n  {init_data[:2000]}")
                # 解码 JSON 展示
                try:
                    import json as _json
                    _decoded = _up.unquote(init_data)
                    print(f"\n  📦 解码后:\n  {_decoded[:2000]}")
                except:
                    pass
            else:
                print(f"\n  ⚠️ URL 里没找到 tgWebAppData")
            print(f"\n  📋 URL 参数:")
            for k, v in qs2.items():
                vv = _up.unquote(v[0])
                print(f"    {k} = {vv[:200]}")
        except Exception as ex:
            print(f"  ❌ 打开失败: {type(ex).__name__}: {ex}")
        return
    # 直接对 bot 调 requestWebView (msg_id 为空)
    if msg_id is None:
        bot_uname = target.strip().lstrip('@')
        print(f"🔘 直接打开 WebApp: @{bot_uname}")
        try:
            peer = await client.get_entity(bot_uname)
            # bot 参数必须传 InputUser（用户ID + access_hash），get_input_entity 返回 InputPeerUser 不适用
            from telethon.tl.types import InputUser, InputPeerUser
            bot_input = InputUser(user_id=peer.id, access_hash=getattr(peer, 'access_hash', 0))
            bot_peer = InputPeerUser(user_id=peer.id, access_hash=getattr(peer, 'access_hash', 0))
            # 前置: 必须先给 bot 发 /start 建立对话（否则 BotInvalidError）
            try:
                await client.send_message(bot_peer, "/start")
                print("  ✅ 已发送 /start 建立对话")
                await asyncio.sleep(1)
            except Exception as ex:
                print(f"  ⚠️ /start 发送失败: {ex}")
            # 关键: 必须传 url (bot 的 WebApp 地址)，from_bot_menu=True 会报 BotInvalidError
            # 🔧 修复: 从 BotInfo 取真实 WebApp url(很多bot路径不是/app, 硬编码会拿到无效initData), 失败fallback /app
            # 🔧 通用WebView url探测: 优先最近消息的WebView按钮外链(h5/game型)→再BotInfo/menu
            try:
                webapp_url = await _discover_webview_url(client, bot_peer, bot_uname.lstrip('@'))
            except Exception as _de:
                webapp_url = None
            if not webapp_url:
                webapp_url = f"https://t.me/{bot_uname}/app"
                print(f"  ⚠️ 未探测到WebView按钮URL, 用默认/app (部分bot会UrlInvalidError)")
            else:
                print(f"  🎯 探测到WebView URL: {webapp_url}")
            res = await client(RequestWebViewRequest(
                peer=bot_peer,
                bot=bot_input,
                platform='android',
                url=webapp_url,
                theme_params=None,
                from_bot_menu=False,
                start_param=None,
            ))
            wv_url = res.url
            print(f"  ✅ WebView URL:\n  {wv_url[:600]}")
            # 抠 initData (可能在 query 或 fragment # 后面)
            import urllib.parse
            _u = urllib.parse.urlparse(wv_url)
            qs = urllib.parse.parse_qs(_u.query)
            if _u.fragment and '=' in _u.fragment:
                # fragment 里也可能是 key=value (tgWebAppData=xxx)
                for _pair in _u.fragment.split('&'):
                    if '=' in _pair:
                        _k, _v = _pair.split('=', 1)
                        qs.setdefault(_k, []).append(_v)
            if 'tgWebAppData' in qs:
                init_data = urllib.parse.unquote(qs['tgWebAppData'][0])
                print(f"\n  ✅ initData (from URL):\n  {init_data[:1200]}")
            else:
                print(f"\n  ⚠️ URL 里没有 tgWebAppData 参数")
            # 列出 URL 全部参数
            print(f"\n  📋 URL 参数:")
            for k, v in qs.items():
                vv = urllib.parse.unquote(v[0])
                print(f"    {k} = {vv[:150]}")
        except Exception as ex:
            print(f"  ❌ 打开失败: {type(ex).__name__}: {ex}")
        return
    try:
        entity = await client.get_entity(target)
    except:
        entity = target
    try:
        msg = await client.get_messages(entity, ids=msg_id)
        if not msg:
            print("❌ 消息不存在")
            return
        if not msg.buttons:
            print("❌ 该消息没有按钮")
            return
        # 找 WebApp 按钮
        wa_btns = []
        for ri, row in enumerate(msg.buttons):
            for ci, b in enumerate(row):
                if getattr(b, 'web_app', None) or getattr(b, 'button', None):
                    wa_btns.append((ri, ci, b))
        if not wa_btns:
            print("❌ 该消息没有 WebApp(小程序)按钮，只有普通按钮")
            for ri, row in enumerate(msg.buttons):
                for ci, b in enumerate(row):
                    btxt = b.text[:50]
                    if getattr(b, 'url', None): btxt += f" →{b.url[:60]}"
                    elif getattr(b, 'data', None): btxt += " [cb]"
                    print(f"  [{ri}][{ci}] {btxt}")
            return
        # 逐个打开 WebApp
        for ri, ci, b in wa_btns:
            btxt = getattr(b, 'text', '小程序')
            print(f"🔘 打开 WebApp 按钮 [{ri}][{ci}] {btxt}")
            try:
                # 获取按钮原始数据
                btn_raw = msg.button(ri, ci)
                # bot_username 从按钮 URL 提取 (tg://resolve?domain=xxx 或 https://t.me/xxxbot)
                bot_uname = None
                url = getattr(b, 'url', '') or ''
                if 't.me/' in url:
                    part = url.split('t.me/')[-1].split('?')[0].split('/')[0]
                    if part.endswith('bot'): bot_uname = part
                if not bot_uname and 'resolve?domain=' in url:
                    bot_uname = url.split('resolve?domain=')[-1].split('&')[0]
                if not bot_uname:
                    print(f"  ⚠️ 无法从按钮提取 bot 用户名 (url={url[:80]})")
                    continue
                # 请求 WebView
                from telethon.tl.types import InputUser
                _bot_ent = await client.get_entity(bot_uname)
                bot_input = InputUser(user_id=_bot_ent.id, access_hash=getattr(_bot_ent, 'access_hash', 0))
                res = await client(RequestWebViewRequest(
                    peer=entity,
                    bot=bot_input,
                    platform='android',
                    url=None,
                    theme_params=None,
                    from_bot_menu=True,
                    start_param=None,
                ))
                wv_url = res.url
                print(f"  ✅ WebView URL:\n  {wv_url[:500]}")
                # 从 URL 里抠 initData (query 或 fragment # 后面)
                import urllib.parse
                _u2 = urllib.parse.urlparse(wv_url)
                qs = urllib.parse.parse_qs(_u2.query)
                if _u2.fragment and '=' in _u2.fragment:
                    for _pair in _u2.fragment.split('&'):
                        if '=' in _pair:
                            _k, _v = _pair.split('=', 1)
                            qs.setdefault(_k, []).append(_v)
                if 'tgWebAppData' in qs:
                    init_data = urllib.parse.unquote(qs['tgWebAppData'][0])
                    print(f"\n  ✅ initData (from URL):\n  {init_data[:800]}")
                else:
                    print(f"\n  ⚠️ URL 里没有 tgWebAppData 参数，可尝试 initdata 命令构造")
                print()
            except Exception as ex:
                print(f"  ❌ 打开失败: {type(ex).__name__}: {ex}")
    except Exception as ex:
        print(f"❌ WebApp 读取失败: {type(ex).__name__}: {ex}")


def gen_initdata(bot_token: str, user_id: int, extra: dict = None):
    """构造 Telegram WebApp initData (HMAC-SHA256 签名)
    官方 WebAppInitData 现支持: query_id/user/auth_date/hash + 可选
    chat_id/chat_type/chat_instance/start_param/can_send_after/
    chat_join_request_query_id(10.1) 等——全部字段按 key 排序参与签名。
    extra 里除 'user' 外的标量字段会一并扣入 data_check 并按字母序签名。"""
    import time, hmac, hashlib, urllib.parse
    import json as _json
    extra = extra or {}
    auth_date = int(time.time())
    user_obj = {"id": user_id, "first_name": "User", "last_name": "", "username": "user", "language_code": "en"}
    if extra.get("user"):
        user_obj.update(extra["user"])
    user_str = _json.dumps(user_obj, separators=(',', ':'))

    # 默认顶层标量字段(排除已特殊处理的 hash/user)
    top = {}
    if extra.get("query_id"):
        top["query_id"] = extra["query_id"]
    for _k in ("chat_id", "chat_type", "chat_instance", "start_param",
               "can_send_after", "chat_join_request_query_id"):  # 10.1新字段
        if extra.get(_k) is not None:
            top[_k] = str(extra[_k])
    # user 总是带上
    top["user"] = user_str
    # auth_date 未指定用当前
    top.setdefault("auth_date", str(auth_date))

    # data_check = 所有字段(去 hash)按 key 字典序拼 "k=v"，换行分隔
    data_check = "\n".join(
        f"{k}={v}" for k, v in sorted(top.items()) if k != "hash")
    secret_key = hmac.new(bot_token.encode(), b"WebAppData", hashlib.sha256).digest()
    h = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    top["hash"] = extra.get("hash", h)

    parts = [f"{k}={urllib.parse.quote(str(v), safe='')}" for k, v in top.items()]
    return "&".join(parts)


async def cmd_initdata(client, cfg, bot_token):
    """构造 Telegram WebApp initData"""
    me = await client.get_me()
    uid = me.id
    init_data = gen_initdata(bot_token, uid)
    print(f"🧑 用户ID: {uid}")
    print(f"🔑 initData:\n{init_data}")
    print(f"\n💡 用法: 发给需要 initData 的小程序/网页，或配合 webapp 命令使用")



def _target_hint(target, dialogs=None):
    """★2026-10-05 给"找不到目标"一句能照做的提示(以前只说 找不到 'xxx')"""
    _t = str(target or "")
    if any(_c in _t for _c in "{}[]") or _t.startswith('"'):
        return (f"参数被拆坏了: 收到的是 JSON 碎片 {_t[:60]!r}。"
                f"正确写法是 **k=v 形式**(例: to=@username text=内容) 或 群名/数字ID, 别把整段 JSON 塞进来。")
    _d = ""
    if dialogs is not None:
        _n = len(dialogs or [])
        _d = f"（{_n} 个对话里没有匹配的）" if _n else "（这个账号的对话列表是空的: 可能没登录/没加群）"
    return (f"找不到目标 {_t[:60]!r} {_d}。可以用: ①@用户名 ②数字ID(群是 -100xxxxxxxxxx) "
            f"③对话列表里显示的名字(要完全一致, 带emoji也算)。先列一下可用目标最省事。")


async def cmd_send(client, cfg, target, text):
    """发送消息"""
    try:
        entity = await client.get_entity(target)
    except:
        try:
            entity = await client.get_entity(int(target))
        except:
            print("ERROR: " + _target_hint(target))
            return
    await client.send_message(entity, text)
    name = getattr(entity, 'title', getattr(entity, 'first_name', '未知'))
    print(f"✅ {cfg['name']} → {name}: {text[:80]}...")


def describe_media(m):
    """提取消息媒体信息: 类型/文件名/大小/尺寸/时长"""
    md = None
    try:
        md = m.to_dict()
    except:
        pass
    media = getattr(m, 'media', None)
    parts = []
    if media is None:
        return ''
    from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage, MessageMediaContact, MessageMediaGeo, MessageMediaPoll, MessageMediaGame, MessageMediaDice, MessageMediaInvoice, MessageMediaGiveaway, MessageMediaGiveawayResults
    if isinstance(media, MessageMediaPhoto):
        parts.append("📷 图片")
        if md:
            ph = md.get('media', {}).get('photo', {})
            sizes = ph.get('sizes', []) if isinstance(ph, dict) else []
            if sizes:
                parts.append(f"{sizes[-1].get('w','?')}x{sizes[-1].get('h','?')}")
    elif isinstance(media, MessageMediaDocument):
        doc = media.document
        attr_map = {}
        for a in getattr(doc, 'attributes', []):
            an = type(a).__name__
            attr_map[an] = a
        if 'DocumentAttributeVideo' in attr_map:
            v = attr_map['DocumentAttributeVideo']
            parts.append("🎬 视频" if getattr(v, 'round', False) is False else "🔄 视频消息")
            parts.append(f"{getattr(v,'w','?')}x{getattr(v,'h','?')}")
            if getattr(v, 'duration', None):
                parts.append(f"{v.duration}s")
        elif 'DocumentAttributeAudio' in attr_map:
            a = attr_map['DocumentAttributeAudio']
            parts.append("🎵 语音" if getattr(a, 'voice', False) else "🎧 音频")
            if getattr(a, 'duration', None): parts.append(f"{a.duration}s")
        elif 'DocumentAttributeAnimated' in attr_map:
            parts.append("🎞 GIF")
        elif 'DocumentAttributeSticker' in attr_map:
            st = attr_map['DocumentAttributeSticker']
            parts.append("🏷 贴纸")
            if getattr(st, 'sticker_set', None) and getattr(st.sticker_set, 'title', None):
                parts.append(f"「{st.sticker_set.title}」")
        else:
            parts.append("📄 文件")
        if getattr(doc, 'size', None): parts.append(f"{doc.size/1024:.0f}KB")
        fn = getattr(doc, 'mime_type', '')
        if fn: parts.append(fn)
    elif isinstance(media, MessageMediaWebPage):
        wp = media.webpage
        parts.append("🔗 链接预览")
        if getattr(wp, 'title', None): parts.append(f"「{wp.title[:50]}」")
    elif isinstance(media, MessageMediaContact):
        parts.append("👤 名片")
    elif isinstance(media, MessageMediaGeo):
        parts.append("📍 位置")
    elif isinstance(media, MessageMediaPoll):
        parts.append("📊 投票")
    elif isinstance(media, MessageMediaDice):
        parts.append("🎲 骰子")
    elif isinstance(media, MessageMediaGame):
        parts.append("🎮 游戏")
    elif isinstance(media, MessageMediaInvoice):
        parts.append("🧾 收款")
    elif isinstance(media, MessageMediaGiveawayResults):
        parts.append("🎁 开奖")
    elif isinstance(media, MessageMediaGiveaway):
        parts.append("🎁 抽奖")
    else:
        parts.append(f"📦 {type(media).__name__}")
    return ' '.join(parts)


async def cmd_msg(client, cfg, target, msg_id):
    """按消息ID精确读取单条消息（含发送者/时间/媒体/按钮）"""
    entity = None
    try:
        entity = await client.get_entity(target)
    except:
        dialogs = await client.get_dialogs()
        for d in dialogs:
            if d.name and str(target).lower() in d.name.lower():
                entity = d.entity
                break
        if entity is None:
            try:
                entity = await client.get_entity(int(target))
            except:
                print(f"ERROR: 找不到 '{target}'")
                return
    try:
        msg = await client.get_messages(entity, ids=int(msg_id))
    except Exception as ex:
        print(f"❌ 读取消息失败: {ex}")
        return
    if not msg:
        print(f"❌ 消息 {msg_id} 不存在")
        return

    name = getattr(entity, 'title', getattr(entity, 'first_name', '未知'))
    print(f"\n📋 {cfg['name']}  读取: {name} | 消息ID: {msg_id}")
    print(f"{'='*70}")
    # 发送者
    try:
        se = await client.get_entity(msg.sender_id) if msg.sender_id else None
        sname = f"{getattr(se,'first_name','')} {getattr(se,'last_name','')}".strip() if se else ''
        suname = get_all_usernames(se) if se else ''
        print(f"  发送者: {sname} {suname}".strip() if (sname or suname) else f"  发送者: {msg.sender_id}")
    except:
        print(f"  发送者: {msg.sender_id}")
    # 时间
    if msg.date:
        tz = timezone.utc
        print(f"  时间:   {msg.date.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    # 转发
    if msg.fwd_from:
        print(f"  转发:   {getattr(msg.fwd_from,'from_name','?')}")
    # 回复
    if msg.reply_to_msg_id:
        print(f"  回复:   消息ID {msg.reply_to_msg_id}")
    # 文本
    text = extract_msg_text(msg)
    if text:
        print(f"  内容:   {text}")
    # 媒体
    media_desc = describe_media(msg)
    if media_desc:
        print(f"  媒体:   {media_desc}")
    # 按钮
    if msg.buttons:
        print(f"  按钮:   {len(msg.buttons)} 行")
        for ri, row in enumerate(msg.buttons):
            parts = []
            for ci, b in enumerate(row):
                btxt = b.text[:50]
                if getattr(b, 'url', None): btxt += f" →{b.url[:80]}"
                elif getattr(b, 'data', None): btxt += " [cb]"
                parts.append(f"[{ri}][{ci}] {btxt}")
            print("          " + " | ".join(parts))
    # 原始JSON预览
    if len(sys.argv) > 5 and sys.argv[5].lower() in ("raw","json","1","true"):
        import json as _json
        print(f"  ── RAW ──")
        print(_json.dumps(msg.to_dict(), ensure_ascii=False, default=str)[:3000])
    print(f"{'='*70}")



async def cmd_groupinfo(client, cfg, target):
    """查群/频道详情(标题/成员/类型/id)——双号解析, 支持用户名/中文名/链接"""
    target = target.strip()
    if 't.me/' in target:
        # 提取最后一段作 t.me 用户名/id
        target = target.rstrip('/').split('/')[-1].split('?')[0]
    try:
        try:
            ent = await client.get_entity(int(target))
        except Exception:
            ent = await client.get_entity(target)
    except Exception as ex:
        print(f"ERROR: 找不到群/频道/用户 '{target}': {type(ex).__name__}")
        return
    cls = type(ent).__name__
    is_ch = 'Channel' in cls
    is_g = 'Chat' in cls and 'Channel' not in cls
    title = getattr(ent, 'title', None) or f'{getattr(ent,"first_name","")} {getattr(ent,"last_name","")}'.strip()
    uname = getattr(ent, 'username', None)
    print(f"\n📇 {cfg['name']} 实体详情:")
    print(f"{'='*60}")
    print(f"  ID:      {ent.id}")
    print(f"  类型:    {'群组' if is_g else '频道/超级群' if is_ch else '用户/机器人'}")
    print(f"  名称:    {title}")
    if uname:
        print(f"  @:       {uname}")
    # 成员数: 更强获取 — 普通群/超级群/频道全兼容
    cnt = None
    _dc = None
    try:
        from telethon import functions as _fn
        if is_ch:
            _dc = getattr(ent, 'participants_count', None)
            if _dc is None:
                try:
                    full = await client(_fn.channels.GetFullChannelRequest(ent))
                    _dc = getattr(getattr(full, 'full_chat', None), 'participants_count', None)
                except Exception:
                    _dc = None
        else:
            from telethon.tl.functions.messages import GetFullChatRequest
            full = await client(GetFullChatRequest(ent.id))
            _dc = getattr(getattr(full, 'full_chat', None), 'participants_count', None)
    except Exception:
        _dc = None
    print(f"  成员数:  {_dc if _dc is not None else '未知/保密'}")
    # 首条消息时间(判断活跃/频道新鲜度)
    try:
        fm = await client.get_messages(ent, limit=1)
        if fm:
            from datetime import datetime
            dd = fm[0].date
            print(f"  最近消息:{dd.strftime('%Y-%m-%d %H:%M')}  (id={fm[0].id})")
    except Exception:
        pass
    return ent.id

async def cmd_user(client, cfg, target):
    try:
        try:
            entity = await client.get_entity(int(target))
        except Exception:
            entity = await client.get_entity(target)
    except:
        print(f"ERROR: 找不到用户 '{target}'(支持@用户名/手机/纯ID)")
        return
    print(f"\n👤 {cfg['name']} 查询用户:")
    print(f"{'='*60}")
    print(f"  ID:       {entity.id}")
    all_unames = get_all_usernames(entity)
    print(f"  用户名:    {all_unames}" if all_unames else "  用户名:    (无)")
    print(f"  姓名:     {entity.first_name or ''} {entity.last_name or ''}")
    print(f"  手机:     {entity.phone if hasattr(entity, 'phone') and entity.phone else '(隐藏)'}")
    print(f"  机器人:    {'是' if entity.bot else '否'}")
    if hasattr(entity, 'premium'):
        print(f"  Premium:  {'是' if entity.premium else '否'}")
    # 共同群组/在线状态
    if hasattr(entity, 'status'):
        st = entity.status
        stype = type(st).__name__
        if 'Offline' in stype:
            print(f"  状态:     离线")
        elif 'Online' in stype:
            print(f"  状态:     🟢 在线")
        elif 'Recently' in stype:
            print(f"  状态:     最近在线")
        elif 'LastWeek' in stype:
            print(f"  状态:     上周在线")
        elif 'LastMonth' in stype:
            print(f"  状态:     上月在线")
    # 👇 头像下载
    try:
        import os
        os.makedirs('/tmp/tg_avatars', exist_ok=True)
        avatar_path = f"/tmp/tg_avatars/{entity.id}.jpg"
        had = await client.download_profile_photo(entity, file=avatar_path)
        if had:
            print(f"  头像:     ✅ 已下载 → {avatar_path}")
        else:
            print(f"  头像:     (无头像)")
    except Exception as ex:
        print(f"  头像:     下载失败 {ex}")


async def cmd_search(client, cfg, target, keyword, limit=50, offset_id=None):
    entity = None
    try:
        entity = await client.get_entity(target)
    except:
        dialogs = await client.get_dialogs()
        for d in dialogs:
            if d.name and target.lower() in d.name.lower():
                entity = d.entity
                break
        if entity is None:
            try:
                entity = await client.get_entity(int(target))
            except:
                pass
    if not entity:
        print(f"ERROR: 找不到 '{target}'")
        return

    print(f"\n🔍 {cfg['name']} 在 '{getattr(entity,'title',target)}' 搜索: {keyword}")
    print(f"{'='*60}")
    messages = await client.get_messages(entity, limit=limit, search=keyword, offset_id=offset_id) if offset_id else await client.get_messages(entity, limit=limit, search=keyword)
    # 批量获取发送者信息
    sender_cache = {}
    for m in messages:
        sid = m.sender_id
        if sid and sid not in sender_cache:
            try:
                se = await client.get_entity(sid)
                sname = getattr(se, 'first_name', '') or ''
                suname = get_all_usernames(se)
                sender_cache[sid] = f"{sname} {suname}".strip()
            except:
                sender_cache[sid] = str(sid)
    for m in messages:
        # 最新在前(Telethon返回已按时间降序, 不再reversed——否则最新被排到末尾看不到)
        sid = m.sender_id
        if sid and sid in sender_cache:
            sender = sender_cache[sid][:25]
        else:
            sender = f"[{sid}]" if sid else "[?]"
        dt = m.date.astimezone(timezone.utc).strftime("%m-%d %H:%M") if m.date else "??"
        text = m.text[:200] if m.text else "[媒体]"
        text = text.replace('\n', ' ')
        print(f"  {dt} {sender:>25s} | {text}")
    print(f"\n--- 找到 {len(messages)} 条 ---")


async def cmd_stats(client, cfg, target, limit=200, offset_id=None):
    entity = None
    try:
        entity = await client.get_entity(target)
    except:
        dialogs = await client.get_dialogs()
        for d in dialogs:
            if d.name and target.lower() in d.name.lower():
                entity = d.entity
                break
    if not entity:
        print(f"ERROR: 找不到 '{target}'")
        return

    messages = await client.get_messages(entity, limit=limit, offset_id=offset_id) if offset_id else await client.get_messages(entity, limit=limit)
    counters = Counter()
    # 批量获取发送者信息
    sender_cache = {}
    for m in messages:
        if m.sender_id:
            counters[m.sender_id] += 1
            sid = m.sender_id
            if sid not in sender_cache:
                try:
                    se = await client.get_entity(sid)
                    sname = getattr(se, 'first_name', '') or ''
                    suname = get_all_usernames(se)
                    sender_cache[sid] = f"{sname} {suname}".strip()
                except:
                    sender_cache[sid] = str(sid)

    name = getattr(entity, 'title', target)
    print(f"\n📊 {cfg['name']} 群活跃统计: {name} (最近{len(messages)}条)")
    print(f"{'='*50}")
    for uid, cnt in counters.most_common(20):
        pct = cnt / len(messages) * 100
        bar = '█' * int(pct / 2)
        sname = sender_cache.get(uid, str(uid))
        print(f"  {sname:>25s} │ {cnt:4d}条 {bar} {pct:.1f}%")


async def cmd_whois(client, cfg, target, limit=200, offset_id=None):
    entity = None
    try:
        entity = await client.get_entity(target)
    except:
        dialogs = await client.get_dialogs()
        for d in dialogs:
            if d.name and target.lower() in d.name.lower():
                entity = d.entity
                break
    if not entity:
        print(f"ERROR: 找不到 '{target}'")
        return

    messages = await client.get_messages(entity, limit=limit, offset_id=offset_id) if offset_id else await client.get_messages(entity, limit=limit)
    counters = Counter()
    for m in messages:
        if m.sender_id:
            counters[m.sender_id] += 1

    print(f"\n🗣️ {cfg['name']} 发言排行 (共{len(messages)}条)")
    print(f"{'='*50}")
    for rank, (uid, cnt) in enumerate(counters.most_common(15), 1):
        try:
            u = await client.get_entity(uid)
            uname = get_all_usernames(u) or f"{u.first_name or ''} {u.last_name or ''}"
        except:
            uname = "?"
        print(f"  #{rank:2d} [{uid:>12d}] {uname:25s} {cnt:4d}条")


async def cmd_members(client, cfg, target, limit=200):
    entity = None
    try:
        entity = await client.get_entity(target)
    except:
        dialogs = await client.get_dialogs()
        for d in dialogs:
            if d.name and target.lower() in d.name.lower():
                entity = d.entity
                break
    if not entity:
        print(f"ERROR: 找不到 '{target}'")
        return

    print(f"\n👥 {cfg['name']} 拉取群成员: {getattr(entity, 'title', target)}")
    print(f"{'='*70}")
    participants = await client.get_participants(entity, limit=limit)
    for i, p in enumerate(participants):
        uid = p.id
        uname = get_all_usernames(p)
        name = f"{p.first_name or ''} {p.last_name or ''}".strip()
        phone = p.phone if hasattr(p, 'phone') and p.phone else ""
        print(f"  [{i:3d}] ID:{uid:>12d} {uname:20s} {name:30s} {phone}")
    print(f"\n--- 共 {len(participants)} 人 ---")


async def cmd_join(client, cfg, invite):
    """通过邀请链接加群"""
    try:
        if invite.startswith("https://t.me/"):
            hash_part = invite.split("/")[-1].replace("+", "")
        elif invite.startswith("+"):
            hash_part = invite[1:]
        else:
            hash_part = invite

        # 先检查邀请
        check = await client(CheckChatInviteRequest(hash_part))
        print(f"📨 {cfg['name']} 准备加群: {getattr(check, 'title', hash_part)}")
        
        # 加入
        result = await client(ImportChatInviteRequest(hash_part))
        print(f"✅ {cfg['name']} 成功加群!")
        return result
    except Exception as e:
        print(f"ERROR: 加群失败 - {e}")


async def cmd_react(client, cfg, target, msg_id, emoji="👍"):
    """给消息点赞/反应"""
    try:
        entity = await client.get_entity(target)
    except:
        dialogs = await client.get_dialogs()
        found = None
        for d in dialogs:
            if d.name and target.lower() in d.name.lower():
                found = d.entity
                break
        if not found:
            try:
                entity = await client.get_entity(int(target))
            except:
                print(json.dumps({"error": _target_hint(target, dialogs)}, ensure_ascii=False))
                return
        else:
            entity = found
    
    try:
        result = await client(SendReactionRequest(
            peer=entity,
            msg_id=int(msg_id),
            reaction=[ReactionEmoji(emoticon=emoji)]
        ))
        print(json.dumps({"ok": True, "emoji": emoji, "msg_id": msg_id, "chat": target}))
    except Exception as e:
        print(json.dumps({"error": str(e)}))


# ── JSON 输出模式 (给 Bot 调用) ──
async def json_output(cmd, *args):
    """JSON 格式输出，供 Bot 解析"""
    pass


# ── Main ──
async def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    account = sys.argv[1].lower()
    if account not in ACCOUNTS:
        print(f"未知账号 '{account}'，可用: wang / naiwa")
        print(__doc__)
        return

    if len(sys.argv) < 3:
        print(f"请指定操作，如: python3 tg_user.py {account} dialogs")
        return

    # 2026-09-08: 检测常驻 tg_daemon 占用提示(与 daemon 并发跑必报 database is locked)
    try:
        _dp = open("/tmp/tg_daemon.pid").read().strip()
        if _dp and os.path.exists("/proc/" + _dp):
            print(f"⚠️ 提示: tg_daemon(pid={_dp}) 常驻占用 session 文件; 若下面报 database is locked, "
                  f"请改用 bot 内 tg 工具或先 kill {_dp}")
    except Exception:
        pass

    cmd = sys.argv[2].lower()
    client, cfg = await get_client(account)
    if not client:
        return

    try:
        if cmd == "dialogs":
            limit = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            await cmd_dialogs(client, cfg, limit)
        elif cmd == "messages":
            target = sys.argv[3] if len(sys.argv) > 3 else "me"
            limit = int(sys.argv[4]) if len(sys.argv) > 4 else 50
            offset_id = int(sys.argv[5]) if len(sys.argv) > 5 else None
            raw = len(sys.argv) > 6 and sys.argv[6].lower() in ("raw","json","1","true")
            await cmd_messages(client, cfg, target, limit, offset_id, raw)
        elif cmd == "msg":
            # 按消息ID读单条: msg <群> <消息ID> [raw]
            if len(sys.argv) < 5:
                print("用法: tg_user.py <账号> msg <群> <消息ID> [raw]")
                return
            target = sys.argv[3]
            msg_id = sys.argv[4]
            await cmd_msg(client, cfg, target, msg_id)
        elif cmd == "send":
            target = sys.argv[3]
            text = " ".join(sys.argv[4:])
            await cmd_send(client, cfg, target, text)
        elif cmd == "click":
            # 点击消息按钮: click <群> <消息ID> <行> <列>
            target = sys.argv[3]
            msg_id = int(sys.argv[4])
            row = int(sys.argv[5])
            col = int(sys.argv[6])
            await cmd_click(client, cfg, target, msg_id, row, col)
        elif cmd == "buttons":
            # 查看消息按钮: buttons <群> <消息ID>
            target = sys.argv[3]
            msg_id = int(sys.argv[4])
            await cmd_buttons(client, cfg, target, msg_id)
        elif cmd == "webapp":
            # webapp <bot用户名> 直接打开 | webapp <群> <消息ID> 从按钮打开
            target = sys.argv[3]
            if len(sys.argv) > 4:
                msg_id = int(sys.argv[4])
                await cmd_webapp(client, cfg, target, msg_id)
            else:
                await cmd_webapp(client, cfg, target)
        elif cmd == "bulkwebapp":
            arg = sys.argv[3] if len(sys.argv) > 3 else ""
            bot_list = []
            if arg and os.path.isfile(arg):
                with open(arg, encoding='utf-8') as _f:
                    bot_list = [ln.strip() for ln in _f if ln.strip()]
            else:
                bot_list = [b.strip() for b in arg.replace(',', ' ').split() if b.strip()]
            if not bot_list:
                print("用法: bulkwebapp \"@bot1 @bot2...\" 或 bulkwebapp bots.txt")
                return
            await cmd_bulkwebapp(client, cfg, bot_list)
        elif cmd == "initdata":
            # 构造 initData: initdata <bot_token>
            bot_token = sys.argv[3]
            await cmd_initdata(client, cfg, bot_token)
        elif cmd == "groupinfo":
            target = sys.argv[3] if len(sys.argv) > 3 else ""
            await cmd_groupinfo(client, cfg, target)
        elif cmd == "user":
            target = sys.argv[3]
            await cmd_user(client, cfg, target)
        elif cmd == "search":
            target = sys.argv[3]
            keyword = sys.argv[4]
            limit = int(sys.argv[5]) if len(sys.argv) > 5 else 50
            offset_id = int(sys.argv[6]) if len(sys.argv) > 6 else None
            await cmd_search(client, cfg, target, keyword, limit, offset_id)
        elif cmd == "stats":
            target = sys.argv[3]
            limit = int(sys.argv[4]) if len(sys.argv) > 4 else 200
            offset_id = int(sys.argv[5]) if len(sys.argv) > 5 else None
            await cmd_stats(client, cfg, target, limit, offset_id)
        elif cmd == "whois":
            target = sys.argv[3]
            limit = int(sys.argv[4]) if len(sys.argv) > 4 else 200
            offset_id = int(sys.argv[5]) if len(sys.argv) > 5 else None
            await cmd_whois(client, cfg, target, limit, offset_id)
        elif cmd == "members":
            target = sys.argv[3]
            limit = int(sys.argv[4]) if len(sys.argv) > 4 else 200
            await cmd_members(client, cfg, target, limit)
        elif cmd == "join":
            invite = sys.argv[3]
            await cmd_join(client, cfg, invite)
        elif cmd == "react":
            target = sys.argv[3]
            msg_id = int(sys.argv[4])
            emoji = sys.argv[5] if len(sys.argv) > 5 else "👍"
            await cmd_react(client, cfg, target, msg_id, emoji)
        else:
            print(f"未知操作 '{cmd}'")
            print("可用: dialogs messages send user search stats whois members join react")
    except Exception as _e:
        # 参数错误/执行失败: 干净报错, 不出traceback
        if "database is locked" in str(_e):
            print("ERROR: session 文件被 tg_daemon 常驻进程锁死(并发连接). 请用 bot 内 tg 工具, 或 kill tg_daemon 后再操作.")
        else:
            print(f"ERROR: {_e}")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
