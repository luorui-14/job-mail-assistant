from datetime import datetime
from email.message import EmailMessage

from job_mail_assistant.deadlines import SHANGHAI
from job_mail_assistant.mailbox import (
    filter_action_urls,
    looks_like_recruiting,
    normalize_message_id,
    parse_mail,
)


def test_normalize_message_id() -> None:
    assert normalize_message_id(" <ABC@Example.COM> ") == "abc@example.com"


def test_ordinary_mail_does_not_enter() -> None:
    assert not looks_like_recruiting("周末聚餐", "friend@example.com", "晚上见")
    assert looks_like_recruiting("在线测评邀请", "hr@example.com", "请完成测评")
    assert looks_like_recruiting("产品经理一面通知", "hr@example.com", "请准时参加")


def test_report_mail_is_excluded() -> None:
    assert not looks_like_recruiting("【秋招早报】2026-08-28 测评", "me@qq.com", "测评")


def test_url_filter_removes_tracking_and_images() -> None:
    urls = filter_action_urls(
        [
            "https://exam.example.com/start?id=1",
            "https://example.com/unsubscribe?id=2",
            "https://cdn.example.com/logo.png",
            "https://track.example.com/pixel?id=3",
            "https://exam.example.com/start?id=2&utm_source=mail",
        ]
    )
    assert urls == [
        "https://exam.example.com/start?id=1",
        "https://exam.example.com/start?id=2",
    ]


def test_parse_mail_uses_internaldate_and_fallback_fingerprint() -> None:
    message = EmailMessage()
    message["Subject"] = "在线测评邀请"
    message["From"] = "HR <hr@example.com>"
    message.set_content("请在收到邮件后72小时内完成：https://exam.example.com/start")
    meta = b'1 (UID 1 INTERNALDATE "27-Aug-2026 16:43:00 +0800" BODY[] {10})'
    parsed = parse_mail("1", meta, message.as_bytes())
    assert parsed.received_at == datetime(2026, 8, 27, 16, 43, tzinfo=SHANGHAI)
    assert parsed.message_id is None
    assert len(parsed.fingerprint) == 64
    assert parsed.urls == ["https://exam.example.com/start"]
