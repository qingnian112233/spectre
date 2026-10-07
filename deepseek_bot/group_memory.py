"""
群聊记忆引擎 v1.0
- 按群组(gid)存储消息，记录发言人(uid+name+msg+ts)
- 语义检索群聊上下文
- 群友画像：按uid聚合发言，分析兴趣/水平
- 自动注入到系统提示
"""
import json, os, time, re
from pathlib import Path

try:
    import jieba
    JIEBA_OK = True
except ImportError:
    JIEBA_OK = False

if JIEBA_OK:
    for word in ["挂机", "盈利", "提现", "吃肉", "流水", "通宵", "熬夜", "凌晨",
                 "USDT", "OKPay", "彩金", "撸毛", "渗透", "挖洞", "补天", "漏洞",
                 "发卡", "ACG", "FAKA", "空投", "钱包", "私钥", "合约"]:
        jieba.add_word(word)

GRP_DIR = Path("/opt/deepseek-bot/group_memories")
GRP_DIR.mkdir(exist_ok=True)

MAX_GROUP_MSGS = 500      # 每组最多存500条
MAX_MSG_LEN = 300         # 单条消息最长300字符
MAX_PROFILE_FACTS = 20    # 每人群友画像最多20条
MAX_CONTEXT_MSGS = 15     # 注入上下文最多15条


def _grp_path(gid: int) -> Path:
    return GRP_DIR / f"g{gid}.json"


def clear_group_messages(gid: int) -> dict:
    """清空群聊聊天记忆(2026-09-04): messages/summary/topic_tags 清空, 群友画像(profiles/兴趣/事实)全部保留"""
    data = load_group(gid)
    data["messages"] = []
    data["summary"] = ""
    data["topic_tags"] = []
    data["last_active"] = time.time()
    save_group(gid, data)
    profs = data.get("profiles", {})
    facts = sum(len(p.get("facts", [])) for p in profs.values())
    return {"profiles": len(profs), "facts": facts}


def load_group(gid: int) -> dict:
    fp = _grp_path(gid)
    if not fp.exists():
        return {"messages": [], "profiles": {}, "summary": "", "topic_tags": [],
                "created_at": time.time(), "last_active": time.time()}
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        data.setdefault("messages", [])
        data.setdefault("profiles", {})
        data.setdefault("summary", "")
        data.setdefault("topic_tags", [])
        data.setdefault("created_at", time.time())
        data.setdefault("last_active", time.time())
        return data
    except:
        return {"messages": [], "profiles": {}, "summary": "",
                "topic_tags": [], "created_at": time.time(), "last_active": time.time()}


def save_group(gid: int, data: dict):
    fp = _grp_path(gid)
    tmp = str(fp) + ".tmp"
    try:
        Path(tmp).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, str(fp))
    except Exception:
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _label_of(data: dict, uid, name: str, uname: str = "") -> str:
    """群成员在本群内唯一的短标签(2026-09-12)

    规则: 名字首次出现原样用; 重名时**优先用 @用户名**(全局唯一), 没有用户名才加 uid 尾号。
    为什么要唯一: 实测某群 174 人有 10 组重名(「1」4 个不同的人), 注入上下文里
    全是「1: ...」→ 模型根本没法分辨谁是谁。
    """
    try:
        labels = data.setdefault("labels", {})
        u = str(uid)
        base = " ".join(str(name or "").split())[:24] or (("@" + str(uname)) if uname else u[-6:])
        cur = labels.get(u) or {}
        if cur.get("label") and cur.get("base") == base:
            return cur["label"]                      # 已有 → 保持稳定(长对话里不跳标签)
        _same = [x for x, v in labels.items() if (v or {}).get("base") == base and x != u]
        if not _same:
            lab = base
        elif uname:
            lab = f"{base}@{uname}"                  # ★全局唯一, 最好用
        else:
            _n = 4
            lab = f"{base}#{u[-_n:]}"
            while any((v or {}).get("label") == lab for v in labels.values()):
                _n += 2
                lab = f"{base}#{u[-_n:]}" if _n < len(u) else f"{base}#{u}"
        labels[u] = {"base": base, "label": lab}
        return lab
    except Exception:
        return str(name or uid)[:24]


