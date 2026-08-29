from job_mail_assistant.config import Config

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
