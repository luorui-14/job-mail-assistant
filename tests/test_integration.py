from datetime import datetime

from job_mail_assistant.app import retry_calendars, run
from job_mail_assistant.config import Config
from job_mail_assistant.deadlines import SHANGHAI
from job_mail_assistant.models import BaseRecord, MailMessage, ParsedEmail, TimeExpression


class FakeMailbox:
    reports: list[tuple[str, str]] = []
    fetch_days: list[int] = []
    message = MailMessage(
        uid="1",
        message_id="same@example.com",
        fingerprint="a" * 64,
        subject="百度在线测评邀请",
        sender="hr@example.com",
        received_at=datetime(2026, 8, 27, 10, tzinfo=SHANGHAI),
        body="请在收到邮件后72小时内完成测评",
        urls=["https://exam.example.com/start"],
    )

    def __init__(self, *_):
        pass

    def fetch_recent(self, *, days, **_):
        self.__class__.fetch_days.append(days)
        return [self.message]

    def send_report(self, subject, body):
        self.reports.append((subject, body))


class FakeAI:
    def __init__(self, *_):
        pass

    def parse(self, _):
        return ParsedEmail(
            classification="action",
            company="百度",
            position="产品经理",
            item_type="测评",
            time_type="deadline",
            original_time_text="收到邮件后72小时内",
            time_expression=TimeExpression(
                kind="relative", relative_value=72, relative_unit="hour"
            ),
            action_url_index=0,
        )


class FakeFeishu:
    records: list[BaseRecord] = []
    cursor_record = None
    cursor: datetime | None = None

    def __init__(self, *_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def resolve_wiki(self, _):
        return "base"

    def find_table(self, _, name):
        return "state" if name.startswith("JMA_") else "main"

    def validate_schema(self, *_):
        pass

    def list_records(self, _, table):
        return [] if table == "state" else list(self.records)

    def get_cursor(self, *_, default):
        return self.cursor or default, self.cursor_record

    def set_cursor(self, *args):
        self.cursor_record = "state-rec"
        self.cursor = args[-2]
        return self.cursor_record

    def create_record(self, _, table, fields):
        assert table == "main"
        record = BaseRecord("rec1", dict(fields))
        self.records.append(record)
        return record

    def update_record(self, _, table, record_id, fields):
        record = next(item for item in self.records if item.record_id == record_id)
        record.fields.update(fields)
        return record


class FakeCalendar:
    calls = 0
    titles: list[str] = []

    def __init__(self, *_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def upsert_event(self, **kwargs):
        self.__class__.calls += 1
        self.__class__.titles.append(kwargs["title"])
        return "created"


def config() -> Config:
    return Config(
        qq_email="me@qq.com",
        qq_auth_code="secret",
        feishu_app_id="app",
        feishu_app_secret="secret",
        feishu_wiki_url="https://example.feishu.cn/wiki/test-token",
        icloud_username="me@icloud.com",
        icloud_app_password="secret",
        ai_api_key="secret",
        ai_base_url="https://ai.example.com/v1",
        ai_model="model",
    )


def test_two_runs_do_not_duplicate_record_or_calendar(monkeypatch):
    FakeFeishu.records = []
    FakeFeishu.cursor_record = None
    FakeFeishu.cursor = None
    FakeCalendar.calls = 0
    FakeCalendar.titles = []
    FakeMailbox.reports = []
    FakeMailbox.fetch_days = []
    monkeypatch.setattr("job_mail_assistant.app.QQMailbox", FakeMailbox)
    monkeypatch.setattr("job_mail_assistant.app.AIParser", FakeAI)
    monkeypatch.setattr("job_mail_assistant.app.FeishuClient", FakeFeishu)
    monkeypatch.setattr("job_mail_assistant.app.AppleCalendar", FakeCalendar)

    now = datetime(2026, 8, 28, 8, tzinfo=SHANGHAI)
    assert run(config(), now=now) == 0
    assert run(config(), now=now) == 0
    assert len(FakeFeishu.records) == 1
    assert FakeCalendar.calls == 1
    assert len(FakeMailbox.reports) == 2
    assert FakeMailbox.fetch_days == [2, 2]


def test_missed_runs_expand_the_mail_scan_window(monkeypatch):
    FakeFeishu.records = []
    FakeFeishu.cursor_record = "state-rec"
    FakeFeishu.cursor = datetime(2026, 8, 24, 8, tzinfo=SHANGHAI)
    FakeCalendar.calls = 0
    FakeCalendar.titles = []
    FakeMailbox.reports = []
    FakeMailbox.fetch_days = []
    monkeypatch.setattr("job_mail_assistant.app.QQMailbox", FakeMailbox)
    monkeypatch.setattr("job_mail_assistant.app.AIParser", FakeAI)
    monkeypatch.setattr("job_mail_assistant.app.FeishuClient", FakeFeishu)
    monkeypatch.setattr("job_mail_assistant.app.AppleCalendar", FakeCalendar)

    assert run(config(), now=datetime(2026, 8, 28, 8, tzinfo=SHANGHAI)) == 0
    assert FakeMailbox.fetch_days == [4]


def test_calendar_retry_does_not_read_mail_or_send_report(monkeypatch):
    when = datetime(2026, 8, 29, 18, tzinfo=SHANGHAI)
    FakeFeishu.records = [
        BaseRecord(
            "rec-calendar",
            {
                "公司": "百度",
                "岗位": "产品经理",
                "类型": "测评",
                "时间类型": "deadline",
                "截止/面试时间": int(when.timestamp() * 1000),
                "需要人工确认": False,
                "已完成": False,
                "Calendar Event ID": "",
                "Calendar 状态": "failed",
            },
        )
    ]
    FakeCalendar.calls = 0
    FakeCalendar.titles = []
    FakeMailbox.reports = []
    monkeypatch.setattr("job_mail_assistant.app.FeishuClient", FakeFeishu)
    monkeypatch.setattr("job_mail_assistant.app.AppleCalendar", FakeCalendar)

    assert retry_calendars(config()) == 0
    assert FakeCalendar.calls == 1
    assert FakeMailbox.reports == []
    assert FakeFeishu.records[0].fields["Calendar 状态"] == "created"


def test_position_only_confirmation_does_not_block_calendar_retry(monkeypatch):
    when = datetime(2026, 9, 8, 12, 19, tzinfo=SHANGHAI)
    FakeFeishu.records = [
        BaseRecord(
            "rec-calendar",
            {
                "公司": "快手",
                "岗位": "",
                "类型": "测评",
                "时间类型": "deadline",
                "截止/面试时间": int(when.timestamp() * 1000),
                "需要人工确认": True,
                "确认说明": "岗位名称无法确定",
                "已完成": False,
                "Calendar Event ID": "",
                "Calendar 状态": "failed",
            },
        )
    ]
    FakeCalendar.calls = 0
    FakeCalendar.titles = []
    monkeypatch.setattr("job_mail_assistant.app.FeishuClient", FakeFeishu)
    monkeypatch.setattr("job_mail_assistant.app.AppleCalendar", FakeCalendar)

    assert retry_calendars(config()) == 0
    assert FakeCalendar.calls == 1
    assert FakeCalendar.titles == ["快手｜测评截止"]
