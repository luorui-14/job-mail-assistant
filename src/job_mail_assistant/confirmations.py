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


def _is_position_only_clause(clause: str) -> bool:
    normalized = clause.casefold()
    return any(term in normalized for term in POSITION_TERMS) and not any(
        term in normalized for term in OTHER_REQUIRED_INFORMATION_TERMS
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
