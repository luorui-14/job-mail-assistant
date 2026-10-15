import pytest

from job_mail_assistant.config import Config, ConfigError

REQUIRED_ENV = {
    "QQ_EMAIL": "me@example.com",
    "QQ_AUTH_CODE": "qq-auth-code",
    "FEISHU_APP_ID": "cli_test",
    "FEISHU_APP_SECRET": "feishu-secret",
    "FEISHU_WIKI_URL": "https://example.feishu.cn/wiki/test-token",
    "ICLOUD_USERNAME": "apple@example.com",
    "ICLOUD_APP_PASSWORD": "apple-password",
    "AI_API_KEY": "ai-key",
    "AI_BASE_URL": "https://ai.example.com/v1/",
    "AI_MODEL": "test-model",
}


def test_optional_empty_environment_values_use_defaults(monkeypatch):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("ICLOUD_CALENDAR_NAME", "")
    monkeypatch.setenv("FEISHU_TABLE_NAME", "")

    config = Config.from_env()

    assert config.icloud_calendar_name == "秋招"
    assert config.feishu_wiki_url == "https://example.feishu.cn/wiki/test-token"
    assert config.feishu_table_name == "测评&面试"
    assert config.ai_base_url == "https://ai.example.com/v1"


def test_environment_values_are_trimmed(monkeypatch):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, f"  {value}  ")

    config = Config.from_env()

    assert config.icloud_username == "apple@example.com"
    assert config.icloud_app_password == "apple-password"
    assert config.ai_base_url == "https://ai.example.com/v1"


def test_default_scan_window_is_two_days(monkeypatch):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SCAN_DAYS", raising=False)

    assert Config.from_env().scan_days == 2


@pytest.mark.parametrize("value, expected", [
    (None, None), ("", None), ("  ", None), ("false", False), (" TRUE ", True),
])
def test_optional_thinking_mode_is_explicit(monkeypatch, value, expected):
    for name, required_value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, required_value)
    if value is None:
        monkeypatch.delenv("AI_ENABLE_THINKING", raising=False)
    else:
        monkeypatch.setenv("AI_ENABLE_THINKING", value)

    assert Config.from_env().ai_enable_thinking is expected


def test_invalid_thinking_mode_is_rejected_without_echoing_input(monkeypatch):
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("AI_ENABLE_THINKING", "private-value")

    with pytest.raises(ConfigError, match="AI_ENABLE_THINKING") as caught:
        Config.from_env()

    assert "private-value" not in str(caught.value)
