from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from .ai_parser import AIParser
from .apple_calendar import AppleCalendar
from .config import Config
from .deadlines import SHANGHAI, resolve_time
from .feishu import (
    STATE_TABLE_NAME,
    FeishuClient,
    FeishuError,
    datetime_to_millis,
    value_to_datetime,
)
from .mailbox import QQMailbox, looks_like_recruiting, normalize_message_id
from .models import BaseRecord, MailMessage, ParsedEmail, ResolvedTime, RunStats
from .report import render_report

LOGGER = logging.getLogger(__name__)


def _ids(record: BaseRecord) -> set[str]:
    return {
        normalized
        for line in record.text("Message-ID").splitlines()
        if (normalized := normalize_message_id(line))
    }


def _signature(
    company: str | None, position: str | None, item_type: str | None, start: datetime | None
) -> tuple[str, str, str, int] | None:
    if not all([company, position, item_type, start]):
        return None
    return (
        (company or "").strip().casefold(),
        (position or "").strip().casefold(),
        item_type or "",
        int((start or datetime.now(SHANGHAI)).timestamp() // 60),
    )


@dataclass
class RecordMatch:
    record: BaseRecord | None
    duplicate: bool


class RecordIndex:
    def __init__(self, records: list[BaseRecord]) -> None:
        self.records = records

    def match(
        self,
        mail: MailMessage,
        parsed: ParsedEmail,
        resolved: ResolvedTime,
    ) -> RecordMatch:
        for record in self.records:
            record_ids = _ids(record)
            if mail.message_id and mail.message_id in record_ids:
                return RecordMatch(record, True)
            if record.text("邮件指纹") == mail.fingerprint:
                return RecordMatch(record, True)
        if mail.references:
            for record in self.records:
                if _ids(record) & mail.references:
                    return RecordMatch(record, False)
        wanted = _signature(parsed.company, parsed.position, parsed.item_type, resolved.start)
        if wanted:
            for record in self.records:
                existing_time = value_to_datetime(record.fields.get("截止/面试时间"))
                existing = _signature(
                    record.text("公司"),
                    record.text("岗位"),
                    record.text("类型"),
                    existing_time,
                )
                if existing == wanted:
                    return RecordMatch(record, False)
        return RecordMatch(None, False)

    def is_exact_duplicate(self, mail: MailMessage) -> bool:
        for record in self.records:
            if mail.message_id and mail.message_id in _ids(record):
                return True
            if record.text("邮件指纹") == mail.fingerprint:
                return True
        return False

    def add(self, record: BaseRecord) -> None:
        self.records.append(record)


def _chosen_url(mail: MailMessage, parsed: ParsedEmail) -> str:
    if parsed.action_url_index is None:
        return ""
    return mail.urls[parsed.action_url_index]


def _merge_message_ids(record: BaseRecord | None, mail: MailMessage) -> str:
    existing = _ids(record) if record else set()
    if mail.message_id:
        existing.add(mail.message_id)
    return "\n".join(sorted(existing))


def record_fields(
    mail: MailMessage,
    parsed: ParsedEmail,
    resolved: ResolvedTime,
    *,
    existing: BaseRecord | None = None,
    now: datetime,
) -> dict[str, object]:
    needs_confirmation = (
        parsed.needs_confirmation
        or resolved.needs_confirmation
        or not parsed.company
        or not parsed.position
        or not parsed.item_type
        or resolved.start is None
    )
    reasons = [
        reason
        for reason in (
            parsed.confirmation_reason,
            resolved.reason,
            "公司名称无法确定" if not parsed.company else None,
            "岗位名称无法确定" if not parsed.position else None,
            "事项类型无法确定" if not parsed.item_type else None,
        )
        if reason
    ]
    fields: dict[str, object] = {
        "公司": parsed.company or (existing.text("公司") if existing else ""),
        "岗位": parsed.position or (existing.text("岗位") if existing else ""),
        "邮件接收时间": datetime_to_millis(mail.received_at),
        "Message-ID": _merge_message_ids(existing, mail),
        "邮件指纹": mail.fingerprint,
        "原邮件主题": mail.subject,
        "原始时间描述": parsed.original_time_text or "",
        "时间为推算": resolved.inferred,
        "需要人工确认": needs_confirmation,
        "确认说明": "；".join(dict.fromkeys(reasons)),
        "最后处理时间": datetime_to_millis(now),
        "时间类型": parsed.time_type,
    }
    if parsed.item_type:
        fields["类型"] = parsed.item_type
    url = _chosen_url(mail, parsed)
    if url:
        fields["链接"] = {"text": "打开链接", "link": url}
    if resolved.start:
        fields["截止/面试时间"] = datetime_to_millis(resolved.start)
        fields["Calendar 状态"] = "pending"
    if resolved.end:
        fields["结束时间"] = datetime_to_millis(resolved.end)
    return fields


def _merge_record(record: BaseRecord, changes: dict[str, object]) -> BaseRecord:
    return BaseRecord(record.record_id, {**record.fields, **changes})


def _event_title(record: BaseRecord) -> str:
    company = record.text("公司") or "公司待确认"
    position = record.text("岗位") or "岗位待确认"
    item_type = record.text("类型") or "事项"
    if record.text("时间类型") == "deadline":
        suffix = f"{item_type}截止"
    else:
        suffix = item_type
    return f"{company}｜{position}｜{suffix}"


def _event_description(record: BaseRecord, default_end: bool) -> str:
    lines = [
        f"类型：{record.text('类型')}",
        f"岗位：{record.text('岗位')}",
        f"原邮件主题：{record.text('原邮件主题')}",
        f"原始时间描述：{record.text('原始时间描述')}",
    ]
    link = record.text("链接")
    if link:
        lines.append(f"链接：{link}")
    if default_end:
        lines.append("结束时间为 Job Mail Assistant 的日历展示默认值，并非邮件原始信息。")
    return "\n".join(lines)


def sync_calendars(
    *,
    config: Config,
    feishu: FeishuClient,
    base_token: str,
    table_id: str,
    records: list[BaseRecord],
    stats: RunStats,
) -> list[str]:
    eligible = [
        record
        for record in records
        if not bool(record.fields.get("已完成"))
        and not bool(record.fields.get("需要人工确认"))
        and value_to_datetime(record.fields.get("截止/面试时间")) is not None
        and (
            record.text("Calendar Event ID") == ""
            or record.text("Calendar 状态") in {"failed", "pending"}
        )
    ]
    if not eligible:
        return []
    warnings: list[str] = []
    try:
        calendar = AppleCalendar(
            config.icloud_username,
            config.icloud_app_password,
            config.icloud_calendar_name,
        )
    except Exception as exc:
        message = f"Calendar 连接失败：{type(exc).__name__}"
        for record in eligible:
            try:
                changes = {"Calendar 状态": "failed", "Calendar 错误": message}
                feishu.update_record(base_token, table_id, record.record_id, changes)
                record.fields.update(changes)
            except FeishuError:
                pass
        return [message]

    with calendar:
        for record in eligible:
            start = value_to_datetime(record.fields.get("截止/面试时间"))
            if not start:
                continue
            explicit_end = value_to_datetime(record.fields.get("结束时间"))
            duration = timedelta(minutes=15 if record.text("时间类型") == "deadline" else 60)
            end = explicit_end or start + duration
            uid = f"jma-{record.record_id}@job-mail-assistant"
            try:
                result = calendar.upsert_event(
                    uid=uid,
                    title=_event_title(record),
                    start=start,
                    end=end,
                    description=_event_description(record, explicit_end is None),
                    url=record.text("链接") or None,
                )
                changes = {
                    "Calendar Event ID": uid,
                    "Calendar 状态": "created",
                    "Calendar 错误": "",
                }
                feishu.update_record(base_token, table_id, record.record_id, changes)
                record.fields.update(changes)
                if result == "created":
                    stats.calendar_created += 1
                else:
                    stats.calendar_updated += 1
            except Exception as exc:
                message = f"{_event_title(record)}：Calendar 创建失败（{type(exc).__name__}）"
                warnings.append(message)
                changes = {"Calendar 状态": "failed", "Calendar 错误": message}
                try:
                    feishu.update_record(base_token, table_id, record.record_id, changes)
                    record.fields.update(changes)
                except FeishuError as update_exc:
                    warnings.append(
                        f"{_event_title(record)}：Calendar 失败状态回写失败"
                        f"（{type(update_exc).__name__}）"
                    )
    return warnings


def retry_calendars(config: Config) -> int:
    """Retry only pending/failed Calendar items without touching mail or reports."""
    stats = RunStats()
    with FeishuClient(config.feishu_app_id, config.feishu_app_secret) as feishu:
        base_token = feishu.resolve_wiki(config.feishu_wiki_url)
        main_table_id = feishu.find_table(base_token, config.feishu_table_name)
        state_table_id = feishu.find_table(base_token, STATE_TABLE_NAME)
        feishu.validate_schema(base_token, main_table_id, state_table_id)
        records = feishu.list_records(base_token, main_table_id)
        warnings = sync_calendars(
            config=config,
            feishu=feishu,
            base_token=base_token,
            table_id=main_table_id,
            records=records,
            stats=stats,
        )
    LOGGER.info(
        "Calendar retry complete: created=%d updated=%d warnings=%d",
        stats.calendar_created,
        stats.calendar_updated,
        len(warnings),
    )
    return 2 if warnings else 0


def run(config: Config, *, dry_run: bool = False, now: datetime | None = None) -> int:
    run_started = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
    stats = RunStats()
    mailbox = QQMailbox(config.qq_email, config.qq_auth_code)
    ai = AIParser(config.ai_api_key, config.ai_base_url, config.ai_model)

    messages = mailbox.fetch_recent(days=config.scan_days, now=run_started)
    stats.fetched = len(messages)
    candidates = [
        mail for mail in messages if looks_like_recruiting(mail.subject, mail.sender, mail.body)
    ]
    stats.candidates = len(candidates)
    LOGGER.info("Fetched %d emails; recruiting candidates: %d", stats.fetched, stats.candidates)

    fatal_errors: list[str] = []
    warnings: list[str] = []
    changed_records: list[BaseRecord] = []
    progress_items: list[str] = []

    with FeishuClient(config.feishu_app_id, config.feishu_app_secret) as feishu:
        base_token = feishu.resolve_wiki(config.feishu_wiki_url)
        main_table_id = feishu.find_table(base_token, config.feishu_table_name)
        state_table_id = feishu.find_table(base_token, STATE_TABLE_NAME)
        feishu.validate_schema(base_token, main_table_id, state_table_id)
        records = feishu.list_records(base_token, main_table_id)
        cursor, cursor_record_id = feishu.get_cursor(
            base_token,
            state_table_id,
            default=run_started - timedelta(days=config.scan_days),
        )
        index = RecordIndex(records)

        for mail in candidates:
            if index.is_exact_duplicate(mail):
                stats.duplicates += 1
                continue
            try:
                parsed = ai.parse(mail)
            except Exception as exc:
                fatal_errors.append(f"AI 解析失败：{mail.subject}（{type(exc).__name__}）")
                continue
            if parsed.classification == "irrelevant":
                continue
            if parsed.classification == "progress":
                if mail.received_at > cursor:
                    label = "｜".join(
                        part
                        for part in [parsed.company, parsed.position, parsed.progress_summary]
                        if part
                    )
                    progress_items.append(label or mail.subject)
                    stats.progress_items += 1
                continue

            stats.relevant += 1
            resolved = resolve_time(parsed, mail.received_at)
            match = index.match(mail, parsed, resolved)
            if match.duplicate:
                stats.duplicates += 1
                continue
            changes = record_fields(
                mail,
                parsed,
                resolved,
                existing=match.record,
                now=run_started,
            )
            if changes.get("需要人工确认"):
                stats.needs_confirmation += 1
            if dry_run:
                record = _merge_record(
                    match.record or BaseRecord(f"dry-run-{mail.uid}", {}), changes
                )
                changed_records.append(record)
                continue
            try:
                if match.record:
                    feishu.update_record(
                        base_token, main_table_id, match.record.record_id, changes
                    )
                    match.record.fields.update(changes)
                    record = match.record
                    stats.updated_records += 1
                else:
                    record = feishu.create_record(base_token, main_table_id, changes)
                    record = _merge_record(record, changes)
                    index.add(record)
                    stats.new_records += 1
                changed_records.append(record)
            except FeishuError as exc:
                LOGGER.error("Feishu record write failed: %s", exc)
                fatal_errors.append(f"飞书写入失败：{mail.subject}（{exc}）")

        if dry_run:
            LOGGER.info(
                "Dry run complete: relevant=%d changes=%d confirmations=%d",
                stats.relevant,
                len(changed_records),
                stats.needs_confirmation,
            )
            return 0 if not fatal_errors else 1

        records = feishu.list_records(base_token, main_table_id)
        warnings.extend(
            sync_calendars(
                config=config,
                feishu=feishu,
                base_token=base_token,
                table_id=main_table_id,
                records=records,
                stats=stats,
            )
        )
        records = feishu.list_records(base_token, main_table_id)
        report_warnings = [*fatal_errors, *warnings]
        subject, body = render_report(
            run_started_at=run_started,
            changed_records=changed_records,
            all_records=records,
            progress_items=progress_items,
            warnings=report_warnings,
            stats=stats,
        )
        mailbox.send_report(subject, body)
        LOGGER.info("Morning report sent successfully")

        if not fatal_errors:
            feishu.set_cursor(
                base_token,
                state_table_id,
                run_started,
                cursor_record_id,
            )

    LOGGER.info(
        "Relevant=%d new=%d updated=%d duplicates=%d calendar_created=%d confirmations=%d",
        stats.relevant,
        stats.new_records,
        stats.updated_records,
        stats.duplicates,
        stats.calendar_created,
        stats.needs_confirmation,
    )
    if fatal_errors:
        return 1
    if warnings:
        return 2
    return 0
