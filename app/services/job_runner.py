import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable

from app import database as db
from app.config import settings
from app.services.text_splitter import distribute_chunks, split_into_chunks
from app.telegram.bot_processor import BotProcessor
from app.telegram.result_parser import ParsedResult
from app.telegram.session_manager import session_manager

logger = logging.getLogger(__name__)


class JobRunner:
    """Runs jobs: one chunk at a time per session, sessions in parallel."""

    def __init__(self) -> None:
        self._running: dict[int, asyncio.Task] = {}
        self._listeners: dict[int, list[Callable]] = defaultdict(list)
        self._event_buffer: dict[int, list[tuple[str, dict]]] = defaultdict(list)
        self._buffer_max = 500

    def subscribe(self, job_id: int, callback: Callable) -> None:
        self._listeners[job_id].append(callback)

    def unsubscribe(self, job_id: int, callback: Callable) -> None:
        if callback in self._listeners[job_id]:
            self._listeners[job_id].remove(callback)

    async def replay_events(self, job_id: int, callback: Callable) -> None:
        for event_type, data in self._event_buffer.get(job_id, []):
            try:
                await callback(event_type, data)
            except Exception:
                logger.exception("Replay error for job %s", job_id)

    async def _emit(self, job_id: int, event_type: str, data: dict) -> None:
        buf = self._event_buffer[job_id]
        buf.append((event_type, data))
        if len(buf) > self._buffer_max:
            del buf[: len(buf) - self._buffer_max]
        for cb in self._listeners.get(job_id, []):
            try:
                await cb(event_type, data)
            except Exception:
                logger.exception("Listener error for job %s", job_id)

    def is_running(self, job_id: int) -> bool:
        task = self._running.get(job_id)
        return task is not None and not task.done()

    async def start_job(self, job_id: int) -> None:
        if self.is_running(job_id):
            raise RuntimeError(f"Job {job_id} is already running")

        task = asyncio.create_task(self._run_job(job_id))
        self._running[job_id] = task

    async def _run_job(self, job_id: int) -> None:
        try:
            job = await db.get_job(job_id)
            if not job:
                return

            await db.update_job_stats(job_id, status="running")
            await self._emit(job_id, "job_started", {"job_id": job_id})

            chunks = await db.get_job_chunks(job_id)
            by_session: dict[int, list[dict]] = defaultdict(list)
            for chunk in chunks:
                by_session[chunk["session_id"]].append(chunk)

            completed = 0
            stats_lock = asyncio.Lock()
            totals = {"found": 0, "failed": 0, "forwarded": 0}

            async def run_session_chunks(session_id: int, session_chunks: list[dict]) -> dict:
                nonlocal completed

                session = await db.get_session(session_id)
                if not session:
                    return {"found": 0, "failed": 0, "forwarded": 0}

                session_found = 0
                session_failed = 0
                session_forwarded = 0

                chunk_live = {"found": 0, "failed": 0, "forwarded": 0}

                async def on_update(event_type: str, data: dict) -> None:
                    bump = False
                    async with stats_lock:
                        if event_type == "result_found":
                            totals["found"] += 1
                            chunk_live["found"] += 1
                            bump = True
                        elif event_type == "result_failed":
                            totals["failed"] += 1
                            chunk_live["failed"] += 1
                            bump = True
                        elif event_type == "forwarded":
                            totals["forwarded"] += 1
                            chunk_live["forwarded"] += 1
                            bump = True

                        if bump:
                            await db.update_job_stats(
                                job_id,
                                found_count=totals["found"],
                                failed_count=totals["failed"],
                                forwarded_count=totals["forwarded"],
                            )
                            await self._emit(
                                job_id,
                                "stats_update",
                                {
                                    "found_count": totals["found"],
                                    "failed_count": totals["failed"],
                                    "forwarded_count": totals["forwarded"],
                                    "completed_chunks": completed,
                                    "total_chunks": job["total_chunks"],
                                },
                            )
                    await self._emit(job_id, event_type, {**data, "job_id": job_id})

                processor = BotProcessor(
                    session_id=session_id,
                    filename=session.get("filename") or "",
                    session_token=session.get("session_token") or "",
                    bot_username=job["bot_username"],
                    command=job["command"],
                    target_group=job["target_group"],
                    on_update=on_update,
                )

                lock = session_manager.get_lock(session_id)
                async with lock:
                    for chunk in sorted(session_chunks, key=lambda c: c["chunk_index"]):
                        chunk_live["found"] = 0
                        chunk_live["failed"] = 0
                        chunk_live["forwarded"] = 0
                        await db.mark_chunk_started(chunk["id"])
                        await self._emit(
                            job_id,
                            "chunk_started",
                            {
                                "chunk_id": chunk["id"],
                                "chunk_index": chunk["chunk_index"],
                                "session_id": session_id,
                                "total_chunks": job["total_chunks"],
                            },
                        )

                        found_dicts: list = []
                        failed_list: list = []

                        try:
                            result = await processor.process_chunk(chunk["chunk_text"])
                            found_dicts = result["found"]
                            failed_list = result["failed"]

                            session_found += chunk_live["found"]
                            session_failed += chunk_live["failed"]
                            session_forwarded += chunk_live["forwarded"]

                            await db.update_chunk(
                                chunk["id"],
                                status="completed",
                                result_text=result["result_text"],
                                found_results=found_dicts,
                                failed_results=failed_list,
                                duration_seconds=result["duration"],
                            )

                        except Exception as exc:
                            logger.exception("Chunk %s failed", chunk["id"])
                            await db.update_chunk(
                                chunk["id"],
                                status="failed",
                                error=str(exc),
                            )
                            async with stats_lock:
                                totals["failed"] += 1
                                chunk_live["failed"] += 1
                                await db.update_job_stats(
                                    job_id,
                                    failed_count=totals["failed"],
                                )
                                await self._emit(
                                    job_id,
                                    "stats_update",
                                    {
                                        "found_count": totals["found"],
                                        "failed_count": totals["failed"],
                                        "forwarded_count": totals["forwarded"],
                                        "completed_chunks": completed,
                                        "total_chunks": job["total_chunks"],
                                    },
                                )
                            session_failed += 1

                        async with stats_lock:
                            completed += 1
                            await db.update_job_stats(job_id, completed_chunks=completed)

                        await self._emit(
                            job_id,
                            "chunk_done",
                            {
                                "chunk_id": chunk["id"],
                                "chunk_index": chunk["chunk_index"],
                                "session_id": session_id,
                                "chunk_found": chunk_live["found"],
                                "chunk_failed": chunk_live["failed"],
                                "chunk_forwarded": chunk_live["forwarded"],
                                "found_count": totals["found"],
                                "failed_count": totals["failed"],
                                "forwarded_count": totals["forwarded"],
                                "completed_chunks": completed,
                                "total_chunks": job["total_chunks"],
                            },
                        )

                return {
                    "found": session_found,
                    "failed": session_failed,
                    "forwarded": session_forwarded,
                }

            tasks = [
                run_session_chunks(sid, schunks)
                for sid, schunks in by_session.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            total_found = totals["found"]
            total_failed = totals["failed"]
            total_forwarded = totals["forwarded"]

            await db.update_job_stats(
                job_id,
                status="completed",
                completed_chunks=completed,
                found_count=total_found,
                failed_count=total_failed,
                forwarded_count=total_forwarded,
            )
            await self._emit(
                job_id,
                "job_completed",
                {
                    "job_id": job_id,
                    "found": total_found,
                    "failed": total_failed,
                    "forwarded": total_forwarded,
                    "found_count": total_found,
                    "failed_count": total_failed,
                    "forwarded_count": total_forwarded,
                    "completed_chunks": completed,
                    "total_chunks": job["total_chunks"],
                },
            )

        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            await db.update_job_stats(job_id, status="failed")
            await self._emit(job_id, "job_failed", {"job_id": job_id, "error": str(exc)})
        finally:
            self._running.pop(job_id, None)

    async def create_and_start(
        self,
        input_text: str,
        bot_username: str,
        command: str,
        target_group: str,
        session_ids: list[int],
    ) -> int:
        chunks = split_into_chunks(input_text, settings.lines_per_chunk)
        if not chunks:
            raise ValueError("Input text produced no lines to process")

        assignments = distribute_chunks(chunks, session_ids)
        job_id = await db.create_job(
            input_text=input_text,
            bot_username=bot_username,
            command=command,
            target_group=target_group,
            session_ids=session_ids,
            total_chunks=len(chunks),
        )

        for session_id, chunk_index, chunk_text in assignments:
            await db.add_job_chunk(job_id, session_id, chunk_index, chunk_text)

        await self.start_job(job_id)
        return job_id


job_runner = JobRunner()
