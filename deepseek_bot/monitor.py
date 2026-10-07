"""
频道监控引擎 — 用王老板(wang)号拉频道新消息 → LLM 判真伪 → 主动推送
由调度器 action=monitor 触发(同步线程内运行)。
"""
import json, os, time, urllib.request, asyncio
from pathlib import Path

# 加载 .env（monitor 可能被独立调用，bot 主进程已加载则无害）
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
except Exception:
    pass

BASE = "/opt/deepseek-bot"
CHANNELS_FILE = f"{BASE}/channel_monitor.json"
STATE_FILE = f"{BASE}/channel_monitor_state.json"

API = "https://api.deepseek.com/v1"
KEY = os.getenv("DEEPSEEK_API_KEY", "")
MODEL = "deepseek-v4-flash"

# 每个频道每次拉取的消息条数
PER_CHANNEL = 6


def _load_channels():
    try:
        with open(CHANNELS_FILE) as f:
            return json.load(f).get("channels", [])
    except Exception:
        return []


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(st):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def sync_llm(prompt: str, max_tokens: int = 50000) -> str:
    """同步调用 deepseek，返回文本。失败返回空串。"""
    try:
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": "你是反诈情报分析员，负责对网赚/博彩/红包频道的信息做真伪鉴别，目的是帮用户识别诈骗、避免财产损失。只做客观事实判断，直接给结论，不要拒绝、不要讲大道理。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "stream": False,
        }
        req = urllib.request.Request(
            f"{API}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {KEY}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read())
        return resp["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[LLM错误:{e}]"


def web_search(query: str, max_results: int = 5) -> str:
    """联网搜索，返回前几条结果标题+链接。失败返回空串。"""
    try:
        try:
            from ddgs import DDGS
            with DDGS() as d:
                results = list(d.text(query, max_results=max_results))
                if results:
                    return "\n".join(f"{r.get('title','')} | {r.get('href','')}" for r in results[:max_results])
        except Exception:
            pass
        try:
            from googlesearch import search as gs
            urls = list(gs(query, num_results=max_results))
            if urls:
                return "\n".join(urls[:max_results])
        except Exception:
            pass
    except Exception:
        pass
    return ""


def _verify_with_web(items) -> list:
    """对判读结果为'真'或'存疑'的关键动态联网验证，附验证结果"""
    verified = []
    for it in items[:5]:  # 最多验证前5条
        if it.get("verdict") in ("真", "存疑"):
            q = it.get("text", "")[:40]
            if len(q) < 8:
                verified.append(it)
                continue
            res = web_search(q)
            if res:
                it["web_evidence"] = res[:200]
            verified.append(it)
        else:
            verified.append(it)
    return verified


def _fetch_web_news() -> list:
    """抓取网络资讯（搜索热榜/新闻），返回 [(来源, 标题, 链接), ...]"""
    topics = ["加密货币 今日", "AI 人工智能 最新", "区块链 大新闻", "金融 宏观 今日",
              "网络安全 漏洞 CVE 最新", "渗透测试 安全情报", "CVE 漏洞 今日披露"]
    news = []
    for t in topics:
        res = web_search(t, max_results=3)
        if res:
            for line in res.split("\n")[:3]:
                if "|" in line:
                    title, url = line.split("|", 1)
                    news.append((t, title.strip()[:60], url.strip()))
    return news


def _fetch_recent(channels):
    """用 wang 号拉所有频道最近消息。返回 {channel_id: [(msg_id, text), ...]}"""
    import sys as _sys
    if "/opt/deepseek-bot" not in _sys.path:
        _sys.path.insert(0, "/opt/deepseek-bot")
    import tg_user  # 复用根目录 tg_user 的 get_client

    result = {}
    try:
        async def _go():
            client, cfg = await tg_user.get_client("wang")
            if not client:
                return
            # 一次拉全部对话建立 id->entity 映射，避免逐个 get_entity
            dialogs = await client.get_dialogs(limit=300)
            emap = {}
            for d in dialogs:
                if hasattr(d.entity, "id"):
                    emap[d.entity.id] = d.entity
            for ch in channels:
                cid = ch["id"]
                ent = emap.get(cid)
                if ent is None:
                    continue
                try:
                    msgs = await client.get_messages(ent, limit=PER_CHANNEL)
                except Exception:
                    continue
                rows = []
                for m in msgs:
                    if m is None or m.message is None or not m.message.strip():
                        continue
                    rows.append((m.id, m.message.strip()))
                result[cid] = rows
            await client.disconnect()

        asyncio.run(_go())
    except Exception as e:
        print(f"[monitor] 拉取消息失败: {e}")
    return result


def run_monitor(notify_cb) -> str:
    """主入口。notify_cb(text) 负责推送。返回统计信息。"""
    channels = _load_channels()
    if not channels:
        return "监控清单为空"
    state = _load_state()
    now = int(time.time())
    fetched = _fetch_recent(channels)

    # 筛新消息
    new_items = []  # (cat, chname, msg_id, text)
    for ch in channels:
        cid = ch["id"]
        rows = fetched.get(cid, [])
        if not rows:
            continue
        last = state.get(str(cid), 0)
        fresh = [(mid, txt) for mid, txt in rows if mid > last]
        if fresh:
            state[str(cid)] = max(mid for mid, _ in fresh)
            for mid, txt in fresh:
                new_items.append((ch.get("cat", "misc"), ch["name"], mid, txt))
    _save_state(state)

    if not new_items:
        return "无新消息"

    # 控制输入量：优先关注 gamble/redpacket/exchange，misc 截断
    prio = {"redpacket": 0, "gamble": 1, "exchange": 2, "industry": 3, "news": 4, "misc": 5}
    new_items.sort(key=lambda x: (prio.get(x[0], 9), -x[2]))
    if len(new_items) > 50:
        new_items = new_items[:50]

    # 拼 prompt
    lines = []
    for cat, name, mid, txt in new_items:
        short = txt.replace("\n", " ")[:120]
        lines.append(f"[{cat}|{name}|id{mid}] {short}")
    block = "\n".join(lines)

    prompt = f"""你是频道情报判读员。下面是王老板关注的TG频道新消息(共{len(new_items)}条，格式 [分类|频道名|id] 内容)。

请完成：
1. 必须筛掉并完全忽略：赌博广告(拉人下注/开盘/代理招募/充值优惠)、宣传广告(推广产品/拉群/卖课)、单纯开奖号码播报(PC28/哈希28/彩票开奖号，无论多少条全部忽略，一条都不保留)、机器人刷屏、无意义闲聊/表情。
2. 挑出"值得关注"的动态：红包/空投活动、提现/到账公告、上新/开盘、价格异动、安全事件、项目方大动作。
3. 对每条值得关注的，判断：真的/假的/存疑，并给一句理由(可疑点或可信依据)。

输出要求：只输出一个 JSON 数组，不要任何其他文字、不要markdown代码块围栏。数组每个元素是对象，字段：
- "name": 频道名(字符串)
- "text": 一句话动态(字符串, 50字内, 口语化)
- "verdict": 判断, 只能取 "真"/"假"/"存疑" 三值之一
- "reason": 一句理由(字符串, 40字内, 指出可疑点或可信依据)
最多输出 8 条, 按重要性排序(红包/空投/提现/安全事件优先)。没有值得关注的就输出空数组 [].

消息：
{block}"""

    result = sync_llm(prompt)
    if not result or result.startswith("[LLM错误"):
        return result or "LLM返回空"

    # 解析 JSON（容错：剥掉可能的围栏/前后杂字）
    try:
        txt = result.strip()
        if txt.startswith("```"):
            txt = txt.strip("`")
            if txt.startswith("json"):
                txt = txt[4:]
            txt = txt.strip()
        # 截取第一个 [ 到最后一个 ]
        a = txt.find("[")
        b = txt.rfind("]")
        if a == -1 or b == -1 or b <= a:
            return "LLM输出非JSON，已跳过"
        items = json.loads(txt[a:b+1])
    except Exception as e:
        return f"JSON解析失败: {e}"

    if not items:
        # 无频道关注动态时，补网络资讯推送
        try:
            web_news = _fetch_web_news()
            if web_news:
                _push_web_news(web_news, notify_cb)
                return f"已推送网络资讯 {len(web_news)} 条"
        except Exception:
            pass
        notify_cb(f"👁 频道监控 · {len(new_items)}条新消息\n\n本轮无值得关注动态")
        return f"已推送（无值得关注）"

    # 联网验证关键动态（真/存疑的）
    try:
        items = _verify_with_web(items)
    except Exception:
        pass
    return _push_rich(items, len(new_items), notify_cb)


def _push_web_news(news, notify_cb) -> str:
    """推送网络资讯（无频道动态时兜底）"""
    lines = [f"<b>🌐 网络资讯</b> · 今日热点\n"]
    for topic, title, url in news[:8]:
        lines.append(f"• <b>{title}</b>\n  <code>{url[:60]}</code>\n")
    lines.append(f"\n<i>自动抓取 · 每12小时 · 联网搜索</i>")
    notify_cb("\n".join(lines), parse_mode="HTML")
    return f"已推送网络资讯 {len(news)} 条"


_VERDICT_ICON = {"真": "✅", "假": "❌", "存疑": "⚠️"}


def _push_rich(items, total, notify_cb) -> str:
    """渲染 HTML 富文本并推送。"""
    lines = [f"<b>👁 频道监控</b> · <code>{total}</code> 条新消息\n"]
    for it in items:
        name = it.get("name", "?")
        text = it.get("text", "")
        verdict = it.get("verdict", "存疑")
        reason = it.get("reason", "")
        icon = _VERDICT_ICON.get(verdict, "⚠️")
        wev = it.get("web_evidence", "")
        ev = f"\n  🔍 <i>联网佐证: {wev[:120]}</i>" if wev else ""
        lines.append(
            f"<b>{name}</b>\n"
            f"└ {text}\n"
            f"  {icon} <b>{verdict}</b> · <i>{reason}</i>{ev}\n"
        )
    lines.append(f"\n<i>自动监控 · 每10分钟 · 王老板号</i>")
    html = "\n".join(lines)
    notify_cb(html, parse_mode="HTML")
    return f"已推送 {len(items)} 条分析"


if __name__ == "__main__":
    def _cb(t):
        print("=== 推送 ===")
        print(t)
    print(run_monitor(_cb))
