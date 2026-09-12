"""SQLite 数据层：上游服务、API 密钥、请求日志、系统设置。"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  api_key TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  priority INTEGER NOT NULL DEFAULT 0,
  timeout REAL NOT NULL DEFAULT 60,
  max_retries INTEGER NOT NULL DEFAULT 1,
  models TEXT NOT NULL DEFAULT '[]',
  headers TEXT NOT NULL DEFAULT '{}',
  weight INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  last_check_at REAL,
  status TEXT NOT NULL DEFAULT 'unknown',
  latency_ms REAL
);
CREATE TABLE IF NOT EXISTS api_keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  rpm INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  last_used_at REAL
);
CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  provider_id INTEGER,
  provider_name TEXT,
  model TEXT,
  endpoint TEXT,
  status INTEGER,
  latency_ms REAL,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  total_tokens INTEGER,
  error TEXT,
  api_key_id INTEGER,
  api_key_name TEXT,
  stream INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_logs_model ON logs(model);
"""


def data_dir() -> Path:
    """返回数据目录（Windows: %LOCALAPPDATA%\\LLMGateway，其他: ~/.llmgateway）。"""
    override = os.environ.get("LLM_GATEWAY_HOME")
    if override:
        d = Path(override)
    elif os.name == "nt":
        d = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LLMGateway"
    else:
        d = Path.home() / ".llmgateway"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "gateway.db"


