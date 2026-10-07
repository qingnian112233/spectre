"""NFTNB Bot - SQLite 持久化 + 项目管理"""
import sqlite3, json, time, threading
from pathlib import Path

DB = Path("/opt/deepseek-bot/data.db")
DB.parent.mkdir(exist_ok=True)

_conn = None
_lock = threading.Lock()

def _get():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(str(DB), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
    return _conn

def init():
    db = _get()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER NOT NULL,
        name TEXT NOT NULL,
        target TEXT DEFAULT '',
        status TEXT DEFAULT 'active',
        created_at REAL DEFAULT (strftime('%s','now')),
        updated_at REAL DEFAULT (strftime('%s','now')),
        UNIQUE(uid, name)
    );

    CREATE TABLE IF NOT EXISTS scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        uid INTEGER NOT NULL,
        tool TEXT NOT NULL,
        target TEXT NOT NULL,
        raw_output TEXT DEFAULT '',
        parsed_json TEXT DEFAULT '',
        status TEXT DEFAULT 'running',
        started_at REAL DEFAULT (strftime('%s','now')),
        finished_at REAL,
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );

    CREATE TABLE IF NOT EXISTS findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_id INTEGER NOT NULL,
        project_id INTEGER NOT NULL,
        uid INTEGER NOT NULL,
        severity TEXT DEFAULT 'info',  -- critical/high/medium/low/info
        title TEXT DEFAULT '',
        description TEXT DEFAULT '',
        target TEXT DEFAULT '',
        evidence TEXT DEFAULT '',
        created_at REAL DEFAULT (strftime('%s','now')),
        FOREIGN KEY(scan_id) REFERENCES scans(id),
        FOREIGN KEY(project_id) REFERENCES projects(id)
    );

    CREATE TABLE IF NOT EXISTS schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER NOT NULL,
        project_id INTEGER NOT NULL,
        name TEXT DEFAULT '',
        cron_expr TEXT NOT NULL,   -- "0 3 * * *" 格式
        action TEXT NOT NULL,      -- playbook/scan/recon
        target TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1,
        last_run REAL,
        next_run REAL,
        created_at REAL DEFAULT (strftime('%s','now'))
    );

    CREATE TABLE IF NOT EXISTS agent_memory (
        uid INTEGER NOT NULL,
        project_id INTEGER NOT NULL DEFAULT 0,
        key TEXT NOT NULL,
        value TEXT DEFAULT '',
        updated_at REAL DEFAULT (strftime('%s','now')),
        PRIMARY KEY(uid, project_id, key)
    );

    CREATE INDEX IF NOT EXISTS idx_scans_project ON scans(project_id);
    CREATE INDEX IF NOT EXISTS idx_findings_project ON findings(project_id);
    CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
    """)
    db.commit()

# ==================== 项目管理 ====================
def project_create(uid: int, name: str, target: str = "") -> int:
    db = _get()
    try:
        cur = db.execute(
            "INSERT INTO projects(uid, name, target) VALUES(?,?,?)",
            (uid, name, target))
        db.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return 0  # 同名项目已存在

def project_list(uid: int) -> list:
    db = _get()
    rows = db.execute(
        "SELECT id, name, target, status, created_at FROM projects WHERE uid=? ORDER BY updated_at DESC",
        (uid,)).fetchall()
    return [dict(r) for r in rows]

def project_set_active(uid: int, project_id: int) -> bool:
    """切换活跃项目"""
    db = _get()
    r = db.execute("SELECT id FROM projects WHERE id=? AND uid=?", (project_id, uid)).fetchone()
    if not r: return False
    db.execute("UPDATE projects SET updated_at=strftime('%s','now') WHERE id=?", (project_id,))
    db.commit()
    return True

def project_delete(uid: int, project_id: int) -> bool:
    db = _get()
    r = db.execute("SELECT id FROM projects WHERE id=? AND uid=?", (project_id, uid)).fetchone()
    if not r: return False
    db.execute("DELETE FROM scans WHERE project_id=?", (project_id,))
    db.execute("DELETE FROM findings WHERE project_id=?", (project_id,))
    db.execute("DELETE FROM projects WHERE id=?", (project_id,))
    db.commit()
    return True

# ==================== 扫描记录 ====================
def scan_start(project_id: int, uid: int, tool: str, target: str) -> int:
    db = _get()
    cur = db.execute(
        "INSERT INTO scans(project_id, uid, tool, target) VALUES(?,?,?,?)",
        (project_id, uid, tool, target))
    db.commit()
    return cur.lastrowid

def scan_finish(scan_id: int, raw: str, parsed: dict = None):
    db = _get()
    db.execute(
        "UPDATE scans SET raw_output=?, parsed_json=?, status='done', finished_at=strftime('%s','now') WHERE id=?",
        (raw[:8000], json.dumps(parsed, ensure_ascii=False) if parsed else '', scan_id))
    db.commit()

def scan_list(project_id: int, limit: int = 20) -> list:
    db = _get()
    rows = db.execute(
        "SELECT id, tool, target, status, started_at, finished_at FROM scans WHERE project_id=? ORDER BY started_at DESC LIMIT ?",
        (project_id, limit)).fetchall()
    return [dict(r) for r in rows]

def scan_get(scan_id: int) -> dict:
    db = _get()
    r = db.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
    return dict(r) if r else {}

# ==================== 漏洞记录 ====================
def finding_add(scan_id: int, project_id: int, uid: int,
                severity: str, title: str, description: str = "",
                target: str = "", evidence: str = "") -> int:
    db = _get()
    cur = db.execute(
        "INSERT INTO findings(scan_id, project_id, uid, severity, title, description, target, evidence) VALUES(?,?,?,?,?,?,?,?)",
        (scan_id, project_id, uid, severity, title, description, target, evidence))
    db.commit()
    return cur.lastrowid

def finding_list(project_id: int, severity: str = None) -> list:
    db = _get()
    q = "SELECT * FROM findings WHERE project_id=? "
    args = [project_id]
    if severity:
        q += "AND severity=? "
        args.append(severity)
    q += "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"
    return [dict(r) for r in db.execute(q, args).fetchall()]

def finding_stats(project_id: int) -> dict:
    db = _get()
    rows = db.execute(
        "SELECT severity, COUNT(*) as cnt FROM findings WHERE project_id=? GROUP BY severity",
        (project_id,)).fetchall()
    return {r['severity']: r['cnt'] for r in rows}

# ==================== Agent 记忆 ====================
def memory_set(uid: int, project_id: int, key: str, value: str):
    db = _get()
    db.execute(
        "INSERT OR REPLACE INTO agent_memory(uid, project_id, key, value, updated_at) VALUES(?,?,?,?,strftime('%s','now'))",
        (uid, project_id, key, value))
    db.commit()

def memory_get(uid: int, project_id: int, key: str) -> str:
    db = _get()
    r = db.execute(
        "SELECT value FROM agent_memory WHERE uid=? AND project_id=? AND key=?",
        (uid, project_id, key)).fetchone()
    return r['value'] if r else ""

def memory_all(uid: int, project_id: int) -> dict:
    db = _get()
    rows = db.execute(
        "SELECT key, value FROM agent_memory WHERE uid=? AND project_id=?",
        (uid, project_id)).fetchall()
    return {r['key']: r['value'] for r in rows}

# ==================== 定时任务 ====================
def schedule_add(uid: int, project_id: int, name: str, cron_expr: str,
                 action: str, target: str = "") -> int:
    db = _get()
    cur = db.execute(
        "INSERT INTO schedules(uid, project_id, name, cron_expr, action, target) VALUES(?,?,?,?,?,?)",
        (uid, project_id, name, cron_expr, action, target))
    db.commit()
    return cur.lastrowid

def schedule_list(uid: int, project_id: int = None) -> list:
    db = _get()
    q = "SELECT * FROM schedules WHERE uid=?"
    args = [uid]
    if project_id:
        q += " AND project_id=?"
        args.append(project_id)
    return [dict(r) for r in db.execute(q, args).fetchall()]

def schedule_toggle(schedule_id: int, enabled: bool):
    db = _get()
    db.execute("UPDATE schedules SET enabled=? WHERE id=?", (1 if enabled else 0, schedule_id))
    db.commit()

def schedule_delete(schedule_id: int):
    db = _get()
    db.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
    db.commit()

def schedule_update_run(schedule_id: int):
    db = _get()
    db.execute("UPDATE schedules SET last_run=strftime('%s','now') WHERE id=?", (schedule_id,))
    db.commit()

# 启动时初始化
init()
print(f"[DB] SQLite ready: {DB} ({DB.stat().st_size if DB.exists() else 0}b)")
