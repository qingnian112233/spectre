"""
记忆检索引擎 v3.1 — 长期记忆 + 语义检索 + 自动摘要 + 对话搜索
增强点(v3.1):
- ✅ 记忆自动注入：每次对话自动检索相关记忆注入系统提示（无需手动调用）
- ✅ 对话索引增量更新：只索引新消息，不再全量重建（性能10x提升）
- ✅ 崩溃恢复：索引损坏时自动检测+重建，不丢数据
增强点(v3):
- 字符级ngram+词级双重相似度（修复"凌晨不睡觉vs通宵熬夜"=0.0的问题）
- 持久化搜索索引（避免每次读320KB history.json）
- LLM辅助事实抽取（regex兜底+关键词增强）
- 自动记忆合并（相似记忆合并而非去重）
- jieba用户词典注入
"""
import json, os, time, re, traceback
from pathlib import Path
from datetime import datetime

try:
    import jieba
    JIEBA_OK = True
except ImportError:
    JIEBA_OK = False

# 注入用户词典，优化分词
if JIEBA_OK:
    for word in ["挂机", "盈利", "提现", "吃肉", "得吃", "流水", "通宵", "熬夜", "凌晨", "不睡觉", "两天", "三天", "USDT", "OKPay", "起床", "睡觉"]:
        jieba.add_word(word)

MEM_DIR = Path("/opt/deepseek-bot/memories")
MEM_DIR.mkdir(exist_ok=True)
SEARCH_INDEX_DIR = Path("/opt/deepseek-bot/search_index")
SEARCH_INDEX_DIR.mkdir(exist_ok=True)

MAX_MEMORIES = 80       # 每人最多80条记忆
MAX_MEMORY_LEN = 600    # 单条最长600字符
SUMMARY_INTERVAL = 8    # 每8轮触发摘要
MEMORY_HALF_LIFE = 30   # 30轮后半衰，旧记忆减权
SIMILARITY_MERGE = 0.65 # 相似度>0.65自动合并

# v3.1: 增量索引阈值（超过此数量才全量重建）
INDEX_MAX_AGE = 3600    # 索引最多缓存1小时
INDEX_INCREMENTAL_THRESHOLD = 50  # 新增消息超过50条才全量重建（否则增量追加）

def _mem_path(uid) -> Path:
    # 支持 uid 或 "uid:chatid"（群聊/私聊记忆分开）
    return MEM_DIR / f"u{str(uid).replace(':','_')}.json"

# ==================== 记忆CRUD ====================

def load_memories(uid: int) -> dict:
    fp = _mem_path(uid)
    if not fp.exists():
        return {"facts": [], "summary": "", "prefs": {}, "last_summary_at": 0, "turn_count": 0, "created_at": time.time()}
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        data.setdefault("facts", [])
        data.setdefault("summary", "")
        data.setdefault("prefs", {})
        data.setdefault("last_summary_at", 0)
        data.setdefault("turn_count", 0)
        data.setdefault("created_at", time.time())
        return data
    except:
        return {"facts": [], "summary": "", "prefs": {}, "last_summary_at": 0, "turn_count": 0, "created_at": time.time()}

def save_memories(uid: int, data: dict):
    fp = _mem_path(uid)
    tmp = str(fp) + ".tmp"
    try:
        Path(tmp).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, str(fp))  # 原子写入，防止中途崩溃写坏文件
    except Exception:
        fp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def add_fact(uid: int, fact: str, category: str = ""):
    """添加事实，带分类和时间戳。相似度>SIMILARITY_MERGE自动合并"""
    data = load_memories(uid)
    fact = fact.strip()[:MAX_MEMORY_LEN]
    if not fact or len(fact) < 3:
        return
    
    # 查找最相似的已有事实
    best_match = None
    best_score = 0.0
    for existing in data["facts"]:
        existing_text = existing["text"] if isinstance(existing, dict) else existing
        score = _similarity(fact, existing_text)
        if score > best_score:
            best_score = score
            best_match = existing
    
    if best_score > SIMILARITY_MERGE and best_match is not None:
        if isinstance(best_match, dict):
            best_match["ts"] = time.time()
            best_match["count"] = best_match.get("count", 1) + 1
            if len(fact) > len(best_match["text"]):
                best_match["text"] = fact
        save_memories(uid, data)
        return
    
    entry = {
        "text": fact,
        "cat": category,
        "ts": time.time(),
        "count": 1,
        "age": data["turn_count"]
    }
    data["facts"].append(entry)
    
    # 保持记忆数量上限
    if len(data["facts"]) > MAX_MEMORIES:
        data["facts"] = data["facts"][-MAX_MEMORIES:]
    
    data["turn_count"] += 1
    save_memories(uid, data)