def record_message(gid: int, uid: int, name: str, text: str, uname: str = ""):
    """记录群聊消息(uname=@用户名, 可选: 重名时用它区分, 全局唯一)"""
    data = load_group(gid)
    text = text.strip()[:MAX_MSG_LEN]
    if not text or len(text) < 2:
        return
    
    # 添加消息
    _lab = _label_of(data, uid, name, uname)     # 本群唯一标签(重名优先@用户名)
    msg_entry = {
        "uid": uid,
        "name": name,
        "username": uname or "",                 # 2026-09-12 存用户名(向后兼容: 老数据无此字段)
        "label": _lab,                           # 唯一标签, 注入时直接用它
        "msg": text,
        "ts": time.time()
    }
    data["messages"].append(msg_entry)
    data["last_active"] = time.time()
    
    # 超出上限，删旧消息
    if len(data["messages"]) > MAX_GROUP_MSGS:
        data["messages"] = data["messages"][-MAX_GROUP_MSGS:]
    
    # 更新群友画像
    _update_profile(data, uid, name, text, uname)
    
    save_group(gid, data)


def _update_profile(data: dict, uid: int, name: str, text: str, uname: str = ""):
    """从消息提取关键词，更新群友画像"""
    profiles = data.setdefault("profiles", {})
    uid_str = str(uid)
    if uid_str not in profiles:
        profiles[uid_str] = {"name": name, "username": uname or "", "facts": [],
                             "msg_count": 0, "interests": []}
    
    profile = profiles[uid_str]
    profile["name"] = name
    if uname:
        profile["username"] = uname          # 后来拿到了就补上(第一次可能没取到)
    profile["msg_count"] += 1
    
    # 提取兴趣关键词
    interests = _extract_interests(text)
    for kw in interests:
        if kw not in profile["interests"]:
            profile["interests"].append(kw)
    if len(profile["interests"]) > 20:
        profile["interests"] = profile["interests"][-20:]
    
    # 提取关键事实
    new_facts = _extract_facts_from_msg(uid, name, text)
    for f in new_facts:
        # 去重
        if f not in profile["facts"]:
            profile["facts"].append(f)
    if len(profile["facts"]) > MAX_PROFILE_FACTS:
        profile["facts"] = profile["facts"][-MAX_PROFILE_FACTS:]


def _extract_interests(text: str) -> list:
    """提取兴趣关键词"""
    interests = []
    kw_map = {
        "渗透": ["渗透", "SQL注入", "XSS", "漏洞", "挖洞", "SRC", "补天", "漏洞盒子",
                  "nmap", "burp", "metasploit", "提权", "getshell", "红队", "蓝队", "CTF", "免杀"],
        "区块链": ["币", "BTC", "ETH", "USDT", "空投", "撸毛", "钱包", "合约",
                   "DeFi", "NFT", "公链", "交易所", "币安", "OKX", "链上", "gas", "质押"],
        "发卡": ["发卡", "ACG", "FAKA", "卡密", "虚拟卡", "代付", "USDT卡", "卡商"],
        "AI": ["AI", "GPT", "Claude", "DeepSeek", "LLM", "大模型", "Agent", "提示词",
               "midjourney", "stable diffusion", "数字人"],
        "编程": ["Python", "JS", "代码", "脚本", "API", "爬虫", "自动化", "Golang",
                 "Java", "前端", "后端", "数据库", "GitHub"],
        "网赚": ["赚钱", "副业", "躺赚", "挂机", "收益", "日入", "月入", "佣金", "推广"],
        "游戏": ["游戏", "原神", "王者", "吃鸡", "LOL", "DOTA", "Steam", "抽卡", "氪金", "开箱"],
        "投资": ["股票", "基金", "A股", "美股", "期货", "黄金", "外汇", "理财"],
        "博彩": ["彩票", "下注", "赔率", "庄家", "开奖", "百家乐", "德州", "老虎机", "滚球"],
        "音乐": ["音乐", "说唱", "嘻哈", "摇滚", "民谣", "电子音乐", "DJ"],
        "影视": ["电影", "电视剧", "动漫", "番剧", "B站", "追剧", "美剧"],
        "体育": ["篮球", "足球", "NBA", "英超", "西甲", "世界杯", "电竞", "CS", "DOTA2", "LOL比赛"],
        "美食": ["美食", "火锅", "烧烤", "奶茶", "外卖", "做饭", "探店"],
        "汽车": ["汽车", "车", "特斯拉", "比亚迪", "跑车", "改装", "二手车"],
        "数码": ["手机", "iPhone", "华为", "小米", "电脑", "显卡", "耳机", "数码"],
        "电商": ["淘宝", "京东", "拼多多", "闲鱼", "带货", "直播卖货", "店铺"],
        "金融": ["贷款", "信用卡", "征信", "网贷", "花呗", "白条", "POS机"],
        "法律": ["法律", "律师", "合同", "起诉", "劳动仲裁", "维权"],
        "医疗": ["医院", "看病", "药", "医生", "体检", "疫苗"],
        "教育": ["考试", "学历", "考研", "公务员", "考证", "培训班"],
    }
    for category, keywords in kw_map.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                interests.append(category)
                break
    return list(set(interests))


