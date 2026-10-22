from __future__ import annotations

import json
import logging
import time
from typing import Any

from openai import APIStatusError, BadRequestError, OpenAI
from pydantic import ValidationError

from .confirmations import is_missing_relative_anchor_only_reason, normalize_confirmation
from .deadlines import explicit_expiry_from_text, normalize_assessment_window
from .models import MailMessage, ParsedEmail, TimeExpression

LOGGER = logging.getLogger(__name__)
MAX_VALIDATION_ATTEMPTS = 3

SYSTEM_PROMPT = """你是求职邮件信息提取器。只输出符合 JSON Schema 的对象。

分类：
- action：有明确投递/申请入口和截止时间的招聘岗位；需要用户完成的测评、笔试、异步 AI
  面试；或邮件已明确确认实际发生日期和时间的视频/电话/现场面试。
- progress：未通过、淘汰、流程结束等进展；不得当作 action。
- irrelevant：投递成功、简历已收到、没有明确投递截止时间的宣传/岗位推荐、宣讲会、招聘
  活动、营销或私人邮件；
  还包括邀请用户预约/选择/协商面试时间、仅给出预约截止时间、面试后的问卷/满意度/体验
  反馈。这些邮件即使含“面试”“邀请”“截止时间”或链接，也不是已确认的面试安排。

规则：
1. 不做日期加减，不根据相对时间生成最终日期。只提取 time_expression 的组成部分。
2. absolute 提取邮件明确写出的年/月/日/时/分；未写的字段必须为 null。
3. relative 提取 relative_value 和 relative_unit；单位只能是 hour/day/workday。
   “N小时内”或“链接有效期为N小时”是 hour，“N天内”是 day，“N个工作日内”是 workday。
   relative 一律以给定的邮件接收时间为起算点；正文未另写起始日期不构成不确定性，不得因此
   设置 needs_confirmation=true。
4. weekday 中本周 week_offset=0、下周=1，星期一到星期日为 1..7。
5. 无法可靠确定时间用 ambiguous 或 none，并 needs_confirmation=true。
6. time_type：截止时间为 deadline，固定发生时间为 fixed，无时间为 none。
7. action_url_index 只能是给定候选链接的从 0 开始序号；不确定则 null。
8. 公司、岗位不确定时留 null，不猜测。岗位是可选信息，缺失本身不得触发
   needs_confirmation，也不得产生“岗位无法确定”之类的确认原因。
9. item_type 必须选最贴切的枚举；岗位投递/申请截止用“投递”，泛称面试用“其他面试”。
10. 行动邮件没有可靠入口链接或候选链接明显冲突时，needs_confirmation=true 并说明原因。
11. 真人视频/电话/现场面试只有在邮件明确确认了实际面试日期和时间时才是 action，且
    time_type 必须为 fixed。预约、选时、改约协商和预约截止不得提取成面试发生时间。
12. 测评/笔试邀请同时写有“生效”和“失效”时，完成截止时间取“失效”时刻；日期和
    时间之间的“周五”等星期标注不改变明确的年月日时分。
13. 测评/笔试/异步 AI 面试的“开始时间”和“结束时间”若表示可完成考试的开放窗口，
    time_type 为 deadline，time_expression 取结束时间，end_time_expression 为 null。
    测试时长（如 80 分钟）是答题耗时，不是开放窗口，也不应从结束时间扣除。
    明确要求统一开考、固定时段参加的考试仍为 fixed，提取实际开始和结束时间。
"""


def _remove_link_only_confirmation(parsed: ParsedEmail) -> None:
    """Clear an AI warning when the only uncertainty was choosing the sole URL."""
    if not parsed.needs_confirmation or not parsed.confirmation_reason:
        return
    clauses = [
        clause.strip()
        for clause in parsed.confirmation_reason.replace("；", ";").split(";")
        if clause.strip()
    ]
    remaining = [
        clause
        for clause in clauses
        if not any(term in clause.casefold() for term in ("链接", "入口", "url"))
    ]
    if remaining:
        parsed.confirmation_reason = "；".join(remaining)
    else:
        parsed.needs_confirmation = False
        parsed.confirmation_reason = None