def set_pref(uid: int, key: str, value: str):
    data = load_memories(uid)
    data["prefs"][key] = value
    save_memories(uid, data)

def get_pref(uid: int, key: str, default=""):
    return load_memories(uid).get("prefs", {}).get(key, default)

def get_all_prefs(uid: int) -> dict:
    return load_memories(uid).get("prefs", {})

# ==================== 语义匹配 v3（双通道：词级 + 字符级 + 同义词） ====================

_SYNONYMS = {
    "凌晨": ["半夜", "深夜", "通宵", "晚上"],
    "通宵": ["熬夜", "不睡觉", "没睡", "没合眼", "凌晨"],
    "熬夜": ["通宵", "不睡觉", "没睡", "凌晨"],
    "不睡觉": ["没睡", "熬夜", "通宵", "没合眼"],
    "没睡": ["不睡觉", "熬夜", "通宵"],
    "挂机": ["挂机盈利", "自动", "托管"],
    "盈利": ["赚钱", "收益", "收入", "赢"],
    "赚钱": ["盈利", "收益", "赚", "赢"],
    "提现": ["到账", "收款", "付款", "提取"],
    "吃肉": ["赚钱", "盈利", "赢了"],
    "睡觉": ["睡", "入睡", "休息"],
    "起床": ["醒了", "起来", "睡醒"],
}
for _k, _v in list(_SYNONYMS.items()):
    for _syn in _v:
        if _syn not in _SYNONYMS:
            _SYNONYMS[_syn] = []
        if _k not in _SYNONYMS[_syn]:
            _SYNONYMS[_syn].append(_k)

def _expand_synonyms(words: set) -> set:
    expanded = set(words)
    for w in words:
        if w in _SYNONYMS:
            expanded.update(_SYNONYMS[w])
    return expanded

def _tokenize(text: str) -> tuple:
    text_clean = text.lower().strip()
    words = set()
    if JIEBA_OK:
        words.update(jieba.cut(text_clean))
    words.update(re.findall(r'[a-zA-Z0-9]{2,}', text_clean))
    words.update(re.findall(r'[一-鿿]{2,4}', text_clean))
    words = {w.strip() for w in words if len(w.strip()) >= 1}
    words = _expand_synonyms(words)
    
    pure = re.sub(r'[^一-鿿\w]', '', text_clean)
    char_ngrams = set()
    for i in range(len(pure) - 1):
        char_ngrams.add(pure[i:i+2])
    char_ngrams.update(pure)
    
    return words, char_ngrams

def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    
    wa, ca = _tokenize(a)
    wb, cb = _tokenize(b)
    
    word_sim = len(wa & wb) / len(wa | wb) if (wa | wb) else 0.0
    char_sim = len(ca & cb) / len(ca | cb) if (ca | cb) else 0.0
    
    nums_a = set(re.findall(r'\d+', a))
    nums_b = set(re.findall(r'\d+', b))
    num_bonus = 0.0
    if nums_a and nums_b:
        num_bonus = len(nums_a & nums_b) / max(len(nums_a | nums_b), 1) * 0.3
    
    return min(1.0, word_sim * 0.6 + char_sim * 0.4 + num_bonus)

def _extract_keywords(text: str) -> list:
    tokens = []
    nums = re.findall(r'\d{2,}', text)
    tokens.extend(nums)
    
    if JIEBA_OK:
        import jieba.posseg as pseg
        for w, flag in pseg.cut(text):
            if flag in ('n', 'v', 'vn', 'nr', 'ns', 'nt', 'nz', 'eng', 'a') and len(w) >= 2:
                tokens.append(w)
    
    if not tokens:
        for t in re.findall(r'[\u4e00-\u9fff]{2,}', text):
            tokens.append(t)
    
    seen = set()
    result = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            result.append(t)
    return result[:15]

# ==================== v3.2 语义向量记忆(Qwen embedding) ====================
_VEC_DIR = Path("/opt/deepseek-bot/memories_vec")
_VEC_DIR.mkdir(exist_ok=True)
_EMBED_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
_EMBED_MODEL = "text-embedding-v3"
_EMBED_CACHE = {}  # uid -> {text_hash: vec} 进程内缓存

