from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ITEM_TYPES = (
    "测评",
    "笔试",
    "AI面试",
    "群面",
    "一面",
    "二面",
    "三面",
    "终面",
    "HR面",
    "其他面试",
)


class TimeExpression(BaseModel):
    """Components extracted by AI; all actual date arithmetic is done by code."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["absolute", "relative", "weekday", "none", "ambiguous"]
    year: int | None = None
    month: int | None = None
    day: int | None = None
    hour: int | None = None
    minute: int | None = None
    relative_value: int | None = None
    relative_unit: Literal["hour", "day", "workday"] | None = None
    week_offset: int | None = Field(default=None, ge=0, le=4)
    weekday: int | None = Field(default=None, ge=1, le=7)

    @model_validator(mode="after")
    def validate_shape(self) -> TimeExpression:
        if self.kind == "relative" and (
            self.relative_value is None or self.relative_unit is None
        ):
            raise ValueError("relative time requires relative_value and relative_unit")
        if self.kind == "weekday" and self.weekday is None:
            raise ValueError("weekday time requires weekday")
        return self


class ParsedEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Literal["action", "progress", "irrelevant"]
    company: str | None = None
    position: str | None = None
    item_type: Literal[
        "测评",
        "笔试",
        "AI面试",
        "群面",
        "一面",
        "二面",
        "三面",
        "终面",
        "HR面",
        "其他面试",
    ] | None = None
    time_type: Literal["deadline", "fixed", "none"] = "none"
    original_time_text: str | None = None
    time_expression: TimeExpression = Field(
        default_factory=lambda: TimeExpression(kind="none")
    )
    end_time_expression: TimeExpression | None = None
    action_url_index: int | None = Field(default=None, ge=0)
    needs_confirmation: bool = False
    confirmation_reason: str | None = None
    progress_summary: str | None = None


@dataclass(frozen=True)
class MailMessage:
    uid: str
    message_id: str | None
    fingerprint: str
    subject: str
    sender: str
    received_at: datetime
    body: str
    urls: list[str]
    references: set[str] = field(default_factory=set)

    @property
    def unique_id(self) -> str:
        return self.message_id or f"sha256:{self.fingerprint}"


@dataclass(frozen=True)
class ResolvedTime:
    start: datetime | None
    end: datetime | None
    inferred: bool
    needs_confirmation: bool
    reason: str | None = None


@dataclass
class BaseRecord:
    record_id: str
    fields: dict[str, object]

    def text(self, name: str) -> str:
        value = self.fields.get(name)
        if value is None:
            return ""
        if isinstance(value, dict):
            if value.get("link") is not None:
                return str(value["link"])
            if value.get("text") is not None:
                return str(value["text"])
        if isinstance(value, list):
            return "\n".join(str(item) for item in value)
        return str(value)


@dataclass
class RunStats:
    fetched: int = 0
    candidates: int = 0
    relevant: int = 0
    new_records: int = 0
    updated_records: int = 0
    duplicates: int = 0
    calendar_created: int = 0
    calendar_updated: int = 0
    needs_confirmation: int = 0
    progress_items: int = 0
    errors: list[str] = field(default_factory=list)
