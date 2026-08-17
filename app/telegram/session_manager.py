import asyncio
import logging
import re
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from app.config import settings

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages multiple Telethon client sessions."""

    def __init__(self) -> None:
        self._clients: dict[int | str, TelegramClient] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def _session_path(self, filename: str) -> Path:
        return settings.session_dir / filename

    async def connect(self, session_id: int, filename: str) -> TelegramClient:
        if session_id in self._clients:
            client = self._clients[session_id]
            if client.is_connected():
                return client

        if not settings.telegram_api_id or not settings.telegram_api_hash:
            raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set in .env")

        session_path = str(self._session_path(filename).with_suffix(""))
        client = TelegramClient(session_path, settings.telegram_api_id, settings.telegram_api_hash)
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError(f"Session {filename} is not authorized. Re-upload a valid .session file.")

        self._clients[session_id] = client
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return client

    def get_lock(self, session_id: int) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    async def disconnect(self, session_id: int) -> None:
        client = self._clients.pop(session_id, None)
        if client and client.is_connected():
            await client.disconnect()

    async def disconnect_all(self) -> None:
        for sid in list(self._clients.keys()):
            await self.disconnect(sid)

    async def get_me(self, session_id: int, filename: str) -> dict:
        client = await self.connect(session_id, filename)
        me = await client.get_me()
        return {
            "id": me.id,
            "username": me.username or "",
            "phone": me.phone or "",
            "first_name": me.first_name or "",
        }

    async def send_to_bot(self, session_id: int, filename: str, bot_username: str, message: str) -> int:
        client = await self.connect(session_id, filename)
        bot = bot_username.lstrip("@")
        entity = await client.get_entity(bot)
        sent = await client.send_message(entity, message)
        return sent.id

    async def send_to_group(self, session_id: int, filename: str, group: str, message: str) -> None:
        client = await self.connect(session_id, filename)
        target = group.lstrip("@")
        entity = await client.get_entity(target)
        await client.send_message(entity, message)

    async def send_login_code(self, name: str, phone: str) -> str:
        """Send OTP to phone. Returns session filename key."""
        settings.session_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^\w\-]", "_", name)
        filename = f"{safe_name}.session"
        session_path = str(self._session_path(filename).with_suffix(""))

        client = TelegramClient(session_path, settings.telegram_api_id, settings.telegram_api_hash)
        await client.connect()
        await client.send_code_request(phone)
        self._clients[f"pending:{safe_name}"] = client
        return filename

    async def verify_login_code(
        self,
        name: str,
        phone: str,
        code: str,
        password: str = "",
    ) -> tuple[str, dict]:
        """Complete phone login with OTP (and optional 2FA password)."""
        safe_name = re.sub(r"[^\w\-]", "_", name)
        pending_key = f"pending:{safe_name}"
        client = self._clients.get(pending_key)
        if not client:
            session_path = str(self._session_path(f"{safe_name}.session").with_suffix(""))
            client = TelegramClient(session_path, settings.telegram_api_id, settings.telegram_api_hash)
            await client.connect()

        try:
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                if not password:
                    raise RuntimeError("Two-factor authentication required. Provide your 2FA password.")
                await client.sign_in(password=password)

            me = await client.get_me()
            user_info = {
                "id": me.id,
                "username": me.username or "",
                "phone": me.phone or "",
                "first_name": me.first_name or "",
            }
            filename = f"{safe_name}.session"
            self._clients.pop(pending_key, None)
            await client.disconnect()
            return filename, user_info
        except Exception:
            await client.disconnect()
            raise


session_manager = SessionManager()
