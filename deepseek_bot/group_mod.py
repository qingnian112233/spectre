"""群管模块: 规则词+高频刷屏检测 → 删+禁言, 记录(充值解禁联动)
规则库可维护: /opt/deepseek-bot/group_mod_rules.json
配置/禁言记录: data.db (gm_mods/gm_bans)
"""
import json, time, sqlite3
from pathlib import Path

DB = "/opt/deepseek-bot/data.db"
RULES_F = Path("/opt/deepseek-bot/group_mod_rules.json")
DEFAULT_RULES = [
    "加群", "一起玩", "返利", "代充", "博彩", "彩票", "提现秒到", "加客服",
    "加微信", "wx:", "vx:", "扣扣", "领红包", "包赔", "刷单", "接单",
    "科技网", "代做", "出租账号", "收购", "出号", "换肤", "外挂",
]
SHOW_RULES = ["加群", "返利", "代充", "博彩", "加客服", "加微信", "刷单", "包赔", "外挂", "出号"]
BAN_DEFAULT_MIN = 60          # 默认禁言60分钟
BAN_DEFAULT_CAP = 1440        # 上限24h(词库命中重复累计上限)
_flood = {}                   # chat+uid -> [时间戳] 高频刷屏检测
_cfg_cache = {}               # chat -> bool(开启)
_ban_min_cache = {}           # chat -> 分钟


def _db():
    conn = sqlite3.connect(DB, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS gm_mods(chat INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 0, ban_min INTEGER DEFAULT 60)")
    conn.execute("CREATE TABLE IF NOT EXISTS gm_bans(uid INTEGER, chat INTEGER, until REAL, reason TEXT, ts REAL, PRIMARY KEY(uid, chat))")
    return conn


def load_rules():
    try:
        return json.loads(RULES_F.read_text(encoding="utf-8"))
    except Exception:
        try:
            RULES_F.write_text(json.dumps(DEFAULT_RULES, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
        return list(DEFAULT_RULES)


RULES = load_rules()


def save_rules(rules):
    global RULES
    RULES = rules
    try:
        RULES_F.write_text(json.dumps(RULES, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def is_on(chat):
    if chat in _cfg_cache:
        return _cfg_cache[chat]
    try:
        c = _db()
        r = c.execute("SELECT enabled FROM gm_mods WHERE chat=?", (chat,)).fetchone()
        c.close()
        v = bool(r[0]) if r else False
    except Exception:
        v = False
    _cfg_cache[chat] = v
    return v


def ban_minutes(chat):
    if chat in _ban_min_cache:
        return _ban_min_cache[chat]
    try:
        c = _db()
        r = c.execute("SELECT ban_min FROM gm_mods WHERE chat=?", (chat,)).fetchone()
        c.close()
        v = int(r[0]) if r and r[0] else BAN_DEFAULT_MIN
    except Exception:
        v = BAN_DEFAULT_MIN
    _ban_min_cache[chat] = v
    return v


def set_cfg(chat, enabled=None, ban_min=None):
    try:
        c = _db()
        cur = c.execute("SELECT enabled, ban_min FROM gm_mods WHERE chat=?", (chat,)).fetchone()
        en = int(enabled) if enabled is not None else (cur[0] if cur else 0)
        bm = int(ban_min) if ban_min is not None else (cur[1] if cur and cur[1] else BAN_DEFAULT_MIN)
        c.execute("INSERT INTO gm_mods(chat, enabled, ban_min) VALUES(?,?,?) ON CONFLICT(chat) DO UPDATE SET enabled=excluded.enabled, ban_min=excluded.ban_min", (chat, en, bm))
        c.commit(); c.close()
    except Exception:
        pass
    _cfg_cache[chat] = bool(enabled) if enabled is not None else bool(en)
    if ban_min is not None:
        _ban_min_cache[chat] = int(ban_min)


def check(text):
    """规则词命中 → 返回词(或 None)"""
    tl = (text or "").lower()
    for w in RULES:
        wl = w.lower()
        if wl and wl in tl:
            return w
    return None


def flood_check(chat, uid, limit=5, window=15):
    """刷屏: 15秒内 >=5 条 → True"""
    key = (chat, uid)
    now = time.time()
    _flood[key] = [t for t in _flood.get(key, []) if now - t < window]
    _flood[key].append(now)
    return len(_flood[key]) >= limit


def record_ban(uid, chat, until, reason):
    try:
        c = _db()
        c.execute("INSERT INTO gm_bans(uid, chat, until, reason, ts) VALUES(?,?,?,?,?) ON CONFLICT(uid,chat) DO UPDATE SET until=excluded.until, reason=excluded.reason, ts=excluded.ts",
                  (uid, chat, until, reason, time.time()))
        c.commit(); c.close()
    except Exception:
        pass


def active_bans(uid):
    """某用户所有未过期禁言 → [(chat, until, reason), ...]"""
    now = time.time()
    try:
        c = _db()
        rows = c.execute("SELECT chat, until, reason FROM gm_bans WHERE uid=? AND until>?", (uid, now)).fetchall()
        c.close()
        return rows
    except Exception:
        return []


def clear_ban(uid, chat):
    try:
        c = _db()
        c.execute("DELETE FROM gm_bans WHERE uid=? AND chat=?", (uid, chat))
        c.commit(); c.close()
    except Exception:
        pass