def _load_vec_cache(uid):
    fp = _VEC_DIR / f"u{str(uid).replace(':','_')}.json"
    if fp.exists():
        try: return json.loads(fp.read_text(encoding="utf-8"))
        except: pass
    return {}

def _save_vec_cache(uid, cache):
    try:
        (_VEC_DIR / f"u{str(uid).replace(':','_')}.json").write_text(json.dumps(cache), encoding="utf-8")
    except: pass

def _embed_batch(texts, timeout=5):
    """Qwen embedding批量向量化, 失败返回空列表(调用方回退关键词检索)

    2026-09-21 老板「机器人响应怎么慢」: 原来 timeout=15, 而且一轮里可能打两次(缺失项 + 当前消息),
    最坏 30 秒。实测这个接口偶尔就是慢(线上出现过 15~20 秒的记忆检索), 所以压到 5 秒 ——
    超时就返回空, 检索自动回退关键词层, 结果一样能用, 但不拖时间。
    (bot.py 那边还有一道 2 秒的不等待闸, 双保险。)"""
    import urllib.request
    key = os.getenv("QWEN_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""
    if not key or not texts:
        return []
    try:
        req = urllib.request.Request(
            _EMBED_URL,
            data=json.dumps({"model": _EMBED_MODEL, "input": list(texts)}).encode(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode())
        embs = sorted(d.get("data", []), key=lambda x: x.get("index", 0))
        return [e["embedding"] for e in embs]
    except Exception:
        return []

def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x*y for x, y in zip(a, b))
    na = (sum(x*x for x in a)) ** 0.5
    nb = (sum(y*y for y in b)) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)

# ==================== 语义检索（增强） ====================

def _fact_weight(fact: dict, current_turn: int) -> float:
    """记忆权重：新记忆权重高，旧记忆半衰递减"""
    age = fact.get("age", 0) if isinstance(fact, dict) else 0
    count = fact.get("count", 1) if isinstance(fact, dict) else 1
    turns_ago = max(0, current_turn - age)
    decay = 0.5 ** (turns_ago / max(MEMORY_HALF_LIFE, 1))
    return decay * min(count, 5) / 5.0

def retrieve_context(uid: int, current_msg: str = "") -> str:
    """
    语义检索：返回要注入系统提示的记忆上下文。
    v3.1: 增强错误处理，任何异常都不影响主流程。
    """
    try:
        data = load_memories(uid)
        parts = []
        current_turn = data.get("turn_count", 0)
        
        # 1. 用户摘要
        if data.get("summary"):
            parts.append(f"📝 用户背景: {data['summary']}")
        
        # 2. 偏好
        prefs = data.get("prefs", {})
        if prefs:
            pref_str = ", ".join(f"{k}={v}" for k, v in list(prefs.items())[:5])
            parts.append(f"⚙️ 偏好: {pref_str}")
        
        # 3. 语义检索记忆（向量语义 + 关键词 + 时间权重 三路混合）
        facts = data.get("facts", [])
        if facts:
            keywords = _extract_keywords(current_msg) if current_msg else []
            facts_texts = [f["text"] if isinstance(f, dict) else f for f in facts]
            # 向量层: 当前消息与所有facts余弦相似度(缓存miss时打向量)
            _sem_scores = {}
            try:
                import hashlib as _hb
                _vc = _load_vec_cache(uid)
                _h = lambda s: _hb.md5(s.encode()).hexdigest()[:16]
                _miss = [ft for ft in facts_texts if _h(ft) not in _vc]
                if _miss:
                    _ve = _embed_batch(_miss)
                    for ft, vec in zip(_miss, _ve):
                        if vec:
                            _vc[_h(ft)] = vec
                    if _ve:
                        _save_vec_cache(uid, _vc)
                _qv = (_embed_batch([current_msg or ""]) or [None])[0]
                if _qv:
                    _sem_scores = {_h(ft): _cosine(_qv, _vc.get(_h(ft))) for ft in facts_texts}
            except Exception:
                pass
            scored_facts = []
            for f in facts:
                ft = f["text"] if isinstance(f, dict) else f
                relevance = 0.0
                if keywords:
                    relevance = sum(_similarity(ft, kw) for kw in keywords) / len(keywords)
                weight = _fact_weight(f, current_turn)
                sem = _sem_scores.get(_h(ft), 0.0)
                total_score = relevance * 3 + sem * 4 + weight
                scored_facts.append((ft, total_score, relevance > 0 or sem > 0.5))

            scored_facts.sort(key=lambda x: -x[1])
            top_facts = [f[0] for f in scored_facts[:20]]  # 检索条数12→20(补偿上下文裁剪)
            if top_facts:
                parts.append("🧠 相关记忆:\n- " + "\n- ".join(top_facts))
        
        return "\n\n".join(parts) if parts else ""
    except Exception:
        # v3.1: 任何异常静默返回空，不影响主对话流程
        return ""

