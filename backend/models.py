"""Pydantic models for the Free4Talk bot service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BotStatus(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    WAITING_LOGIN = "waiting_login"
    JOINING = "joining"
    IN_ROOM = "in_room"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    STOPPED = "stopped"


class BotCreate(BaseModel):
    nickname: str = Field(..., min_length=1, max_length=60)
    room_url: str = Field(..., min_length=5)
    auto_start: bool = True


class BotUpdate(BaseModel):
    nickname: Optional[str] = None
    room_url: Optional[str] = None
    auto_start: Optional[bool] = None


class Bot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    nickname: str
    room_url: str
    status: BotStatus = BotStatus.IDLE
    last_message: str = ""
    display_num: Optional[int] = None
    vnc_port: Optional[int] = None
    auto_start: bool = True
    logged_in: bool = False
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_document(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        status = normalized.get("status")

        if isinstance(status, str):
            stripped_status = status.strip()
            lowered_status = stripped_status.lower()

            if lowered_status in BotStatus._value2member_map_:
                normalized["status"] = lowered_status
            elif stripped_status.upper() in BotStatus.__members__:
                normalized["status"] = BotStatus[stripped_status.upper()].value

        for field_name in ("created_at", "updated_at"):
            value = normalized.get(field_name)
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                normalized[field_name] = value.astimezone(timezone.utc).isoformat()

        return normalized


class BotRuntimeInfo(BaseModel):
    id: str
    status: BotStatus
    last_message: str
    in_room: bool
    running: bool
    logged_in: bool
    vnc_available: bool
