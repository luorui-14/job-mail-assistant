from datetime import datetime

import httpx
import pytest

from job_mail_assistant.deadlines import SHANGHAI
from job_mail_assistant.feishu import (
    STATE_SCHEMA_VERSION,
    FeishuClient,
    FeishuError,
    datetime_to_millis,
)
from job_mail_assistant.models import BaseRecord


def test_http_error_exposes_only_safe_feishu_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"code": 1254003, "msg": "WrongRequest"},
            headers={"X-Tt-Logid": "safe-request-id"},
            request=request,
        )

    client = FeishuClient("cli_test", "super-secret")
    client.http.close()
    client.http = httpx.Client(
        base_url="https://open.feishu.cn",
        transport=httpx.MockTransport(handler),
    )
    client._token = "tenant-secret-token"

    with pytest.raises(FeishuError) as error:
        client.list_tables("base-token")

    message = str(error.value)
    assert "http=400" in message
    assert "code=1254003" in message
    assert "msg=WrongRequest" in message
    assert "request_id=safe-request-id" in message
    assert "super-secret" not in message
    assert "tenant-secret-token" not in message
    client.close()


def test_cursor_exposes_state_schema_version() -> None:
    when = datetime(2026, 9, 16, 8, tzinfo=SHANGHAI)
    client = FeishuClient.__new__(FeishuClient)
    client.list_records = lambda *_: [  # type: ignore[method-assign]
        BaseRecord(
            "rec-state",
            {
                "状态键": "main",
                "上次完整成功时间": datetime_to_millis(when),
                "Schema 版本": "1",
            },
        )
    ]

    assert client.get_cursor("base", "state", default=when) == (
        when,
        "rec-state",
        "1",
    )


def test_set_cursor_persists_current_schema_version() -> None:
    when = datetime(2026, 9, 16, 8, tzinfo=SHANGHAI)
    captured: dict[str, object] = {}
    client = FeishuClient.__new__(FeishuClient)

    def update_record(_base: str, _table: str, _record: str, fields: dict) -> BaseRecord:
        captured.update(fields)
        return BaseRecord("rec-state", fields)

    client.update_record = update_record  # type: ignore[method-assign]

    assert client.set_cursor("base", "state", when, "rec-state") == "rec-state"
    assert captured["Schema 版本"] == STATE_SCHEMA_VERSION