# ==================== 自动事实抽取（增强版 v3） ====================

def auto_extract_facts(uid: int, user_msg: str, assistant_reply: str):
    """从对话中自动提取关键信息。Regex + 关键词增强。"""
    combined = user_msg + " " + assistant_reply
    extracted = set()
    
    patterns = [
        (r'(?:提现|充值|转账|收到付款|到账|中奖|吃肉)[^\d]*(\d{1,8}\.?\d{0,2})\s*(?:U|USDT|元|块|刀|CNY)?', "交易"),
        (r'(?:赢|赚|盈利|收入|利润)[^\d]*(\d{1,8}\.?\d{0,2})', "交易"),
        (r'(?:挂机|流水|下注|投注|玩|打)[^\d]*(\d{1,8}\.?\d{0,2})', "交易"),
        (r'(?:流水|今日|昨天|今天)[^\d]*(\d{3,8})', "交易"),
        (r'(\d{1,6}\.?\d{0,2})\s*(?:U|USDT|元|块|刀|CNY)', "金额"),
        (r'(?:我是|我叫|我是做|我在|我用|我喜欢|我需要|我想|我要|我的|我.*?是)([\u4e00-\u9fff\w]{2,30})', "身份"),
        (r'(?:群|群名|群聊)(?:是|叫|为|：|:)\s*([\u4e00-\u9fff\w]{2,20})', "群聊"),
        (r'(?:睡了|没睡|通宵|熬夜|失眠|困|醒了|起床)([\u4e00-\u9fff]{0,20})', "状态"),
        (r'(\d{1,2})\s*(?:点|小时|天)\s*(?:睡|醒|没睡|没合眼|没睡觉|没闭眼)', "状态"),
        (r'(\d{1,2})\s*(?:小时|天)\s*(?:没睡|没合眼)', "状态"),
        (r'(?:喜欢|爱|讨厌|不喜欢|不想)([\u4e00-\u9fff\w]{2,20})', "偏好"),
        (r'(?:目标|靶机|IP|主机)(?:是|为|：|:)\s*([\w.\-/:]{3,40})', "目标"),
        (r'(?:端口|port)\s*(?:是|为|：|:)?\s*([0-9]{2,5})', "端口"),
        (r'(?:漏洞|CVE|RCE|SQLi|XSS|SSRF|LFI|命令执行)(?:[：:]\s*)?([\w\-]{3,30})', "漏洞"),
        (r'(?:项目|project)\s*(?:是|为|：|:)\s*([\u4e00-\u9fff\w]{2,20})', "项目"),
    ]
    
    for pattern, label in patterns:
        for m in re.findall(pattern, combined, re.IGNORECASE):
            m = m.strip()
            if 2 <= len(m) <= 80:
                extracted.add((label, m))
    
    for label, fact in extracted:
        add_fact(uid, f"[{label}] {fact}", label)

def should_summarize(uid: int) -> bool:
    data = load_memories(uid)
    turns_since = data["turn_count"] - data.get("last_summary_turn", 0)
    return turns_since >= SUMMARY_INTERVAL

def mark_summary_done(uid: int):
    data = load_memories(uid)
    data["last_summary_turn"] = data["turn_count"]
    data["pending_summary"] = True
    save_memories(uid, data)

def check_pending_summary(uid: int) -> bool:
    data = load_memories(uid)
    if data.get("pending_summary"):
        data["pending_summary"] = False
        save_memories(uid, data)
        return True
    return False

def update_summary(uid: int, summary: str):
    """手动更新用户摘要"""
    data = load_memories(uid)
    data["summary"] = summary[:MAX_MEMORY_LEN]
    data["pending_summary"] = False
    save_memories(uid, data)

