from datetime import datetime
from email.message import EmailMessage

from job_mail_assistant.deadlines import SHANGHAI
from job_mail_assistant.mailbox import (
    filter_action_urls,
    is_non_action_recruiting_notice,
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


def test_job_application_with_explicit_deadline_enters() -> None:
    subject = "示例科技公司校园招聘岗位推荐"
    body = """【产品类】产品策略经理（增长方向）
    所属机构：示例科技公司
    截止时间：2026-10-10
    岗位职责：负责 AI 相关产品的全生命周期管理。
    立即投递
    """

    assert looks_like_recruiting(subject, "recruiting@example.com", body)


def test_job_recommendation_without_application_deadline_is_excluded() -> None:
    subject = "本周岗位推荐"
    body = "校园招聘职位上新，欢迎前往招聘官网查看并申请。"

    assert not looks_like_recruiting(subject, "recruiting@example.com", body)


def test_report_mail_is_excluded() -> None:
    assert not looks_like_recruiting("【秋招早报】2026-08-28 测评", "me@qq.com", "测评")


def test_interview_scheduling_invitation_is_excluded() -> None:
    subject = "校园招聘-面试邀请"
    body = "请点击按钮选择面试时间。预约截止时间为明天中午。"

    assert is_non_action_recruiting_notice(subject, body)
    assert not looks_like_recruiting(subject, "recruiting@example.com", body)


def test_interview_feedback_survey_is_excluded() -> None:
    subject = "邀请反馈面试体验"
    body = "请填写问卷，反馈内容仅用于面试体验优化。"

    assert is_non_action_recruiting_notice(subject, body)
    assert not looks_like_recruiting(subject, "recruiting@example.com", body)


def test_confirmed_interview_arrangement_is_included() -> None:
    subject = "校园招聘-面试安排"
    body = "面试时间：2026-09-10 10:00，面试形式：视频面试。"

    assert not is_non_action_recruiting_notice(subject, body)
    assert looks_like_recruiting(subject, "recruiting@example.com", body)


def test_recruiting_event_registration_is_excluded() -> None:
    subject = "产品团队线下专场活动｜报名已开启"
    body = "欢迎报名参加招聘活动。"

    assert is_non_action_recruiting_notice(subject, body)
    assert not looks_like_recruiting(subject, "recruiting@example.com", body)


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


def test_parse_mail_prefers_richer_html_job_deadline() -> None:
    message = EmailMessage()
    message["Subject"] = "校园招聘岗位推荐"
    message["From"] = "Recruiting <recruiting@example.com>"
    message.set_content("请前往招聘官网查看职位。")
    message.add_alternative(
        """<html><body>
        <h1>【产品类】产品策略经理（增长方向）</h1>
        <p>所属机构：示例科技公司</p>
        <p>截止时间：2026-10-10</p>
        <a href="https://jobs.example.com/apply">立即投递</a>
        </body></html>""",
        subtype="html",
    )
    meta = b'1 (UID 1 INTERNALDATE "16-Sep-2026 08:00:00 +0800" BODY[] {10})'

    parsed = parse_mail("1", meta, message.as_bytes())

    assert "截止时间：2026-10-10" in parsed.body
    assert "立即投递" in parsed.body
    assert looks_like_recruiting(parsed.subject, parsed.sender, parsed.body)
    assert parsed.urls == ["https://jobs.example.com/apply"]
