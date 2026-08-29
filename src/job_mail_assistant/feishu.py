from __future__ import annotations

import time
from datetime import datetime
from urllib.parse import quote, urlparse

import httpx

from .deadlines import SHANGHAI
from .models import BaseRecord

MAIN_REQUIRED_FIELDS = {
    "已完成",
    "公司",
    "岗位",
    "类型",
    "邮件接收时间",
    "截止/面试时间",
    "链接",
    "Message-ID",
    "邮件指纹",
    "原邮件主题",
    "原始时间描述",
    "时间为推算",
    "需要人工确认",
    "确认说明",
    "Calendar Event ID",
    "Calendar 状态",
    "Calendar 错误",
    "最后处理时间",
    "时间类型",
    "结束时间",
}
STATE_REQUIRED_FIELDS = {"状态键", "上次完整成功时间", "Schema 版本"}
STATE_TABLE_NAME = "JMA_运行状态（请勿手动编辑）"


class FeishuError(RuntimeError):
    pass


def datetime_to_millis(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return int(value.timestamp() * 1000)


def value_to_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=SHANGHAI)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(SHANGHAI)
        except ValueError:
            return None
    return None


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.http = httpx.Client(base_url="https://open.feishu.cn", timeout=30.0)
        self._token: str | None = None

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> FeishuClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _authenticate(self) -> None:
        response = self.http.post(
            "/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0 or not data.get("tenant_access_token"):
            raise FeishuError(f"Feishu authentication failed: code={data.get('code')}")
        self._token = data["tenant_access_token"]

    def _request(
        self, method: str, path: str, *, params: dict | None = None, json: dict | None = None
    ) -> dict:
        if not self._token:
            self._authenticate()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.http.request(
                    method,
                    path,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("code") != 0:
                    code = payload.get("code")
                    message = payload.get("msg", "")
                    raise FeishuError(
                        f"Feishu API failed: code={code} msg={message}"
                    )
                return payload.get("data") or {}
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                last_error = exc
                retryable = not isinstance(
                    exc, httpx.HTTPStatusError
                ) or exc.response.status_code in {408, 429, 500, 502, 503, 504}
                if not retryable or attempt == 2:
                    if isinstance(exc, httpx.HTTPStatusError):
                        try:
                            error_payload = exc.response.json()
                        except ValueError:
                            error_payload = {}
                        code = error_payload.get("code", "unknown")
                        message = str(error_payload.get("msg", "unknown"))[:200].replace(
                            "\n", " "
                        )
                        request_id = exc.response.headers.get("X-Tt-Logid", "unknown")
                        raise FeishuError(
                            "Feishu request failed: "
                            f"http={exc.response.status_code} code={code} "
                            f"msg={message} request_id={request_id}"
                        ) from exc
                    raise FeishuError(f"Feishu request failed: {type(exc).__name__}") from exc
                time.sleep(2**attempt)
        raise FeishuError(f"Feishu request failed: {type(last_error).__name__}")

    def resolve_wiki(self, wiki_url: str) -> str:
        token = urlparse(wiki_url).path.rstrip("/").split("/")[-1]
        if not token:
            raise FeishuError("Invalid Feishu Wiki URL")
        data = self._request(
            "GET", "/open-apis/wiki/v2/spaces/get_node", params={"token": token}
        )
        node = data.get("node") or {}
        if node.get("obj_type") != "bitable" or not node.get("obj_token"):
            raise FeishuError("Wiki node is not a Base/bitable resource")
        return str(node["obj_token"])

    def _paged_items(self, path: str, *, page_size: int = 100) -> list[dict]:
        items: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict[str, object] = {"page_size": page_size}
            if page_token:
                params["page_token"] = page_token
            data = self._request("GET", path, params=params)
            items.extend(data.get("items") or [])
            if not data.get("has_more"):
                return items
            page_token = data.get("page_token")
            if not page_token:
                raise FeishuError("Feishu pagination indicated more data without a page token")

    def list_tables(self, base_token: str) -> list[dict]:
        return self._paged_items(
            f"/open-apis/bitable/v1/apps/{quote(base_token)}/tables", page_size=100
        )

    def find_table(self, base_token: str, name: str) -> str:
        matches = [table for table in self.list_tables(base_token) if table.get("name") == name]
        if len(matches) != 1:
            raise FeishuError(f"Expected exactly one table named {name!r}, found {len(matches)}")
        return str(matches[0]["table_id"])

    def list_fields(self, base_token: str, table_id: str) -> list[dict]:
        return self._paged_items(
            f"/open-apis/bitable/v1/apps/{quote(base_token)}/tables/{quote(table_id)}/fields",
            page_size=100,
        )

    def validate_schema(
        self, base_token: str, main_table_id: str, state_table_id: str
    ) -> None:
        main_names = {
            str(field.get("field_name"))
            for field in self.list_fields(base_token, main_table_id)
        }
        state_names = {
            str(field.get("field_name")) for field in self.list_fields(base_token, state_table_id)
        }
        missing_main = sorted(MAIN_REQUIRED_FIELDS - main_names)
        missing_state = sorted(STATE_REQUIRED_FIELDS - state_names)
        if missing_main or missing_state:
            details = []
            if missing_main:
                details.append("main=" + ",".join(missing_main))
            if missing_state:
                details.append("state=" + ",".join(missing_state))
            raise FeishuError("Feishu schema is not provisioned: " + "; ".join(details))

    def list_records(self, base_token: str, table_id: str) -> list[BaseRecord]:
        items = self._paged_items(
            f"/open-apis/bitable/v1/apps/{quote(base_token)}/tables/{quote(table_id)}/records",
            page_size=500,
        )
        return [
            BaseRecord(record_id=str(item["record_id"]), fields=item.get("fields") or {})
            for item in items
        ]

    def create_record(self, base_token: str, table_id: str, fields: dict) -> BaseRecord:
        data = self._request(
            "POST",
            f"/open-apis/bitable/v1/apps/{quote(base_token)}/tables/{quote(table_id)}/records",
            json={"fields": fields},
        )
        record = data.get("record") or {}
        if not record.get("record_id"):
            raise FeishuError("Feishu create record response had no record_id")
        return BaseRecord(str(record["record_id"]), record.get("fields") or fields)

    def update_record(
        self, base_token: str, table_id: str, record_id: str, fields: dict
    ) -> BaseRecord:
        data = self._request(
            "PUT",
            f"/open-apis/bitable/v1/apps/{quote(base_token)}/tables/{quote(table_id)}/records/{quote(record_id)}",
            json={"fields": fields},
        )
        record = data.get("record") or {}
        return BaseRecord(str(record.get("record_id") or record_id), record.get("fields") or fields)

    def get_cursor(
        self, base_token: str, state_table_id: str, *, default: datetime
    ) -> tuple[datetime, str | None]:
        records = self.list_records(base_token, state_table_id)
        state = next((record for record in records if record.text("状态键") == "main"), None)
        if not state:
            return default, None
        return value_to_datetime(state.fields.get("上次完整成功时间")) or default, state.record_id

    def set_cursor(
        self,
        base_token: str,
        state_table_id: str,
        value: datetime,
        record_id: str | None,
    ) -> str:
        fields = {
            "状态键": "main",
            "上次完整成功时间": datetime_to_millis(value),
            "Schema 版本": "1",
        }
        if record_id:
            return self.update_record(
                base_token, state_table_id, record_id, fields
            ).record_id
        return self.create_record(base_token, state_table_id, fields).record_id