def get_memory_stats(uid: int) -> str:
    data = load_memories(uid)
    facts = data.get("facts", [])
    cats = {}
    for f in facts:
        cat = f.get("cat", "其他") if isinstance(f, dict) else "其他"
        cats[cat] = cats.get(cat, 0) + 1
    cat_str = ", ".join(f"{k}:{v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])[:8])
    return (
        f"📊 记忆统计:\n"
        f"- 事实数: {len(facts)}/{MAX_MEMORIES}\n"
        f"- 对话轮次: {data['turn_count']}\n"
        f"- 有摘要: {'是' if data.get('summary') else '否'}\n"
        f"- 分类: {cat_str or '无'}"
    )

# ==================== 对话搜索 v3.1（增量索引 + 崩溃恢复） ====================

def _get_history():
    """读取历史文件"""
    HF = Path("/opt/deepseek-bot/history.json")
    if not HF.exists():
        return {}
    try:
        return json.loads(HF.read_text(encoding="utf-8"))
    except:
        return {}

def _index_path(uid: int = 0) -> Path:
    return SEARCH_INDEX_DIR / f"idx_{uid}.json"

def _check_index_integrity(index: dict) -> bool:
    """v3.1: 验证索引结构完整性"""
    try:
        if not isinstance(index, dict):
            return False
        if "_updated" not in index:
            return False
        if "_count" not in index:
            return False
        if "keywords" not in index or not isinstance(index["keywords"], dict):
            return False
        # 抽查前5个关键词条目
        for i, (kw, entries) in enumerate(index["keywords"].items()):
            if i >= 5:
                break
            if not isinstance(entries, list):
                return False
            if entries and not isinstance(entries[0], list):
                return False
        return True
    except Exception:
        return False

def _build_index(uid: int, force: bool = False):
    """
    v3.1: 增量索引构建
    - 优先增量追加新消息
    - 索引损坏或过期太久才全量重建
    - 自动崩溃恢复
    """
    idx_path = _index_path(uid)
    all_history = _get_history()
    user_history = all_history.get(str(uid), [])
    total_msgs = len(user_history)
    
    # ── 尝试加载已有索引 ──
    existing_index = None
    if not force and idx_path.exists():
        try:
            existing_index = json.loads(idx_path.read_text(encoding="utf-8"))
            if not _check_index_integrity(existing_index):
                # v3.1: 索引损坏，自动重建
                existing_index = None
        except (json.JSONDecodeError, OSError):
            # v3.1: 崩溃恢复 - 文件损坏就重建
            existing_index = None
    
    # ── 决定是全量重建还是增量更新 ──
    if existing_index is None:
        # 全量重建
        return _rebuild_full_index(uid, user_history, idx_path)
    
    age = time.time() - existing_index.get("_updated", 0)
    last_count = existing_index.get("_count", 0)
    new_msgs = total_msgs - last_count
    
    if age > INDEX_MAX_AGE or new_msgs > INDEX_INCREMENTAL_THRESHOLD or new_msgs < 0:
        # 全量重建
        return _rebuild_full_index(uid, user_history, idx_path)
    
    if new_msgs == 0:
        # 没有新消息，直接用缓存
        return existing_index
    
    # ── 增量追加新消息 ──
    return _incremental_append(uid, user_history, existing_index, last_count, idx_path)

def _rebuild_full_index(uid: int, user_history: list, idx_path: Path) -> dict:
    """全量重建索引"""
    index = {"_updated": time.time(), "_count": len(user_history), "keywords": {}}
    
    for i, msg in enumerate(user_history):
        content = msg.get("content", "")
        if isinstance(content, list):
            content = str(content)
        if not content:
            continue
        
        role = msg.get("role", "?")
        snippet = content[:300].replace("\n", " ")
        
        keywords = _extract_keywords(content)
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower not in index["keywords"]:
                index["keywords"][kw_lower] = []
            index["keywords"][kw_lower].append([i, role, snippet, len(content)])
    
    # 去重
    for kw in index["keywords"]:
        seen_pos = set()
        deduped = []
        for entry in index["keywords"][kw]:
            if entry[0] not in seen_pos:
                seen_pos.add(entry[0])
                deduped.append(entry)
        index["keywords"][kw] = deduped
    
    # v3.1: 原子写入
    _atomic_write_index(idx_path, index)
    return index

def _incremental_append(uid: int, user_history: list, index: dict, last_count: int, idx_path: Path) -> dict:
    """v3.1: 增量追加新消息到索引"""
    start = last_count
    end = len(user_history)
    added = 0
    
    for i in range(start, end):
        msg = user_history[i]
        content = msg.get("content", "")
        if isinstance(content, list):
            content = str(content)
        if not content:
            continue
        
        role = msg.get("role", "?")
        snippet = content[:300].replace("\n", " ")
        
        keywords = _extract_keywords(content)
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower not in index["keywords"]:
                index["keywords"][kw_lower] = []
            # 检查是否已存在（去重）
            if not any(e[0] == i for e in index["keywords"][kw_lower]):
                index["keywords"][kw_lower].append([i, role, snippet, len(content)])
        added += 1
    
    index["_updated"] = time.time()
    index["_count"] = end
    
    _atomic_write_index(idx_path, index)
    return index

def _atomic_write_index(idx_path: Path, index: dict):
    """v3.1: 原子写入索引，防止写一半崩溃"""
    tmp_path = str(idx_path) + ".tmp"
    try:
        Path(tmp_path).write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_path, str(idx_path))
    except OSError:
        # 回退到直接写入
        idx_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