def _remove_expiry_time_only_confirmation(parsed: ParsedEmail) -> None:
    if not parsed.needs_confirmation or not parsed.confirmation_reason:
        return
    clauses = [
        clause.strip()
        for clause in parsed.confirmation_reason.replace("；", ";").split(";")
        if clause.strip()
    ]
    remaining = [
        clause
        for clause in clauses
        if not (
            any(term in clause for term in ("时间", "日期", "失效", "到期", "月或日"))
            and any(
                term in clause
                for term in ("无法", "缺少", "未提取", "未识别", "不确定", "未给出", "没有")
            )
            and not any(term in clause.casefold() for term in ("链接", "入口", "url"))
        )
    ]
    parsed.confirmation_reason = "；".join(remaining) or None
    parsed.needs_confirmation = bool(remaining)


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make every object property required as demanded by strict structured output."""
    schema = json.loads(json.dumps(schema))

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    return schema


class AIParseError(RuntimeError):
    pass


def _validation_feedback(exc: ValidationError) -> str:
    """Describe schema failures without including model output or mail content."""
    known_fields = set(ParsedEmail.model_fields) | set(TimeExpression.model_fields)
    failures = []
    for error in exc.errors(include_input=False, include_context=False, include_url=False)[:8]:
        path = ".".join(
            str(part) if part in known_fields or isinstance(part, int) else "<field>"
            for part in error["loc"]
        ) or "<root>"
        failures.append(f"{path}: {error['type']}")
    return "; ".join(failures)


class AIParser:
    def __init__(
        self, api_key: str, base_url: str, model: str, *, enable_thinking: bool | None = None
    ) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
        self.model = model
        self.enable_thinking = enable_thinking

    def _messages(
        self, mail: MailMessage, validation_feedback: str | None = None
    ) -> list[dict[str, str]]:
        url_lines = "\n".join(f"[{index}] {url}" for index, url in enumerate(mail.urls))
        user = (
            f"邮件接收时间（北京时间）：{mail.received_at.isoformat()}\n"
            f"主题：{mail.subject}\n"
            f"发件人：{mail.sender}\n"
            f"候选链接：\n{url_lines or '(无)'}\n\n"
            f"正文：\n{mail.body[:30000]}"
        )
        schema = json.dumps(
            _strict_json_schema(ParsedEmail.model_json_schema()), ensure_ascii=False
        )
        messages = [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n完整 JSON Schema：\n{schema}"},
            {"role": "user", "content": user},
        ]
        if validation_feedback:
            messages.append({
                "role": "user",
                "content": (
                    f"上次输出未通过校验：{validation_feedback}。请重新提取并返回完整 JSON 对象。"
                    "严格使用 schema 的字段名和枚举；未知信息用 null。relative 必须包含"
                    "正整数 relative_value 以及 hour/day/workday 之一的 relative_unit。"
                ),
            })
        return messages

    def _request(
        self,
        mail: MailMessage,
        response_format: dict[str, Any],
        *,
        validation_feedback: str | None = None,
    ) -> str:
        last_error: Exception | None = None
        request_options: dict[str, Any] = {}
        if self.enable_thinking is not None:
            request_options["extra_body"] = {"enable_thinking": self.enable_thinking}
        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self._messages(mail, validation_feedback),  # type: ignore[arg-type]
                    temperature=0,
                    response_format=response_format,  # type: ignore[arg-type]
                    **request_options,
                )
                content = response.choices[0].message.content
                if not content:
                    raise AIParseError("AI returned empty content")
                return content
            except APIStatusError as exc:
                last_error = exc
                if exc.status_code not in {408, 409, 429, 500, 502, 503, 504}:
                    raise
                if attempt < 2:
                    time.sleep(2**attempt)
        raise AIParseError(f"AI request failed after retries: {type(last_error).__name__}")

    def parse(self, mail: MailMessage) -> ParsedEmail:
        schema_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "job_mail_extraction",
                "strict": True,
                "schema": _strict_json_schema(ParsedEmail.model_json_schema()),
            },
        }
        response_format = schema_format
        feedback = None
        for attempt in range(MAX_VALIDATION_ATTEMPTS):
            request_kwargs = {"validation_feedback": feedback} if feedback else {}
            try:
                content = self._request(mail, response_format, **request_kwargs)
            except BadRequestError:
                if response_format["type"] != "json_schema":
                    raise
                response_format = {"type": "json_object"}
                content = self._request(mail, response_format, **request_kwargs)
            try:
                parsed = ParsedEmail.model_validate(json.loads(content))
                break
            except json.JSONDecodeError as exc:
                feedback = f"Invalid JSON at line {exc.lineno}, column {exc.colno}"
            except ValidationError as exc:
                feedback = _validation_feedback(exc)
            LOGGER.warning(
                "AI response rejected (attempt %d/%d): %s",
                attempt + 1, MAX_VALIDATION_ATTEMPTS, feedback,
            )
        else:
            raise AIParseError(f"AI response failed schema validation after retries: {feedback}")
        parsed.needs_confirmation, normalized_reason = normalize_confirmation(
            parsed.needs_confirmation, parsed.confirmation_reason
        )
        parsed.confirmation_reason = normalized_reason or None
        if (
            parsed.time_expression.kind == "relative"
            and is_missing_relative_anchor_only_reason(parsed.confirmation_reason)
        ):
            parsed.needs_confirmation = False
            parsed.confirmation_reason = None
        if parsed.action_url_index is not None and parsed.action_url_index >= len(mail.urls):
            parsed.action_url_index = None
            parsed.needs_confirmation = True
            parsed.confirmation_reason = "AI 返回的链接序号无效"
        window_deadline = normalize_assessment_window(parsed, mail.body)
        if (
            parsed.classification == "action"
            and parsed.action_url_index is None
            and len(mail.urls) == 1
        ):
            parsed.action_url_index = 0
            _remove_link_only_confirmation(parsed)
        if (
            parsed.classification == "action"
            and parsed.item_type in {"测评", "笔试", "AI面试"}
            and parsed.time_type != "fixed"
            and (window_deadline is not None or explicit_expiry_from_text(mail.body) is not None)
        ):
            if parsed.time_type == "none":
                parsed.time_type = "deadline"
            _remove_expiry_time_only_confirmation(parsed)
        return parsed
