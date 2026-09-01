from __future__ import annotations

import json
import time
from typing import Any

from openai import APIStatusError, BadRequestError, OpenAI

from .confirmations import normalize_confirmation
from .models import MailMessage, ParsedEmail

SYSTEM_PROMPT = """你是求职邮件信息提取器。只输出符合 JSON Schema 的对象。

分类：
- action：需要用户后续行动的测评、笔试、AI/视频/电话/现场面试、预约或时间确认。
- progress：未通过、淘汰、流程结束等进展；不得当作 action。
- irrelevant：投递成功、简历已收到、宣传、宣讲会、岗位推荐、营销或私人邮件。

规则：
1. 不做日期加减，不根据相对时间生成最终日期。只提取 time_expression 的组成部分。
2. absolute 提取邮件明确写出的年/月/日/时/分；未写的字段必须为 null。
3. relative 提取 value 和 hour/day/workday；“N天内”是 day，“N个工作日内”是 workday。
4. weekday 中本周 week_offset=0、下周=1，星期一到星期日为 1..7。
5. 无法可靠确定时间用 ambiguous 或 none，并 needs_confirmation=true。
6. time_type：截止时间为 deadline，固定发生时间为 fixed，无时间为 none。
7. action_url_index 只能是给定候选链接的从 0 开始序号；不确定则 null。
8. 公司、岗位不确定时留 null，不猜测。岗位是可选信息，缺失本身不得触发
   needs_confirmation，也不得产生“岗位无法确定”之类的确认原因。
9. item_type 必须选最贴切的枚举；泛称面试用“其他面试”。
10. 行动邮件没有可靠入口链接或候选链接明显冲突时，needs_confirmation=true 并说明原因。
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


class AIParser:
    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
        self.model = model

    def _messages(self, mail: MailMessage) -> list[dict[str, str]]:
        url_lines = "\n".join(f"[{index}] {url}" for index, url in enumerate(mail.urls))
        user = (
            f"邮件接收时间（北京时间）：{mail.received_at.isoformat()}\n"
            f"主题：{mail.subject}\n"
            f"发件人：{mail.sender}\n"
            f"候选链接：\n{url_lines or '(无)'}\n\n"
            f"正文：\n{mail.body[:30000]}"
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]

    def _request(self, mail: MailMessage, response_format: dict[str, Any]) -> str:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=self._messages(mail),  # type: ignore[arg-type]
                    temperature=0,
                    response_format=response_format,  # type: ignore[arg-type]
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
        try:
            content = self._request(mail, schema_format)
        except BadRequestError:
            content = self._request(mail, {"type": "json_object"})
        try:
            parsed = ParsedEmail.model_validate(json.loads(content))
        except Exception as exc:
            raise AIParseError("AI response failed schema validation") from exc
        parsed.needs_confirmation, normalized_reason = normalize_confirmation(
            parsed.needs_confirmation, parsed.confirmation_reason
        )
        parsed.confirmation_reason = normalized_reason or None
        if parsed.action_url_index is not None and parsed.action_url_index >= len(mail.urls):
            parsed.action_url_index = None
            parsed.needs_confirmation = True
            parsed.confirmation_reason = "AI 返回的链接序号无效"
        if (
            parsed.classification == "action"
            and parsed.action_url_index is None
            and len(mail.urls) == 1
        ):
            parsed.action_url_index = 0
            _remove_link_only_confirmation(parsed)
        return parsed