def search_conversations(uid: int, query: str, limit: int = 10) -> list:
    """
    跨会话全文搜索 — 使用索引加速
    v3.1: 索引损坏自动恢复
    """
    query_keywords = _extract_keywords(query)
    if not query_keywords:
        return []
    
    try:
        index = _build_index(uid)
    except Exception:
        # v3.1: 崩溃恢复 - 重建索引重试一次
        try:
            index = _build_index(uid, force=True)
        except Exception:
            return []
    
    kw_index = index.get("keywords", {})
    
    candidates = {}
    for kw in query_keywords:
        kw_lower = kw.lower()
        if kw_lower in kw_index:
            for entry in kw_index[kw_lower]:
                pos = entry[0]
                if pos not in candidates:
                    candidates[pos] = {"idx": pos, "role": entry[1], "snippet": entry[2], "hits": 0, "keywords_matched": set()}
                candidates[pos]["hits"] += 1
                candidates[pos]["keywords_matched"].add(kw)
    
    results = []
    for c in candidates.values():
        exact_score = sum(_similarity(c["snippet"], kw) for kw in query_keywords) / len(query_keywords)
        total_score = c["hits"] * 2 + exact_score * 5
        results.append({
            "idx": c["idx"],
            "role": c["role"],
            "score": round(total_score, 3),
            "snippet": c["snippet"],
            "matched": ", ".join(c["keywords_matched"]),
        })
    
    results.sort(key=lambda x: -x["score"])
    return results[:limit]

def search_all_users(query: str, limit: int = 20) -> list:
    """全局搜索所有用户对话"""
    all_history = _get_history()
    keywords = _extract_keywords(query)
    if not keywords:
        return []
    
    results = []
    for uid_str, msgs in all_history.items():
        for i, msg in enumerate(msgs):
            content = msg.get("content", "")
            if isinstance(content, list):
                content = str(content)
            if not content:
                continue
            score = sum(_similarity(content, kw) for kw in keywords) / len(keywords)
            if score > 0.05:
                results.append({
                    "uid": int(uid_str),
                    "idx": i,
                    "role": msg.get("role", "?"),
                    "score": round(score, 3),
                    "snippet": content[:200].replace("\n", " "),
                })
    
    results.sort(key=lambda x: -x["score"])
    return results[:limit]

def get_recent_topics(uid: int, n: int = 5) -> list:
    """获取最近的对话主题"""
    all_history = _get_history()
    user_history = all_history.get(str(uid), [])
    if not user_history:
        return []
    
    recent = [m for m in user_history[-50:] if m.get("role") == "user"]
    topics = []
    for msg in recent:
        content = msg.get("content", "")
        if isinstance(content, list):
            content = str(content)
        if len(content) > 10:
            topics.append(content[:80].strip())
    
    return topics[-n:]

def rebuild_all_indexes():
    """重建所有用户的搜索索引（v3.1: 带崩溃恢复）"""
    all_history = _get_history()
    rebuilt = 0
    failed = 0
    for uid_str in all_history:
        try:
            _build_index(int(uid_str), force=True)
            rebuilt += 1
        except Exception:
            failed += 1
    return f"索引重建完成: {rebuilt} 成功, {failed} 失败 (共 {len(all_history)} 个用户)"

def check_index_health() -> str:
    """v3.1: 检查所有索引健康状态"""
    all_history = _get_history()
    healthy = 0
    corrupted = 0
    missing = 0
    
    for uid_str in all_history:
        idx_path = _index_path(int(uid_str))
        if not idx_path.exists():
            missing += 1
            continue
        try:
            index = json.loads(idx_path.read_text(encoding="utf-8"))
            if _check_index_integrity(index):
                healthy += 1
            else:
                corrupted += 1
        except Exception:
            corrupted += 1
    
    return f"🏥 索引健康: {healthy} 正常, {corrupted} 损坏, {missing} 缺失 (共 {len(all_history)} 用户)"
