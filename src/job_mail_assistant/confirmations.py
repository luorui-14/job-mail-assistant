from __future__ import annotations

POSITION_TERMS = ("岗位", "职位", "职务")
OTHER_REQUIRED_INFORMATION_TERMS = (
    "公司",
    "企业",
    "事项类型",
    "时间",
    "日期",
    "截止",
    "链接",
    "入口",
    "url",
)
RELATIVE_ANCHOR_TERMS = (
    "起始日期",
    "开始日期",
    "起算日期",
    "基准日期",
    "起始时间",
    "开始时间",
    "起算时间",
    "基准时间",
)
MISSING_ANCHOR_TERMS = ("未给", "未提供", "没有", "缺少", "不明确", "无法确定")
MATERIAL_UNCERTAINTY_TERMS = (
    "候选链接",
    "链接序号",
    "入口",
    "url",
    "冲突",
    "矛盾",
    "无法访问",
    "不可用",
    "公司",
    "企业",
    "事项类型",
    "岗位",
    "职位",
)


def _is_position_only_clause(clause: str) -> bool:
    normalized = clause.casefold()
    return any(term in normalized for term in POSITION_TERMS) and not any(
        term in normalized for term in OTHER_REQUIRED_INFORMATION_TERMS
    )


def is_missing_relative_anchor_only_reason(reason: str | None) -> bool:
    """Recognize an AI warning that relative time lacks an explicit anchor.

    Relative durations are anchored to the authoritative IMAP receive time, so this
    warning is not actionable. Keep the check conservative so unrelated uncertainty
    in the same reason is never discarded.
    """
    if not reason:
        return False
    normalized = reason.casefold()
    return (
        any(term in normalized for term in RELATIVE_ANCHOR_TERMS)
        and any(term in normalized for term in MISSING_ANCHOR_TERMS)
        and not any(term in normalized for term in MATERIAL_UNCERTAINTY_TERMS)
    )


def normalize_confirmation(needs_confirmation: bool, reason: str | None) -> tuple[bool, str]:
    """Ignore position-only uncertainty while preserving every material uncertainty."""
    if not needs_confirmation:
        return False, ""
    if not reason:
        return True, ""
    clauses = [
        clause.strip()
        for clause in reason.replace("；", ";").split(";")
        if clause.strip()
    ]
    remaining = [clause for clause in clauses if not _is_position_only_clause(clause)]
    return bool(remaining), "；".join(remaining)
