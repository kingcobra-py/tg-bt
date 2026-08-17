import asyncio
import logging
import time
from collections.abc import Callable

from telethon import events

from app.config import settings
from app.telegram.result_parser import (
    ParsedResult,
    is_antispam_message,
    is_completion_message,
    parse_results,
)
from app.telegram.session_manager import session_manager

logger = logging.getLogger(__name__)


class BotProcessor:
    """Sends commands to a target bot and collects responses until completion."""

    def __init__(
        self,
        session_id: int,
        filename: str,
        bot_username: str,
        command: str,
        on_update: Callable | None = None,
        session_token: str = "",
    ) -> None:
        self.session_id = session_id
        self.filename = filename
        self.session_token = session_token
        self.bot_username = bot_username.lstrip("@")
        self.command = command
        self.on_update = on_update

    async def _emit(self, event_type: str, data: dict) -> None:
        if self.on_update:
            await self.on_update(event_type, data)

    async def process_chunk(self, chunk_text: str) -> dict:
        """
        Send command + chunk to bot, wait for completion marker.
        Returns dict with result_text, found, failed, duration, error.
        """
        start = time.monotonic()
        client = await session_manager.connect(self.session_id, self.filename, self.session_token)
        bot_entity = await client.get_entity(self.bot_username)

        message_body = f"{self.command}\n{chunk_text}" if self.command else chunk_text
        collected: list[str] = []
        done = asyncio.Event()
        error_msg = ""

        @client.on(events.NewMessage(from_users=bot_entity))
        async def handler(event):
            nonlocal error_msg
            text = event.message.message or ""
            if not text:
                return
            collected.append(text)
            await self._emit("message", {"text": text, "session_id": self.session_id})

            if is_completion_message(text) or is_completion_message("\n".join(collected)):
                done.set()

        retries = 0
        while retries <= settings.max_retries:
            try:
                collected.clear()
                done.clear()

                await client.send_message(bot_entity, message_body)
                await self._emit("sent", {"session_id": self.session_id, "chars": len(message_body)})

                try:
                    await asyncio.wait_for(done.wait(), timeout=settings.bot_response_timeout)
                    break
                except asyncio.TimeoutError:
                    full = "\n".join(collected)
                    if collected:
                        logger.warning("Timeout but got partial response for session %s", self.session_id)
                        break
                    raise RuntimeError(f"Bot did not respond within {settings.bot_response_timeout}s")

            except RuntimeError:
                raise
            except Exception as exc:
                full_check = "\n".join(collected)
                is_spam, wait_secs = is_antispam_message(full_check)
                if is_spam and retries < settings.max_retries:
                    retries += 1
                    await self._emit(
                        "antispam",
                        {"session_id": self.session_id, "wait": wait_secs, "retry": retries},
                    )
                    await asyncio.sleep(max(wait_secs, settings.antispam_wait_seconds))
                    continue
                error_msg = str(exc)
                raise

        full_text = "\n".join(collected)

        is_spam, wait_secs = is_antispam_message(full_text)
        while is_spam and retries < settings.max_retries:
            retries += 1
            await self._emit(
                "antispam",
                {"session_id": self.session_id, "wait": wait_secs, "retry": retries},
            )
            await asyncio.sleep(max(wait_secs, settings.antispam_wait_seconds))
            collected.clear()
            done.clear()
            await client.send_message(bot_entity, message_body)
            try:
                await asyncio.wait_for(done.wait(), timeout=settings.bot_response_timeout)
            except asyncio.TimeoutError:
                pass
            full_text = "\n".join(collected)
            is_spam, wait_secs = is_antispam_message(full_text)

        client.remove_event_handler(handler)

        valid, failed = parse_results(full_text)
        duration = time.monotonic() - start

        return {
            "result_text": full_text,
            "found": [r.to_dict() for r in valid],
            "failed": failed,
            "duration": round(duration, 2),
            "error": error_msg,
        }

    async def forward_results(
        self,
        results: list[ParsedResult | dict],
        target_group: str,
    ) -> int:
        forwarded = 0
        for item in results:
            if isinstance(item, dict):
                result = ParsedResult(
                    cc=item.get("cc", ""),
                    status=item.get("status", ""),
                    response=item.get("response", ""),
                    receipt=item.get("receipt", ""),
                )
            else:
                result = item

            if not result.is_valid():
                continue

            msg = result.format_message()
            await session_manager.send_to_group(
                self.session_id, self.filename, target_group, msg, self.session_token
            )
            forwarded += 1
            await self._emit("forwarded", {"session_id": self.session_id, "cc": result.cc})
            await asyncio.sleep(1)

        return forwarded
