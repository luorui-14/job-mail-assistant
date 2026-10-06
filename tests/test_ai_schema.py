import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from job_mail_assistant.ai_parser import AIParseError, AIParser, _strict_json_schema
from job_mail_assistant.deadlines import SHANGHAI, resolve_time
from job_mail_assistant.models import MailMessage, ParsedEmail, TimeExpression


def test_structured_output_schema_requires_all_object_properties() -> None:
    schema = _strict_json_schema(ParsedEmail.model_json_schema())
    assert set(schema["required"]) == set(schema["properties"])
    time_schema = schema["$defs"]["TimeExpression"]
    assert set(time_schema["required"]) == set(time_schema["properties"])
    assert "default" not in time_schema["properties"]["year"]


def test_job_application_deadline_is_supported(monkeypatch) -> None:
    parser = AIParser("key", "https://ai.example.com/v1", "model")
    content = """{
        "classification": "action",
        "company": "示例科技公司",
        "position": "产品策略经理（增长方向）",
        "item_type": "投递",
        "time_type": "deadline",
        "original_time_text": "截止时间：2026-10-10",
        "time_expression": {"kind": "absolute", "year": 2026, "month": 10,
            "day": 10, "hour": null, "minute": null, "relative_value": null,
            "relative_unit": null, "week_offset": null, "weekday": null},
        "end_time_expression": null,
        "action_url_index": 0,
        "needs_confirmation": false,
        "confirmation_reason": null,
        "progress_summary": null
    }"""
    monkeypatch.setattr(parser, "_request", lambda *_: content)
    mail = MailMessage(
        uid="1",
        message_id="application@example.com",
        fingerprint="f" * 64,
        subject="校园招聘岗位推荐",
        sender="recruiting@example.com",
        received_at=datetime(2026, 9, 16, 8, tzinfo=SHANGHAI),
        body="截止时间：2026-10-10。立即投递。",
        urls=["https://jobs.example.com/apply"],
    )

    parsed = parser.parse(mail)
    resolved = resolve_time(parsed, mail.received_at)

    assert parsed.item_type == "投递"
    assert resolved.start == datetime(2026, 10, 10, 23, 59, tzinfo=SHANGHAI)
    assert resolved.inferred
    assert not resolved.needs_confirmation


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


def test_missing_position_does_not_require_confirmation(monkeypatch) -> None:
    parser = AIParser("key", "https://ai.example.com/v1", "model")
    content = """{
        "classification": "action",
        "company": "快手",
        "position": null,
        "item_type": "测评",
        "time_type": "deadline",
        "original_time_text": "7个工作日之内",
        "time_expression": {"kind": "relative", "year": null, "month": null,
            "day": null, "hour": null, "minute": null, "relative_value": 7,
            "relative_unit": "workday", "week_offset": null, "weekday": null},
        "end_time_expression": null,
        "action_url_index": null,
        "needs_confirmation": true,
        "confirmation_reason": "岗位名称无法确定",
        "progress_summary": null
    }"""
    monkeypatch.setattr(parser, "_request", lambda *_: content)
    mail = MailMessage(
        uid="1",
        message_id="kuaishou@example.com",
        fingerprint="f" * 64,
        subject="在线测评邀请",
        sender="hr@example.com",
        received_at=datetime(2026, 8, 28, 12, 19, tzinfo=SHANGHAI),
        body="请在7个工作日之内完成测评",
        urls=[],
    )

    parsed = parser.parse(mail)

    assert parsed.position is None
    assert not parsed.needs_confirmation
    assert parsed.confirmation_reason is None


def test_relative_duration_uses_mail_received_time_as_implicit_anchor(monkeypatch) -> None:
    parser = AIParser("key", "https://ai.example.com/v1", "model")
    content = """{
        "classification": "action",
        "company": "TCL实业",
        "position": null,
        "item_type": "AI面试",
        "time_type": "deadline",
        "original_time_text": "链接有效期：7天",
        "time_expression": {"kind": "relative", "year": null, "month": null,
            "day": null, "hour": null, "minute": null, "relative_value": 7,
            "relative_unit": "day", "week_offset": null, "weekday": null},
        "end_time_expression": null,
        "action_url_index": 0,
        "needs_confirmation": true,
        "confirmation_reason": "无法确定链接有效期截止日期，仅知有效期为7天，未给出起始日期",
        "progress_summary": null
    }"""
    monkeypatch.setattr(parser, "_request", lambda *_: content)
    mail = MailMessage(
        uid="1",
        message_id="tcl@example.com",
        fingerprint="f" * 64,
        subject="AI面试邀请",
        sender="hr@example.com",
        received_at=datetime(2026, 9, 8, 18, 14, tzinfo=SHANGHAI),
        body="链接有效期：7天",
        urls=["https://assessment.example.com/start"],
    )

    parsed = parser.parse(mail)
    resolved = resolve_time(parsed, mail.received_at)

    assert not parsed.needs_confirmation
    assert parsed.confirmation_reason is None
    assert resolved.start == datetime(2026, 9, 15, 18, 14, tzinfo=SHANGHAI)
    assert not resolved.needs_confirmation


