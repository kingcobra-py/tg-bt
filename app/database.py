import json
from pathlib import Path

import aiosqlite

from app.config import settings

DB_PATH = settings.database_path


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL DEFAULT '',
                phone TEXT DEFAULT '',
                session_token TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT DEFAULT 'pending',
                input_text TEXT NOT NULL,
                bot_username TEXT NOT NULL,
                command TEXT NOT NULL,
                target_group TEXT NOT NULL,
                total_chunks INTEGER DEFAULT 0,
                completed_chunks INTEGER DEFAULT 0,
                found_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                forwarded_count INTEGER DEFAULT 0,
                session_ids TEXT DEFAULT '[]',
                created_at TEXT DEFAULT (datetime('now')),
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS job_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                session_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                result_text TEXT DEFAULT '',
                found_results TEXT DEFAULT '[]',
                failed_results TEXT DEFAULT '[]',
                duration_seconds REAL DEFAULT 0,
                error TEXT DEFAULT '',
                started_at TEXT,
                finished_at TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs(id),
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        await db.commit()
        try:
            await db.execute("ALTER TABLE sessions ADD COLUMN session_token TEXT DEFAULT ''")
            await db.commit()
        except Exception:
            pass


async def get_config(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT value FROM app_config WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row["value"] if row else default


async def set_config(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO app_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


async def list_sessions() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sessions ORDER BY id") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def add_session(name: str, filename: str = "", phone: str = "", session_token: str = "") -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO sessions (name, filename, phone, session_token) VALUES (?, ?, ?, ?)",
            (name, filename, phone, session_token),
        )
        await db.commit()
        return cur.lastrowid


async def delete_session(session_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        await db.commit()


async def get_session(session_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_job(
    input_text: str,
    bot_username: str,
    command: str,
    target_group: str,
    session_ids: list[int],
    total_chunks: int,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO jobs (input_text, bot_username, command, target_group, session_ids, total_chunks, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (input_text, bot_username, command, target_group, json.dumps(session_ids), total_chunks),
        )
        await db.commit()
        return cur.lastrowid


async def add_job_chunk(job_id: int, session_id: int, chunk_index: int, chunk_text: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO job_chunks (job_id, session_id, chunk_index, chunk_text)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, session_id, chunk_index, chunk_text),
        )
        await db.commit()
        return cur.lastrowid


async def get_job(job_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            data = dict(row)
            data["session_ids"] = json.loads(data["session_ids"])
            return data


async def get_job_chunks(job_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM job_chunks WHERE job_id = ? ORDER BY chunk_index",
            (job_id,),
        ) as cur:
            rows = await cur.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["found_results"] = json.loads(d["found_results"])
                d["failed_results"] = json.loads(d["failed_results"])
                result.append(d)
            return result


async def update_chunk(
    chunk_id: int,
    status: str,
    result_text: str = "",
    found_results: list | None = None,
    failed_results: list | None = None,
    duration_seconds: float = 0,
    error: str = "",
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE job_chunks SET
                status = ?,
                result_text = ?,
                found_results = ?,
                failed_results = ?,
                duration_seconds = ?,
                error = ?,
                finished_at = datetime('now')
            WHERE id = ?
            """,
            (
                status,
                result_text,
                json.dumps(found_results or []),
                json.dumps(failed_results or []),
                duration_seconds,
                error,
                chunk_id,
            ),
        )
        await db.commit()


async def mark_chunk_started(chunk_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE job_chunks SET status = 'running', started_at = datetime('now') WHERE id = ?",
            (chunk_id,),
        )
        await db.commit()


async def update_job_stats(
    job_id: int,
    status: str | None = None,
    completed_chunks: int | None = None,
    found_count: int | None = None,
    failed_count: int | None = None,
    forwarded_count: int | None = None,
) -> None:
    fields = []
    values: list = []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if completed_chunks is not None:
        fields.append("completed_chunks = ?")
        values.append(completed_chunks)
    if found_count is not None:
        fields.append("found_count = ?")
        values.append(found_count)
    if failed_count is not None:
        fields.append("failed_count = ?")
        values.append(failed_count)
    if forwarded_count is not None:
        fields.append("forwarded_count = ?")
        values.append(forwarded_count)
    if status in ("completed", "stopped", "failed"):
        fields.append("finished_at = datetime('now')")

    if not fields:
        return

    values.append(job_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()


async def list_jobs(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, status, bot_username, total_chunks, completed_chunks, found_count, failed_count, forwarded_count, created_at, finished_at FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
