from datetime import datetime

from job_mail_assistant.deadlines import SHANGHAI
from job_mail_assistant.feishu import datetime_to_millis
from job_mail_assistant.models import BaseRecord, RunStats
from job_mail_assistant.report import render_report


def test_legacy_position_only_confirmation_is_rendered_as_pending_without_placeholder() -> None:
    deadline = datetime(2026, 9, 8, 12, 19, tzinfo=SHANGHAI)
    record = BaseRecord(
        "rec-kuaishou",
        {
            "公司": "快手",
            "岗位": "",
            "类型": "测评",
            "截止/面试时间": datetime_to_millis(deadline),
            "已完成": False,
            "需要人工确认": True,
            "确认说明": "岗位名称无法确定",
        },
    )

    _, body = render_report(
        run_started_at=datetime(2026, 9, 1, 8, tzinfo=SHANGHAI),
        changed_records=[],
        all_records=[record],
        progress_items=[],
        warnings=[],
        stats=RunStats(),
    )

    assert "快手｜测评" in body
    assert "岗位待确认" not in body
    assert "三、需要人工确认\n没有需要人工确认的事项。" in body
