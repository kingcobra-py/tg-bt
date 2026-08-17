import logging
import re
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import database as db
from app.config import settings
from app.config_store import load_settings, save_settings
from app.services.job_runner import job_runner
from app.telegram.session_manager import session_manager

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.session_dir.mkdir(parents=True, exist_ok=True)
    Path("./data").mkdir(parents=True, exist_ok=True)
    await db.init_db()
    await load_settings()
    yield
    await session_manager.disconnect_all()


app = FastAPI(title="TG-BT Multi-Session Bot Manager", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


class ConfigUpdate(BaseModel):
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    lines_per_chunk: int | None = Field(default=None, ge=1, le=100)
    antispam_wait_seconds: int | None = Field(default=None, ge=1, le=60)
    bot_response_timeout: int | None = Field(default=None, ge=30, le=600)
    max_retries: int | None = Field(default=None, ge=1, le=50)


class JobCreate(BaseModel):
    input_text: str
    bot_username: str
    command: str = ""
    target_group: str
    session_ids: list[int]


class PhoneLoginStart(BaseModel):
    name: str
    phone: str


class PhoneLoginVerify(BaseModel):
    name: str
    phone: str
    code: str
    password: str = ""


class SessionTokenCreate(BaseModel):
    name: str
    token: str


@app.get("/")
async def index():
    return FileResponse("app/static/index.html")


@app.get("/api/config")
async def get_config():
    hash_saved = bool(settings.telegram_api_hash) or bool(await db.get_config("telegram_api_hash", ""))
    return {
        "telegram_api_id": settings.telegram_api_id,
        "telegram_api_hash_set": hash_saved,
        "lines_per_chunk": settings.lines_per_chunk,
        "antispam_wait_seconds": settings.antispam_wait_seconds,
        "bot_response_timeout": settings.bot_response_timeout,
        "max_retries": settings.max_retries,
        "default_bot": await db.get_config("default_bot", ""),
        "default_command": await db.get_config("default_command", ""),
        "default_group": await db.get_config("default_group", ""),
    }


@app.post("/api/config")
async def update_config(body: ConfigUpdate):
    updates: dict = {}
    if body.telegram_api_id is not None:
        updates["telegram_api_id"] = body.telegram_api_id
    if body.telegram_api_hash is not None and body.telegram_api_hash.strip():
        updates["telegram_api_hash"] = body.telegram_api_hash.strip()
    if body.lines_per_chunk is not None:
        updates["lines_per_chunk"] = body.lines_per_chunk
    if body.antispam_wait_seconds is not None:
        updates["antispam_wait_seconds"] = body.antispam_wait_seconds
    if body.bot_response_timeout is not None:
        updates["bot_response_timeout"] = body.bot_response_timeout
    if body.max_retries is not None:
        updates["max_retries"] = body.max_retries

    if not updates:
        raise HTTPException(400, "No settings to save")

    await save_settings(updates)
    return {
        "ok": True,
        "message": "Config saved successfully",
        "telegram_api_id": settings.telegram_api_id,
        "telegram_api_hash_set": bool(settings.telegram_api_hash),
    }


@app.post("/api/config/defaults")
async def set_defaults(
    default_bot: str = Form(""),
    default_command: str = Form(""),
    default_group: str = Form(""),
):
    await db.set_config("default_bot", default_bot)
    await db.set_config("default_command", default_command)
    await db.set_config("default_group", default_group)
    return {"ok": True, "message": "Defaults saved successfully"}


@app.get("/api/sessions")
async def list_sessions():
    sessions = await db.list_sessions()
    enriched = []
    for s in sessions:
        info = {**s}
        try:
            me = await session_manager.get_me(
                s["id"],
                s.get("filename") or "",
                s.get("session_token") or "",
            )
            info["user"] = me
            info["connected"] = True
            info["type"] = "token" if s.get("session_token") else "file"
        except Exception as exc:
            info["user"] = {}
            info["connected"] = False
            info["error"] = str(exc)
        enriched.append(info)
    return enriched


@app.post("/api/sessions/upload")
async def upload_session(
    name: str = Form(...),
    file: UploadFile = File(...),
):
    if not file.filename or not file.filename.endswith(".session"):
        raise HTTPException(400, "Upload a .session file")

    safe_name = re.sub(r"[^\w\-]", "_", name)
    dest = settings.session_dir / f"{safe_name}.session"
    settings.session_dir.mkdir(parents=True, exist_ok=True)

    async with aiofiles.open(dest, "wb") as f:
        content = await file.read()
        await f.write(content)

    session_id = await db.add_session(name=safe_name, filename=f"{safe_name}.session")
    try:
        me = await session_manager.get_me(session_id, f"{safe_name}.session")
        phone = me.get("phone", "")
        return {"id": session_id, "name": safe_name, "user": me, "phone": phone}
    except Exception as exc:
        await db.delete_session(session_id)
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Session invalid: {exc}") from exc


@app.post("/api/sessions/token")
async def add_session_token(body: SessionTokenCreate):
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise HTTPException(400, "Set TELEGRAM_API_ID and TELEGRAM_API_HASH first")
    if not body.token.strip():
        raise HTTPException(400, "Session token is empty")

    safe_name = re.sub(r"[^\w\-]", "_", body.name)
    try:
        user_info = await session_manager.validate_string_token(body.token)
    except Exception as exc:
        raise HTTPException(400, f"Invalid token: {exc}") from exc

    session_id = await db.add_session(
        name=safe_name,
        filename="",
        phone=user_info.get("phone", ""),
        session_token=body.token.strip(),
    )
    return {"id": session_id, "name": safe_name, "user": user_info, "type": "token"}


@app.post("/api/sessions/login/start")
async def login_start(body: PhoneLoginStart):
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise HTTPException(400, "Set TELEGRAM_API_ID and TELEGRAM_API_HASH first")
    try:
        await session_manager.send_login_code(body.name, body.phone)
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "message": "Code sent. Enter it and click Login / Verify."}