def _extract_facts_from_msg(uid: int, name: str, text: str) -> list:
    """从消息提取关键事实"""
    facts = []
    patterns = [
        (r'(?:我是|我做|我在|我搞|我玩)([\u4e00-\u9fff\w]{2,30})', "身份"),
        (r'(?:我有|我有过|我买过|我用过)([\u4e00-\u9fff\w]{2,30})', "拥有"),
        (r'(?:我喜欢|我讨厌|我不喜欢)([\u4e00-\u9fff\w]{2,20})', "偏好"),
        (r'(\d{1,6}\.?\d{0,2})\s*(?:U|USDT|元|块|刀)', "金额"),
    ]
    for pattern, label in patterns:
        for m in re.findall(pattern, text, re.IGNORECASE):
            m = m.strip()
            if 2 <= len(m) <= 60:
                facts.append(f"[{label}] {m}")
    return facts


def retrieve_group_context(gid: int, uid: int, current_msg: str) -> str:
    """
    检索群聊上下文，返回要注入系统提示的内容。
    包含：群聊摘要、最近消息、当前发言人的画像、相关消息
    """
    data = load_group(gid)
    parts = []
    
    # 1. 群聊基本信息
    total_msgs = len(data["messages"])
    profiles_count = len(data["profiles"])
    if total_msgs > 0:
        parts.append(f"📢 群聊基本信息: 已收录{total_msgs}条消息，{profiles_count}位群友画像")
    
    # 2. 群聊摘要
    if data.get("summary"):
        parts.append(f"📝 群聊摘要: {data['summary']}")
    
    # 3. 最近消息（最多10条）
    recent = data["messages"][-80:]
    if recent:
        recent_lines = []
        for m in recent:
            # 2026-09-12 修「分不清谁是谁」的核心一处: 原来只用名字、还截断到 10 字,
            #   四个同名的人在注入上下文里全是「1: ...」, 连 uid 都没有 → 模型无从分辨;
            #   而且 [:10] 会把唯一标签的「#尾号/@用户名」后缀直接切掉。
            #   现在: 用唯一标签 + 带上 TG:uid, 长度上限放宽到 28(保住区分后缀)。
            _lb = m.get("label") or (data.get("labels", {}).get(str(m.get("uid"))) or {}).get("label") \
                or str(m.get("name") or "")[:28]
            _un = str(m.get("username") or "").strip()
            _who = f"{_lb[:28]}" + (f"(@{_un})" if (_un and ("@" + _un) not in _lb) else "")
            msg = m["msg"][:120]
            recent_lines.append(f"  {_who}(TG:{m.get('uid')}): {msg}")
        parts.append("💬 最近群聊:\n" + "\n".join(recent_lines))
    
    # 4. 当前发言人画像
    uid_str = str(uid)
    profile = data.get("profiles", {}).get(uid_str)
    if profile:
        prof_parts = []
        if profile.get("interests"):
            prof_parts.append(f"兴趣: {', '.join(profile['interests'][:8])}")
        if profile.get("facts"):
            prof_parts.append(f"事实: {'; '.join(profile['facts'][:5])}")
        if prof_parts:
            parts.append(f"👤 当前发言人画像({profile['name']}, 发言{profile['msg_count']}次):\n  " + "\n  ".join(prof_parts))
    
    # 5. 关键词搜索相关消息
    keywords = _extract_keywords(current_msg)
    if keywords:
        scored = []
        for m in data["messages"]:
            score = sum(1 for kw in keywords if kw in m["msg"])
            if score > 0:
                scored.append((m, score))
        scored.sort(key=lambda x: -x[1])
        relevant = scored[:15]
        if relevant:
            rel_lines = []
            for m, s in relevant:
                rel_lines.append(f"  {m['name']}: {m['msg'][:100]}")
            parts.append("🔍 相关历史消息:\n" + "\n".join(rel_lines))
    
    return "\n\n".join(parts) if parts else ""