def test_relative_anchor_cleanup_preserves_other_material_uncertainty(monkeypatch) -> None:
    parser = AIParser("key", "https://ai.example.com/v1", "model")
    content = """{
        "classification": "action",
        "company": "TCL实业",
        "position": null,
        "item_type": "AI面试",
        "time_type": "deadline",
        "original_time_text": "链接有效期：7天",
        "time_expression": {"kind": "relative", "year": null, "month": null,
            "day": null, "hour": null, "minute": null, "relative_value": 7,
            "relative_unit": "day", "week_offset": null, "weekday": null},
        "end_time_expression": null,
        "action_url_index": null,
        "needs_confirmation": true,
        "confirmation_reason": "未给出起始日期，且候选链接存在冲突",
        "progress_summary": null
    }"""
    monkeypatch.setattr(parser, "_request", lambda *_: content)
    mail = MailMessage(
        uid="1",
        message_id="tcl@example.com",
        fingerprint="f" * 64,
        subject="AI面试邀请",
        sender="hr@example.com",
        received_at=datetime(2026, 9, 8, 18, 14, tzinfo=SHANGHAI),
        body="链接有效期：7天",
        urls=["https://one.example.com", "https://two.example.com"],
    )

    parsed = parser.parse(mail)

    assert parsed.needs_confirmation
    assert parsed.confirmation_reason == "未给出起始日期，且候选链接存在冲突"


def test_explicit_assessment_expiry_clears_time_only_ai_warning(monkeypatch) -> None:
    parser = AIParser("key", "https://ai.example.com/v1", "model")
    content = ParsedEmail(
        classification="action",
        company="示例公司",
        item_type="测评",
        time_type="deadline",
        original_time_text="邀请于 2026年09月18日 周五 16:32 生效",
        needs_confirmation=True,
        confirmation_reason="无法确定截止时间",
    ).model_dump_json()
    monkeypatch.setattr(parser, "_request", lambda *_: content)
    mail = MailMessage(
        uid="1",
        message_id="assessment@example.com",
        fingerprint="f" * 64,
        subject="在线测评邀请",
        sender="hr@example.com",
        received_at=datetime(2026, 9, 18, 17, tzinfo=SHANGHAI),
        body="2026年09月18日 周五 16:32 生效，于 2026年09月25日 周五 16:32 失效",
        urls=["https://example.com/start"],
    )

    parsed = parser.parse(mail)
    resolved = resolve_time(parsed, mail.received_at, body=mail.body)

    assert not parsed.needs_confirmation
    assert resolved.start == datetime(2026, 9, 25, 16, 32, tzinfo=SHANGHAI)


def assessment_mail() -> MailMessage:
    return MailMessage(
        uid="2",
        message_id="assessment@example.com",
        fingerprint="a" * 64,
        subject="示例企业校园招聘测评通知",
        sender="recruiting@example.com",
        received_at=datetime(2026, 10, 5, 14, 32, tzinfo=SHANGHAI),
        body="请使用测评入口完成测试。链接有效期为72小时。客服提供24小时咨询服务。",
        urls=[
            "https://assessment.example.com/start",
            "https://jobs.example.com/progress",
            "https://devices.example.com/check",
        ],
    )


def valid_assessment_json() -> str:
    return ParsedEmail(
        classification="action",
        company="示例企业",
        item_type="测评",
        time_type="deadline",
        original_time_text="链接有效期为72小时",
        time_expression=TimeExpression(kind="relative", relative_value=72, relative_unit="hour"),
        action_url_index=0,
    ).model_dump_json()


@pytest.mark.parametrize("invalid", [
    '{"classification": "action", "time_expression": {"kind": "relative"}}',
    valid_assessment_json().replace('"relative_unit":"hour"', '"relative_unit":"hours"'),
    '{"classification":',
    '[]',
])
def test_invalid_output_is_retried_before_resolving_assessment(monkeypatch, invalid) -> None:
    parser = AIParser("key", "https://ai.example.com/v1", "model")
    calls = []
    responses = iter([invalid, valid_assessment_json()])

    def request(mail, response_format, **kwargs):
        calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(parser, "_request", request)
    mail = assessment_mail()
    parsed = parser.parse(mail)
    resolved = resolve_time(parsed, mail.received_at, body=mail.body)

    assert len(calls) == 2
    assert calls[1]["validation_feedback"]
    assert parsed.action_url_index == 0
    assert resolved.start == datetime(2026, 10, 8, 14, 32, tzinfo=SHANGHAI)
    assert not resolved.needs_confirmation


def test_schema_retries_are_bounded_and_do_not_expose_model_output(monkeypatch, caplog) -> None:
    parser = AIParser("key", "https://ai.example.com/v1", "model")
    calls = []
    invalid = '{"classification":"private model text", "private@example.com":"secret"}'

    def request(*args, **kwargs):
        calls.append(kwargs)
        return invalid

    monkeypatch.setattr(parser, "_request", request)
    with pytest.raises(AIParseError, match="after retries") as caught:
        parser.parse(assessment_mail())

    assert len(calls) == 3
    assert "classification: literal_error" in str(caught.value)
    for value in (str(caught.value), caplog.text, str(calls)):
        assert "private model text" not in value
        assert "private@example.com" not in value
        assert "secret" not in value


def test_json_object_fallback_receives_complete_schema(monkeypatch) -> None:
    parser = AIParser("key", "https://ai.example.com/v1", "model")
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        if kwargs["response_format"]["type"] == "json_schema":
            raise BadRequestError(
                "Unsupported response format",
                response=httpx.Response(400, request=httpx.Request("POST", "https://example.com")),
                body=None,
            )
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=valid_assessment_json())
        )])

    monkeypatch.setattr(parser.client.chat.completions, "create", create)
    parsed = parser.parse(assessment_mail())

    assert len(requests) == 2
    assert requests[1]["response_format"] == {"type": "json_object"}
    prompt = requests[1]["messages"][0]["content"]
    schema = json.loads(prompt.split("完整 JSON Schema：\n", 1)[1])
    assert set(schema["required"]) == set(ParsedEmail.model_fields)
    assert "relative_value" in schema["$defs"]["TimeExpression"]["required"]
    assert parsed.time_expression.relative_value == 72
