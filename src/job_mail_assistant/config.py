from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    qq_email: str
    qq_auth_code: str
    feishu_app_id: str
    feishu_app_secret: str
    feishu_wiki_url: str
    icloud_username: str
    icloud_app_password: str
    ai_api_key: str
    ai_base_url: str
    ai_model: str
    icloud_calendar_name: str = "秋招"
    feishu_table_name: str = "测评&面试"
    scan_days: int = 2

    @classmethod
    def from_env(cls) -> Config:
        required = {
            name: (os.getenv(name) or "").strip()
            for name in (
                "QQ_EMAIL",
                "QQ_AUTH_CODE",
                "FEISHU_APP_ID",
                "FEISHU_APP_SECRET",
                "FEISHU_WIKI_URL",
                "ICLOUD_USERNAME",
                "ICLOUD_APP_PASSWORD",
                "AI_API_KEY",
                "AI_BASE_URL",
                "AI_MODEL",
            )
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigError("Missing required environment variables: " + ", ".join(missing))

        try:
            scan_days = int(os.getenv("SCAN_DAYS", "2"))
        except ValueError as exc:
            raise ConfigError("SCAN_DAYS must be an integer") from exc
        if not 1 <= scan_days <= 30:
            raise ConfigError("SCAN_DAYS must be between 1 and 30")

        return cls(
            qq_email=required["QQ_EMAIL"] or "",
            qq_auth_code=required["QQ_AUTH_CODE"] or "",
            feishu_app_id=required["FEISHU_APP_ID"] or "",
            feishu_app_secret=required["FEISHU_APP_SECRET"] or "",
            feishu_wiki_url=required["FEISHU_WIKI_URL"] or "",
            icloud_username=required["ICLOUD_USERNAME"] or "",
            icloud_app_password=required["ICLOUD_APP_PASSWORD"] or "",
            ai_api_key=required["AI_API_KEY"] or "",
            ai_base_url=(required["AI_BASE_URL"] or "").rstrip("/"),
            ai_model=required["AI_MODEL"] or "",
            icloud_calendar_name=(os.getenv("ICLOUD_CALENDAR_NAME") or "").strip()
            or "秋招",
            feishu_table_name=(os.getenv("FEISHU_TABLE_NAME") or "").strip()
            or "测评&面试",
            scan_days=scan_days,
        )
