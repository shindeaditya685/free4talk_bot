"""Telegram control bot for managing Free4Talk room bots."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib import error, request

logger = logging.getLogger("telegram_bot")


@dataclass
class TelegramCallbacks:
    list_bots: Callable[[], Awaitable[list[dict[str, Any]]]]
    create_bot: Callable[[str, str, bool], Awaitable[dict[str, Any]]]
    start_bot: Callable[[str], Awaitable[dict[str, Any]]]
    stop_bot: Callable[[str], Awaitable[dict[str, Any]]]
    delete_bot: Callable[[str], Awaitable[str]]


class TelegramControlBot:
    def __init__(
        self,
        token: str,
        callbacks: TelegramCallbacks,
        allowed_chat_ids: set[int] | None = None,
        public_base_url: str = "",
    ) -> None:
        self.token = token.strip()
        self.callbacks = callbacks
        self.allowed_chat_ids = allowed_chat_ids or set()
        self.public_base_url = public_base_url.rstrip("/")
        self._offset = 0
        self._task: asyncio.Task | None = None
        self._running = False
        self._known_chat_ids: set[int] = set(allowed_chat_ids or set())

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def start(self) -> None:
        if not self.enabled or self._running:
            return

        self._running = True
        await self._safe_api_call("deleteWebhook", {"drop_pending_updates": False})
        await self._safe_api_call(
            "setMyCommands",
            {
                "commands": [
                    {"command": "start", "description": "Show help"},
                    {"command": "bots", "description": "List all bots"},
                    {"command": "create", "description": "Create: /create Name | URL"},
                    {"command": "run", "description": "Start: /run <id>"},
                    {"command": "stop", "description": "Stop: /stop <id>"},
                    {"command": "status", "description": "Status: /status <id>"},
                    {"command": "viewer", "description": "Viewer link: /viewer <id>"},
                    {"command": "delete", "description": "Delete: /delete <id>"},
                    {"command": "dashboard", "description": "Dashboard link"},
                    {"command": "whoami", "description": "Show your Telegram chat id"},
                ]
            },
        )
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram bot enabled%s", self._allowed_suffix())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            finally:
                self._task = None

    def _allowed_suffix(self) -> str:
        if not self.allowed_chat_ids:
            return " (all chats allowed)"
        return f" (allowed chats: {', '.join(str(chat_id) for chat_id in sorted(self.allowed_chat_ids))})"

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def _api_call_sync(self, method: str, payload: dict[str, Any]) -> Any:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._api_url(method),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=35) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Telegram API {method} failed: HTTP {exc.code} {body}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"Telegram API {method} failed: {exc.reason}") from exc

        parsed = json.loads(raw)
        if not parsed.get("ok"):
            raise RuntimeError(f"Telegram API {method} failed: {parsed}")
        return parsed.get("result")

    async def _api_call(self, method: str, payload: dict[str, Any]) -> Any:
        return await asyncio.to_thread(self._api_call_sync, method, payload)

    async def _safe_api_call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            return await self._api_call(method, payload)
        except Exception as exc:
            logger.warning("telegram %s failed: %s", method, exc)
            return None

    async def _send_message(self, chat_id: int, text: str) -> None:
        await self._safe_api_call(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
        )

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                updates = await self._api_call(
                    "getUpdates",
                    {
                        "offset": self._offset,
                        "timeout": 20,
                        "allowed_updates": ["message"],
                    },
                )
                for update in updates or []:
                    self._offset = max(self._offset, int(update["update_id"]) + 1)
                    await self._handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("telegram poll loop error: %s", exc)
                await asyncio.sleep(3)

    async def _handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()

        if not chat_id or not text:
            return

        self._known_chat_ids.add(int(chat_id))

        if not self._is_allowed_chat(int(chat_id)):
            await self._send_message(
                int(chat_id),
                "Access denied.\n"
                f"Your Telegram chat id is {chat_id}.\n"
                "Add it to TELEGRAM_ALLOWED_CHAT_IDS to authorize this chat.",
            )
            return

        reply = await self._handle_command(int(chat_id), text)
        if reply:
            await self._send_message(int(chat_id), reply)

    def _is_allowed_chat(self, chat_id: int) -> bool:
        return not self.allowed_chat_ids or chat_id in self.allowed_chat_ids

    async def send_alert(self, text: str) -> None:
        chat_ids = self.allowed_chat_ids or self._known_chat_ids
        if not chat_ids:
            logger.info("telegram alert skipped because no chat ids are available")
            return

        for chat_id in sorted(chat_ids):
            await self._send_message(chat_id, text)

    async def notify_crash(
        self,
        bot_id: str,
        nickname: str,
        room_url: str,
        message: str,
        recovery_failed: bool = False,
    ) -> None:
        headline = "Free4Talk bot crash alert"
        if recovery_failed:
            headline = "Free4Talk bot recovery failed"

        lines = [
            headline,
            "",
            f"Bot: {nickname} [{bot_id[:8]}]",
            f"Room: {room_url}",
            f"Message: {message}",
        ]

        if self.public_base_url:
            lines.extend(
                [
                    f"Dashboard: {self.public_base_url}/bots/{bot_id}",
                    f"Viewer: {self.public_base_url}/api/bots/{bot_id}/viewer",
                ]
            )

        await self.send_alert("\n".join(lines))

    async def _handle_command(self, chat_id: int, text: str) -> str:
        command, _, remainder = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        remainder = remainder.strip()

        if command in {"/start", "/help"}:
            return self._help_text()
        if command == "/whoami":
            return f"Your Telegram chat id is {chat_id}."
        if command == "/dashboard":
            return self._dashboard_text()
        if command == "/bots":
            return await self._list_bots_text()
        if command == "/create":
            return await self._create_bot_text(remainder)
        if command in {"/run", "/startbot"}:
            return await self._change_bot_state(remainder, action="start")
        if command == "/stop":
            return await self._change_bot_state(remainder, action="stop")
        if command == "/status":
            return await self._status_text(remainder)
        if command == "/viewer":
            return await self._viewer_text(remainder)
        if command == "/delete":
            return await self._delete_bot_text(remainder)
        return (
            "Unknown command.\n"
            "Use /help to see the Telegram controls for your Free4Talk dashboard."
        )

    def _help_text(self) -> str:
        lines = [
            "Free4Talk control bot",
            "",
            "/bots - list bots",
            "/create Name | https://www.free4talk.com/room/...",
            "/run <id-or-prefix> - start a bot",
            "/stop <id-or-prefix> - stop a bot",
            "/status <id-or-prefix> - show status",
            "/viewer <id-or-prefix> - send viewer links",
            "/delete <id-or-prefix> - delete a bot",
            "/dashboard - open the site dashboard",
            "/whoami - show your Telegram chat id",
            "",
            "Google sign-in still happens once in the web viewer. After that, the bot reuses the saved session.",
        ]
        return "\n".join(lines)

    def _dashboard_text(self) -> str:
        if self.public_base_url:
            return f"Dashboard: {self.public_base_url}/"
        return "Set PUBLIC_BASE_URL in the backend env to receive dashboard links here."

    async def _list_bots_text(self) -> str:
        bots = await self.callbacks.list_bots()
        if not bots:
            return "No bots created yet.\nUse /create Name | https://www.free4talk.com/room/..."

        lines = []
        for bot in bots[:25]:
            bot_id = bot["id"]
            short_id = bot_id[:8]
            status = str(bot.get("status", "unknown")).replace("_", " ")
            nickname = bot.get("nickname", short_id)
            room_url = bot.get("room_url", "")
            last_message = bot.get("last_message", "")
            lines.append(f"{nickname} [{short_id}]")
            lines.append(f"status: {status}")
            if room_url:
                lines.append(room_url)
            if last_message:
                lines.append(last_message)
            lines.append("")

        return "\n".join(lines).strip()

    async def _create_bot_text(self, remainder: str) -> str:
        nickname, room_url = self._split_create_args(remainder)
        if not nickname or not room_url:
            return (
                "Usage:\n"
                "/create Study Room | https://www.free4talk.com/room/vc666?key=694049"
            )

        try:
            bot = await self.callbacks.create_bot(nickname, room_url, True)
        except Exception as exc:
            return f"Create failed: {exc}"

        return (
            f"Created bot {bot['nickname']}.\n"
            f"id: {bot['id'][:8]}\n"
            f"Use /run {bot['id'][:8]} to start it."
        )

    def _split_create_args(self, remainder: str) -> tuple[str, str]:
        if "|" not in remainder:
            return "", ""
        nickname, room_url = remainder.split("|", 1)
        return nickname.strip(), room_url.strip()

    async def _change_bot_state(self, selector: str, action: str) -> str:
        bot = await self._resolve_bot(selector)
        if not bot:
            return "Bot not found. Use /bots to see available ids."

        short_id = bot["id"][:8]
        try:
            if action == "start":
                started = await self.callbacks.start_bot(bot["id"])
                return (
                    f"Starting {started['nickname']} [{short_id}].\n"
                    f"Status: {started.get('status', 'starting')}\n"
                    f"{started.get('last_message', '')}".strip()
                )

            stopped = await self.callbacks.stop_bot(bot["id"])
            return (
                f"Stopped {stopped['nickname']} [{short_id}].\n"
                f"{stopped.get('last_message', 'Stopped')}"
            )
        except Exception as exc:
            return f"{action.title()} failed for [{short_id}]: {exc}"

    async def _status_text(self, selector: str) -> str:
        bot = await self._resolve_bot(selector)
        if not bot:
            return "Bot not found. Use /bots to see available ids."

        lines = [
            f"{bot['nickname']} [{bot['id'][:8]}]",
            f"status: {str(bot.get('status', 'unknown')).replace('_', ' ')}",
            f"logged in: {'yes' if bot.get('logged_in') else 'no'}",
        ]
        if bot.get("last_message"):
            lines.append(f"message: {bot['last_message']}")
        if bot.get("room_url"):
            lines.append(bot["room_url"])
        return "\n".join(lines)

    async def _viewer_text(self, selector: str) -> str:
        bot = await self._resolve_bot(selector)
        if not bot:
            return "Bot not found. Use /bots to see available ids."

        if not self.public_base_url:
            return (
                f"Viewer for {bot['nickname']} [{bot['id'][:8]}] is available in the site dashboard.\n"
                "Set PUBLIC_BASE_URL in the backend env to receive direct links here."
            )

        return "\n".join(
            [
                f"{bot['nickname']} [{bot['id'][:8]}]",
                f"Dashboard page: {self.public_base_url}/bots/{bot['id']}",
                f"Direct viewer: {self.public_base_url}/api/bots/{bot['id']}/viewer",
            ]
        )

    async def _delete_bot_text(self, selector: str) -> str:
        bot = await self._resolve_bot(selector)
        if not bot:
            return "Bot not found. Use /bots to see available ids."

        try:
            deleted_id = await self.callbacks.delete_bot(bot["id"])
        except Exception as exc:
            return f"Delete failed for [{bot['id'][:8]}]: {exc}"

        return f"Deleted {bot['nickname']} [{deleted_id[:8]}]."

    async def _resolve_bot(self, selector: str) -> dict[str, Any] | None:
        target = selector.strip().lower()
        if not target:
            return None

        bots = await self.callbacks.list_bots()
        exact = None
        prefix_matches: list[dict[str, Any]] = []
        nickname_matches: list[dict[str, Any]] = []

        for bot in bots:
            bot_id = str(bot.get("id", ""))
            nickname = str(bot.get("nickname", ""))
            if bot_id.lower() == target:
                exact = bot
                break
            if bot_id.lower().startswith(target):
                prefix_matches.append(bot)
            if nickname.lower() == target:
                nickname_matches.append(bot)

        if exact:
            return exact
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        if len(nickname_matches) == 1:
            return nickname_matches[0]
        return None