@app.post("/api/sessions/login/verify")
async def login_verify(body: PhoneLoginVerify):
    try:
        filename, user_info = await session_manager.verify_login_code(
            body.name,
            body.phone,
            body.code,
            body.password,
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    safe_name = re.sub(r"[^\w\-]", "_", body.name)
    session_id = await db.add_session(
        name=safe_name,
        filename=filename,
        phone=user_info.get("phone", body.phone),
    )
    return {"id": session_id, "name": safe_name, "user": user_info}


@app.delete("/api/sessions/{session_id}")
async def remove_session(session_id: int):
    session = await db.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    await session_manager.disconnect(session_id)
    if session.get("filename"):
        filepath = settings.session_dir / session["filename"]
        filepath.unlink(missing_ok=True)
        journal = Path(str(filepath) + "-journal")
        journal.unlink(missing_ok=True)
    await db.delete_session(session_id)
    return {"ok": True}


@app.post("/api/jobs/preview")
async def preview_job(body: JobCreate):
    from app.services.text_splitter import split_into_chunks, distribute_chunks

    chunks = split_into_chunks(body.input_text, settings.lines_per_chunk)
    assignments = distribute_chunks(chunks, body.session_ids) if body.session_ids else []
    by_session: dict[int, int] = {}
    for sid, _, _ in assignments:
        by_session[sid] = by_session.get(sid, 0) + 1

    return {
        "total_lines": len([ln for ln in body.input_text.splitlines() if ln.strip()]),
        "total_chunks": len(chunks),
        "lines_per_chunk": settings.lines_per_chunk,
        "assignments": [
            {"session_id": sid, "chunk_index": idx, "lines": len(txt.splitlines())}
            for sid, idx, txt in assignments
        ],
        "chunks_per_session": by_session,
    }


@app.post("/api/jobs")
async def create_job(body: JobCreate):
    if not body.session_ids:
        raise HTTPException(400, "Select at least one session")
    if not body.bot_username:
        raise HTTPException(400, "Bot username is required")
    if not body.target_group:
        raise HTTPException(400, "Target group is required")
    if not body.input_text.strip():
        raise HTTPException(400, "Input text is empty")

    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise HTTPException(400, "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in config first")

    try:
        job_id = await job_runner.create_and_start(
            input_text=body.input_text,
            bot_username=body.bot_username,
            command=body.command,
            target_group=body.target_group,
            session_ids=body.session_ids,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc

    return {"job_id": job_id}


@app.get("/api/jobs")
async def list_jobs():
    return await db.list_jobs()


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: int):
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    chunks = await db.get_job_chunks(job_id)
    return {"job": job, "chunks": chunks}


@app.websocket("/ws/jobs/{job_id}")
async def job_ws(websocket: WebSocket, job_id: int):
    await websocket.accept()

    async def listener(event_type: str, data: dict):
        try:
            await websocket.send_json({"type": event_type, "data": data})
        except Exception:
            logger.debug("WS send failed for job %s event %s", job_id, event_type)

    job_runner.subscribe(job_id, listener)

    async def ping_loop():
        while True:
            await asyncio.sleep(20)
            await websocket.send_json({"type": "ping", "data": {}})

    ping_task = asyncio.create_task(ping_loop())
    try:
        job = await db.get_job(job_id)
        if job:
            await websocket.send_json({"type": "snapshot", "data": job})
            chunks = await db.get_job_chunks(job_id)
            await websocket.send_json({"type": "chunks", "data": chunks})

        # Replay events that fired before this socket connected
        await job_runner.replay_events(job_id, listener)

        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        ping_task.cancel()
        job_runner.unsubscribe(job_id, listener)


def main():
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=settings.debug)


if __name__ == "__main__":
    main()