def _extract_keywords(text: str) -> list:
    """简单关键词提取"""
    if not text or len(text) < 2:
        return []
    stop_words = {"的", "了", "是", "我", "你", "他", "她", "在", "有", "不", "这", "那",
                  "吗", "呢", "吧", "啊", "哦", "嗯", "哈", "呀", "嘛", "唉", "哦", "额",
                  "就", "都", "也", "还", "要", "会", "能", "可以", "一个", "什么"}
    # 分词 + 去停用词
    if JIEBA_OK:
        words = [w for w in jieba.cut(text) if len(w) >= 2 and w not in stop_words]
    else:
        words = [w for w in re.findall(r'[\u4e00-\u9fff\w]{2,}', text) if w not in stop_words]
    return list(set(words))[:10]


def get_group_stats(gid: int) -> str:
    """群聊统计"""
    data = load_group(gid)
    msgs = data["messages"]
    profiles = data["profiles"]
    
    if not msgs:
        return "📭 该群暂无消息记录"
    
    # 最活跃成员 TOP5
    active = {}
    for m in msgs:
        uid = str(m["uid"])
        active[uid] = active.get(uid, 0) + 1
    
    top_active = sorted(active.items(), key=lambda x: -x[1])[:5]
    top_lines = []
    for uid_str, count in top_active:
        name = profiles.get(uid_str, {}).get("name", uid_str)
        top_lines.append(f"  {name}: {count}条")
    
    # 主题标签统计
    all_interests = {}
    for p in profiles.values():
        for kw in p.get("interests", []):
            all_interests[kw] = all_interests.get(kw, 0) + 1
    top_topics = sorted(all_interests.items(), key=lambda x: -x[1])[:5]
    topic_lines = [f"  {kw}: {cnt}人" for kw, cnt in top_topics]
    
    return f"""📊 群聊统计
▸ 总消息: {len(msgs)}条
▸ 群友: {len(profiles)}人

🔥 最活跃:
{chr(10).join(top_lines) if top_lines else '  暂无数据'}

🏷️ 热门话题:
{chr(10).join(topic_lines) if topic_lines else '  暂无数据'}"""


def auto_summarize_group(gid: int):
    """自动生成群聊摘要（基于最近消息的主题聚类）"""
    data = load_group(gid)
    msgs = data["messages"]
    if len(msgs) < 10:
        return
    
    recent = msgs[-100:]
    # 简单主题统计
    topic_count = {}
    for m in recent:
        interests = _extract_interests(m["msg"])
        for kw in interests:
            topic_count[kw] = topic_count.get(kw, 0) + 1
    
    top_topics = sorted(topic_count.items(), key=lambda x: -x[1])[:5]
    if top_topics:
        summary = "群聊热门话题: " + ", ".join(f"{kw}({cnt}次)" for kw, cnt in top_topics)
        data["summary"] = summary
    
    # 更新主题标签
    data["topic_tags"] = [kw for kw, _ in top_topics]
    save_group(gid, data)
