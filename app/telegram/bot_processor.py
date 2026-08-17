import asyncio
import logging
import time
from collections.abc import Callable

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
        target_group: str = "",
        on_update: Callable | None = None,
        session_token: str = "",
    ) -> None:
        self.session_id = session_id
        self.filename = filename
        self.session_token = session_token
        self.bot_username = bot_username.lstrip("@")
        self.target_group = target_group
        self.command = command
        self.on_update = on_update

    async def _emit(self, event_type: str, data: dict) -> None:
        if self.on_update:
            await self.on_update(event_type, data)

    async def _forward_one(self, result: ParsedResult) -> bool:
        if not self.target_group or not result.is_valid():
            return False
        msg = result.format_message()
        await session_manager.send_to_group(
            self.session_id, self.filename, self.target_group, msg, self.session_token
        )
        await asyncio.sleep(0.5)
        return True

    async def _check_live_results(
        self,
        collected: list[str],
        seen_found: set[str],
        seen_failed: set[str],
        live_found: list[dict],
        live_failed: list[str],
    ) -> None:
        valid, failed = parse_results("\n".join(collected))
        for r in valid:
            if r.cc in seen_found:
                continue
            seen_found.add(r.cc)
            d = r.to_dict()
            live_found.append(d)
            await self._emit("result_found", {"result": d, "session_id": self.session_id})
            if await self._forward_one(r):
                await self._emit(
                    "forwarded",
                    {"session_id": self.session_id, "cc": r.cc, "result": d},
                )
        for f in failed:
            key = f[:120]
            if key in seen_failed:
                continue
            seen_failed.add(key)
            live_failed.append(f)
            await self._emit("result_failed", {"text": f, "session_id": self.session_id})

    async def process_chunk(self, chunk_text: str) -> dict:
        """Send command + chunk, collect all bot messages until completion marker."""
        start = time.monotonic()
        client = await session_manager.connect(self.session_id, self.filename, self.session_token)
        bot_entity = await client.get_entity(self.bot_username)
        message_body = f"{self.command}\n{chunk_text}" if self.command else chunk_text
        collected: list[str] = []
        seen_found: set[str] = set()
        seen_failed: set[str] = set()
        live_found: list[dict] = []
        live_failed: list[str] = []
        retries = 0

        while retries <= settings.max_retries:
            collected.clear()
            try:
                async with client.conversation(bot_entity, timeout=settings.bot_response_timeout) as conv:
                    await conv.send_message(message_body)
                    await self._emit("sent", {"session_id": self.session_id, "chars": len(message_body)})

                    while True:
                        try:
                            response = await conv.get_response(timeout=120)
                        except asyncio.TimeoutError:
                            full = "\n".join(collected)
                            if collected and is_completion_message(full):
                                break
                            raise RuntimeError(
                                f"Bot did not respond within timeout (session {self.session_id})"
                            )

                        text = response.text or ""
                        if not text:
                            continue

                        collected.append(text)
                        await self._emit("message", {"text": text, "session_id": self.session_id})
                        await self._check_live_results(
                            collected, seen_found, seen_failed, live_found, live_failed
                        )

                        is_spam, wait_secs = is_antispam_message(text)
                        if is_spam:
                            retries += 1
                            await self._emit(
                                "antispam",
                                {"session_id": self.session_id, "wait": wait_secs, "retry": retries},
                            )
                            await asyncio.sleep(max(wait_secs, settings.antispam_wait_seconds))
                            await conv.send_message(message_body)
                            continue

                        full = "\n".join(collected)
                        if is_completion_message(text) or is_completion_message(full):
                            break

                full_text = "\n".join(collected)
                if full_text and not is_antispam_message(full_text)[0]:
                    break

            except RuntimeError:
                raise
            except Exception as exc:
                logger.exception("Chunk processing error session %s", self.session_id)
                if retries < settings.max_retries:
                    retries += 1
                    await asyncio.sleep(settings.antispam_wait_seconds)
                    continue
                raise RuntimeError(str(exc)) from exc

            retries += 1

        full_text = "\n".join(collected)
        if not full_text:
            raise RuntimeError("Bot returned no response")

        # Final parse catch anything missed
        await self._check_live_results(collected, seen_found, seen_failed, live_found, live_failed)
        duration = time.monotonic() - start

        return {
            "result_text": full_text,
            "found": live_found,
            "failed": live_failed,
            "duration": round(duration, 2),
            "error": "",
        }

    async def forward_results(
        self,
        results: list[ParsedResult | dict],
        target_group: str,
    ) -> int:
        """Forward any results not already sent live."""
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
            await self._emit("forwarded", {"session_id": self.session_id, "cc": result.cc, "result": result.to_dict()})
            await asyncio.sleep(1)

        return forwarded
