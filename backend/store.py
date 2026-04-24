"""Storage backends for bot metadata."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorClient


class BotStore(Protocol):
    async def list_bots(self) -> list[dict[str, Any]]: ...

    async def find_bot(self, bot_id: str) -> dict[str, Any] | None: ...

    async def save_bot(self, data: dict[str, Any]) -> None: ...

    async def delete_bot(self, bot_id: str) -> None: ...

    async def close(self) -> None: ...


class MongoBotStore:
    def __init__(self, mongo_url: str, db_name: str) -> None:
        self.client = AsyncIOMotorClient(mongo_url)
        self.collection = self.client[db_name].bots

    async def list_bots(self) -> list[dict[str, Any]]:
        return await self.collection.find({}, {"_id": 0}).to_list(500)

    async def find_bot(self, bot_id: str) -> dict[str, Any] | None:
        return await self.collection.find_one({"id": bot_id}, {"_id": 0})

    async def save_bot(self, data: dict[str, Any]) -> None:
        await self.collection.update_one({"id": data["id"]}, {"$set": data}, upsert=True)

    async def delete_bot(self, bot_id: str) -> None:
        await self.collection.delete_one({"id": bot_id})

    async def close(self) -> None:
        self.client.close()


class FileBotStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _read_bots_sync(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        raw = self.path.read_text(encoding="utf-8").strip()
        if not raw:
            return []

        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"Invalid bot store format in {self.path}")

        return [item for item in data if isinstance(item, dict)]

    def _write_bots_sync(self, records: list[dict[str, Any]]) -> None:
        payload = json.dumps(records, indent=2, sort_keys=True)
        self.path.write_text(f"{payload}\n", encoding="utf-8")

    async def list_bots(self) -> list[dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._read_bots_sync)

    async def find_bot(self, bot_id: str) -> dict[str, Any] | None:
        async with self._lock:
            records = await asyncio.to_thread(self._read_bots_sync)

        for record in records:
            if record.get("id") == bot_id:
                return record

        return None

    async def save_bot(self, data: dict[str, Any]) -> None:
        async with self._lock:
            records = await asyncio.to_thread(self._read_bots_sync)
            for index, record in enumerate(records):
                if record.get("id") == data["id"]:
                    records[index] = data
                    break
            else:
                records.append(data)

            await asyncio.to_thread(self._write_bots_sync, records)

    async def delete_bot(self, bot_id: str) -> None:
        async with self._lock:
            records = await asyncio.to_thread(self._read_bots_sync)
            next_records = [record for record in records if record.get("id") != bot_id]
            await asyncio.to_thread(self._write_bots_sync, next_records)

    async def close(self) -> None:
        return None


def create_bot_store(default_file_path: Path) -> tuple[BotStore, str]:
    mongo_url = os.environ.get("MONGO_URL", "").strip()
    if mongo_url:
        db_name = os.environ.get("DB_NAME", "free4talk")
        return MongoBotStore(mongo_url, db_name), f"mongo:{db_name}"

    file_path = Path(os.environ.get("BOT_STORE_PATH", default_file_path))
    return FileBotStore(file_path), f"file:{file_path}"