def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(db_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = sqlite3.connect(db_path())
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(providers)")]
    if "weight" not in cols:
        conn.execute("ALTER TABLE providers ADD COLUMN weight INTEGER NOT NULL DEFAULT 1")
    conn.commit()
    conn.close()


# ---------- settings ----------

def get_setting(key: str, default: str = "") -> str:
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


# ---------- providers ----------

def _provider_from_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    try:
        d["models"] = json.loads(d.get("models") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["models"] = []
    try:
        d["headers"] = json.loads(d.get("headers") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["headers"] = {}
    d["enabled"] = bool(d["enabled"])
    return d


def list_providers(only_enabled: bool = False) -> list:
    sql = "SELECT * FROM providers" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY priority, id"
    return [_provider_from_row(r) for r in get_db().execute(sql).fetchall()]


def get_provider(pid: int):
    r = get_db().execute("SELECT * FROM providers WHERE id=?", (pid,)).fetchone()
    return _provider_from_row(r) if r else None


def get_provider_by_name(name: str):
    r = get_db().execute("SELECT * FROM providers WHERE name=?", (name,)).fetchone()
    return _provider_from_row(r) if r else None


def create_provider(data: dict) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO providers(name,base_url,api_key,enabled,priority,timeout,max_retries,"
        "models,headers,weight,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            data["name"], data["base_url"].rstrip("/"), data.get("api_key", ""),
            1 if data.get("enabled", True) else 0, int(data.get("priority", 0)),
            float(data.get("timeout", 60)), int(data.get("max_retries", 1)),
            json.dumps(data.get("models", []), ensure_ascii=False),
            json.dumps(data.get("headers", {}), ensure_ascii=False),
            max(1, int(data.get("weight", 1))),
            time.time(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_provider(pid: int, data: dict) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE providers SET name=?,base_url=?,api_key=?,enabled=?,priority=?,timeout=?,"
        "max_retries=?,models=?,headers=?,weight=? WHERE id=?",
        (
            data["name"], data["base_url"].rstrip("/"), data.get("api_key", ""),
            1 if data.get("enabled", True) else 0, int(data.get("priority", 0)),
            float(data.get("timeout", 60)), int(data.get("max_retries", 1)),
            json.dumps(data.get("models", []), ensure_ascii=False),
            json.dumps(data.get("headers", {}), ensure_ascii=False),
            max(1, int(data.get("weight", 1))), pid,
        ),
    )
    conn.commit()


def set_provider_enabled(pid: int, enabled: bool) -> None:
    conn = get_db()
    conn.execute("UPDATE providers SET enabled=? WHERE id=?", (1 if enabled else 0, pid))
    conn.commit()


def delete_provider(pid: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM providers WHERE id=?", (pid,))
    conn.commit()


def set_provider_health(pid: int, status: str, latency_ms) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE providers SET status=?, latency_ms=?, last_check_at=? WHERE id=?",
        (status, latency_ms, time.time(), pid),
    )
    conn.commit()


def select_providers_for_model(model: str) -> list:
    """返回支持指定模型且已启用的上游，按优先级排序。"""
    cands = []
    for p in list_providers(only_enabled=True):
        models = p["models"] or []
        if "*" in models or model in models:
            cands.append(p)
    cands.sort(key=lambda p: (p["priority"], p["latency_ms"] if p["latency_ms"] is not None else 1e9))
    return cands


# ---------- api keys ----------

def _key_from_row(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["enabled"] = bool(d["enabled"])
    return d


def list_api_keys() -> list:
    return [_key_from_row(r) for r in get_db().execute("SELECT * FROM api_keys ORDER BY id").fetchall()]


def get_api_key_by_value(value: str):
    r = get_db().execute("SELECT * FROM api_keys WHERE key=?", (value,)).fetchone()
    return _key_from_row(r) if r else None


def get_api_key(kid: int):
    r = get_db().execute("SELECT * FROM api_keys WHERE id=?", (kid,)).fetchone()
    return _key_from_row(r) if r else None


def create_api_key(key: str, name: str, rpm: int = 0) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO api_keys(key,name,enabled,rpm,created_at) VALUES(?,?,1,?,?)",
        (key, name, int(rpm), time.time()),
    )
    conn.commit()
    return cur.lastrowid


def set_api_key_enabled(kid: int, enabled: bool) -> None:
    conn = get_db()
    conn.execute("UPDATE api_keys SET enabled=? WHERE id=?", (1 if enabled else 0, kid))
    conn.commit()


def touch_api_key(kid: int) -> None:
    conn = get_db()
    conn.execute("UPDATE api_keys SET last_used_at=? WHERE id=?", (time.time(), kid))
    conn.commit()


def delete_api_key(kid: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM api_keys WHERE id=?", (kid,))
    conn.commit()


# ---------- logs ----------

def insert_log(entry: dict) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO logs(ts,provider_id,provider_name,model,endpoint,status,latency_ms,"
        "prompt_tokens,completion_tokens,total_tokens,error,api_key_id,api_key_name,stream)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            entry.get("ts", time.time()), entry.get("provider_id"), entry.get("provider_name"),
            entry.get("model"), entry.get("endpoint"), entry.get("status"), entry.get("latency_ms"),
            entry.get("prompt_tokens"), entry.get("completion_tokens"), entry.get("total_tokens"),
            entry.get("error"), entry.get("api_key_id"), entry.get("api_key_name"),
            1 if entry.get("stream") else 0,
        ),
    )
    conn.commit()


def query_logs(limit: int = 100, offset: int = 0, model: str = "", status: int = 0,
               provider_id: int = 0) -> list:
    sql, args = "SELECT * FROM logs WHERE 1=1", []
    if model:
        sql += " AND model LIKE ?"
        args.append(f"%{model}%")
    if status == 4:
        sql += " AND status>=400 AND status<500"
    elif status == 5:
        sql += " AND (status>=500 OR status=0)"
    elif status:
        sql += " AND status=?"
        args.append(status)
    if provider_id:
        sql += " AND provider_id=?"
        args.append(provider_id)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [dict(r) for r in get_db().execute(sql, args).fetchall()]


def count_logs(model: str = "", status: int = 0, provider_id: int = 0) -> int:
    sql, args = "SELECT COUNT(*) c FROM logs WHERE 1=1", []
    if model:
        sql += " AND model LIKE ?"
        args.append(f"%{model}%")
    if status == 4:
        sql += " AND status>=400 AND status<500"
    elif status == 5:
        sql += " AND (status>=500 OR status=0)"
    elif status:
        sql += " AND status=?"
        args.append(status)
    if provider_id:
        sql += " AND provider_id=?"
        args.append(provider_id)
    return get_db().execute(sql, args).fetchone()["c"]


def clear_logs() -> None:
    conn = get_db()
    conn.execute("DELETE FROM logs")
    conn.commit()


def cleanup_old_logs(days: int) -> int:
    conn = get_db()
    cur = conn.execute("DELETE FROM logs WHERE ts < ?", (time.time() - days * 86400,))
    conn.commit()
    return cur.rowcount


# ---------- stats ----------

def stats_summary(days: float = 1.0) -> dict:
    conn = get_db()
    db = conn
    now = time.time()
    since = now - days * 86400

    def agg(s: float) -> dict:
        row = db.execute(
            "SELECT COUNT(*) c, "
            "SUM(CASE WHEN status>=200 AND status<300 THEN 1 ELSE 0 END) ok, "
            "AVG(latency_ms) avg_lat, AVG(total_tokens) avg_tok, "
            "SUM(COALESCE(prompt_tokens,0)) pt, SUM(COALESCE(completion_tokens,0)) ct, "
            "SUM(COALESCE(total_tokens,0)) tt "
            "FROM logs WHERE ts>=?",
            (s,),
        ).fetchone()
        return {
            "total": row["c"] or 0,
            "success": row["ok"] or 0,
            "avg_latency_ms": round(row["avg_lat"] or 0, 1),
            "avg_tokens": round(row["avg_tok"] or 0, 1),
            "prompt_tokens": row["pt"] or 0,
            "completion_tokens": row["ct"] or 0,
            "total_tokens": row["tt"] or 0,
        }

    per_provider = [
        dict(r) for r in db.execute(
            "SELECT provider_name, COUNT(*) c, "
            "SUM(CASE WHEN status>=200 AND status<300 THEN 1 ELSE 0 END) ok, "
            "AVG(latency_ms) avg_lat, "
            "SUM(COALESCE(prompt_tokens,0)) pt, SUM(COALESCE(completion_tokens,0)) ct, "
            "SUM(COALESCE(total_tokens,0)) tt "
            "FROM logs WHERE ts>=? GROUP BY provider_name ORDER BY tt DESC, c DESC",
            (since,),
        ).fetchall()
    ]
    per_model = [
        dict(r) for r in db.execute(
            "SELECT model, COUNT(*) c, "
            "SUM(COALESCE(prompt_tokens,0)) pt, SUM(COALESCE(completion_tokens,0)) ct, "
            "SUM(COALESCE(total_tokens,0)) tt "
            "FROM logs WHERE ts>=? GROUP BY model ORDER BY tt DESC, c DESC LIMIT 10",
            (since,),
        ).fetchall()
    ]
    per_key = [
        dict(r) for r in db.execute(
            "SELECT COALESCE(NULLIF(api_key_name,''),'(未命名)') k, COUNT(*) c, "
            "SUM(COALESCE(prompt_tokens,0)) pt, SUM(COALESCE(completion_tokens,0)) ct, "
            "SUM(COALESCE(total_tokens,0)) tt "
            "FROM logs WHERE ts>=? AND api_key_id IS NOT NULL "
            "GROUP BY api_key_id ORDER BY tt DESC, c DESC LIMIT 10",
            (since,),
        ).fetchall()
    ]
    hourly = [
        {"hour": int(r["h"]), "count": r["c"]}
        for r in db.execute(
            "SELECT CAST((ts - ?)/3600 AS INTEGER) h, COUNT(*) c FROM logs "
            "WHERE ts>=? GROUP BY h ORDER BY h",
            (now - 24 * 3600, now - 24 * 3600),
        ).fetchall()
    ]
    return {
        "today": agg(since),
        "all": agg(0),
        "per_provider": per_provider,
        "per_model": per_model,
        "per_key": per_key,
        "hourly": hourly,
    }
