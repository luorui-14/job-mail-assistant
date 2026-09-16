import httpx
import pytest

from job_mail_assistant.feishu import FeishuClient, FeishuError


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
