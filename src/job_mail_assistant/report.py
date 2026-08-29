from __future__ import annotations

from datetime import datetime

from .deadlines import SHANGHAI
from .feishu import value_to_datetime
from .models import BaseRecord, RunStats


def _label(record: BaseRecord) -> str:
    company = record.text("公司") or "公司待确认"
    position = record.text("岗位") or "岗位待确认"
    item_type = record.text("类型") or "类型待确认"
    return f"{company}｜{position}｜{item_type}"


def _format_time(value: datetime | None) -> str:
    return value.astimezone(SHANGHAI).strftime("%m月%d日 %H:%M") if value else "未确定"


def render_report(
    *,
    run_started_at: datetime,
    changed_records: list[BaseRecord],
    all_records: list[BaseRecord],
    progress_items: list[str],
    warnings: list[str],
    stats: RunStats,
) -> tuple[str, str]:
    subject = f"【秋招早报】{run_started_at.astimezone(SHANGHAI):%Y-%m-%d} 测评 & 面试汇总"
    lines: list[str] = ["Job Mail Assistant 秋招早报", ""]

    lines.append("一、本次扫描新增")
    if changed_records:
        lines.append(f"本次新增或确认 {len(changed_records)} 项：")
        for record in changed_records:
            when = value_to_datetime(record.fields.get("截止/面试时间"))
            lines.extend(["", _label(record), f"时间：{_format_time(when)}"])
            link = record.text("链接")
            if link:
                lines.append(f"链接：{link}")
    else:
        lines.append("本次没有新增事项。")

    pending: list[tuple[datetime, BaseRecord]] = []
    confirmations: list[BaseRecord] = []
    now = run_started_at.astimezone(SHANGHAI)
    for record in all_records:
        if not (record.text("公司") or record.text("岗位")):
            continue
        if bool(record.fields.get("已完成")):
            continue
        when = value_to_datetime(record.fields.get("截止/面试时间"))
        needs_confirmation = bool(record.fields.get("需要人工确认"))
        if needs_confirmation or when is None:
            confirmations.append(record)
        elif when >= now:
            pending.append((when, record))
    pending.sort(key=lambda item: item[0])

    lines.extend(["", "二、当前未完成事项"])
    if pending:
        for when, record in pending:
            lines.extend(["", _format_time(when), _label(record)])
    else:
        lines.append("当前没有有明确时间且尚未过期的未完成事项。")

    lines.extend(["", "三、需要人工确认"])
    if confirmations:
        for record in confirmations:
            lines.extend(["", _label(record)])
            original = record.text("原始时间描述")
            reason = record.text("确认说明") or "截止/面试时间无法可靠确定"
            if original:
                lines.append(f"原文：{original}")
            lines.append(f"问题：{reason}")
    else:
        lines.append("没有需要人工确认的事项。")

    lines.extend(["", "四、其他招聘进展"])
    if progress_items:
        lines.extend(f"- {item}" for item in progress_items)
    else:
        lines.append("本次没有新的淘汰或流程结束通知。")

    if warnings:
        lines.extend(["", "五、同步异常"])
        lines.extend(f"- {warning}" for warning in warnings)

    lines.extend(
        [
            "",
            "运行摘要",
            f"Fetched emails: {stats.fetched}",
            f"Recruiting candidates: {stats.candidates}",
            f"Relevant recruiting emails: {stats.relevant}",
            f"New records: {stats.new_records}",
            f"Updated records: {stats.updated_records}",
            f"Duplicates skipped: {stats.duplicates}",
            f"Calendar events created: {stats.calendar_created}",
            f"Calendar events updated: {stats.calendar_updated}",
            f"Needs confirmation: {stats.needs_confirmation}",
        ]
    )
    return subject, "\n".join(lines).strip() + "\n"
