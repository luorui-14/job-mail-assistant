from datetime import datetime

from job_mail_assistant.ai_parser import AIParser, _strict_json_schema
from job_mail_assistant.deadlines import SHANGHAI
from job_mail_assistant.models import MailMessage, ParsedEmail


def test_structured_output_schema_requires_all_object_properties() -> None:
    schema = _strict_json_schema(ParsedEmail.model_json_schema())
    assert set(schema["required"]) == set(schema["properties"])
    time_schema = schema["$defs"]["TimeExpression"]
    assert set(time_schema["required"]) == set(time_schema["properties"])
    assert "default" not in time_schema["properties"]["year"]


def test_only_candidate_url_repairs_link_only_ai_uncertainty(monkeypatch) -> None:
    parser = AIParser("key", "https://ai.example.com/v1", "model")
    content = """{
        "classification": "action",
        "company": "网易互娱",
        "position": "产品经理",
        "item_type": "笔试",
        "time_type": "fixed",
        "original_time_text": "2026-08-30 10:00:00 -- 11:00:00",
        "time_expression": {"kind": "absolute", "year": 2026, "month": null,
            "day": null, "hour": 10, "minute": 0, "relative_value": null,
            "relative_unit": null, "week_offset": null, "weekday": null},
        "end_time_expression": null,
        "action_url_index": null,
        "needs_confirmation": true,
        "confirmation_reason": "候选链接[0]可能是确认入口，但无法可靠确定",
        "progress_summary": null
    }"""
    monkeypatch.setattr(parser, "_request", lambda *_: content)
    mail = MailMessage(
        uid="1",
        message_id="netease@example.com",
        fingerprint="f" * 64,
        subject="在线笔试确认",
        sender="hr@example.com",
        received_at=datetime(2026, 8, 28, 15, 42, tzinfo=SHANGHAI),
        body="请确认参加",
        urls=["https://exam.example.com/confirm"],
    )

    parsed = parser.parse(mail)

    assert parsed.action_url_index == 0
    assert not parsed.needs_confirmation
    assert parsed.confirmation_reason is None
