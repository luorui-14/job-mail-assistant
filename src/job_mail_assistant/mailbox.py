from __future__ import annotations

import hashlib
import imaplib
import re
import smtplib
from datetime import datetime, timedelta
from email import policy
from email.header import decode_header
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from .deadlines import SHANGHAI
from .models import MailMessage

ACTION_TERMS = (
    "测评",
    "笔试",
    "面试",
    "在线测试",
    "assessment",
    "interview",
    "written test",
    "一面",
    "二面",
    "三面",
    "终面",
    "群面",
    "hr面",
    "电话沟通",
    "视频沟通",
    "在线评估",
    "性格测试",
    "未通过",
    "淘汰",
    "流程结束",
)
REPORT_PREFIX = "【秋招早报】"
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
INTERNALDATE_RE = re.compile(rb'INTERNALDATE "([^"]+)"')


def decode_mime(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            parts.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(part)
    return "".join(parts).strip()


def normalize_message_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip("<>").strip().lower()
    return normalized or None


def parse_references(message: Message) -> set[str]:
    values = " ".join(
        filter(None, [message.get("References", ""), message.get("In-Reply-To", "")])
    )
    return {
        normalized
        for raw in re.findall(r"<([^>]+)>", values)
        if (normalized := normalize_message_id(raw))
    }


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")


def extract_body_and_urls(message: Message) -> tuple[str, list[str]]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain_parts.append(_decode_part(part))
        elif content_type == "text/html":
            html_parts.append(_decode_part(part))

    urls: list[str] = []
    html_text: list[str] = []
    for html in html_parts:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("a", href=True):
            href = unescape(str(tag.get("href", ""))).strip()
            urls.append(href)
        html_text.append(soup.get_text("\n", strip=True))

    body = "\n".join(part.strip() for part in plain_parts if part.strip())
    if not body:
        body = "\n".join(html_text)
    urls.extend(URL_RE.findall(body))
    return body[:50_000], filter_action_urls(urls)


def _unwrap_known_redirect(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in ("url", "target", "redirect", "redirect_url"):
        value = query.get(key)
        if value and value[0].startswith(("http://", "https://")):
            return unquote(value[0])
    return url


def filter_action_urls(urls: list[str]) -> list[str]:
    blocked_terms = (
        "unsubscribe",
        "退订",
        "privacy",
        "policy",
        "tracking",
        "track.",
        "pixel",
    )
    image_exts = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")
    result: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = _unwrap_known_redirect(raw.rstrip(".,;，。；）)]"))
        parsed = urlparse(url)
        lowered = url.lower()
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if any(term in lowered for term in blocked_terms):
            continue
        if parsed.path.lower().endswith(image_exts):
            continue
        kept_query = [
            part
            for part in parsed.query.split("&")
            if part and not part.lower().startswith("utm_")
        ]
        normalized = parsed._replace(query="&".join(kept_query), fragment="").geturl()
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result[:30]


def looks_like_recruiting(subject: str, sender: str, body: str) -> bool:
    if subject.startswith(REPORT_PREFIX):
        return False
    sample = f"{subject}\n{sender}\n{body[:5000]}".lower()
    return any(term.lower() in sample for term in ACTION_TERMS)


def _received_at(meta: bytes) -> datetime:
    match = INTERNALDATE_RE.search(meta)
    if not match:
        raise ValueError("IMAP response did not contain INTERNALDATE")
    value = match.group(1).decode("ascii")
    parsed = parsedate_to_datetime(value)
    if parsed is None:
        raise ValueError("IMAP INTERNALDATE could not be parsed")
    return parsed.astimezone(SHANGHAI)


def _sender(message: Message) -> str:
    addresses = getaddresses([message.get("From", "")])
    if not addresses:
        return decode_mime(message.get("From"))
    name, address = addresses[0]
    decoded_name = decode_mime(name)
    return f"{decoded_name} <{address}>" if decoded_name else address


def parse_mail(uid: str, meta: bytes, raw: bytes) -> MailMessage:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = decode_mime(message.get("Subject"))
    sender = _sender(message)
    received_at = _received_at(meta)
    body, urls = extract_body_and_urls(message)
    message_id = normalize_message_id(message.get("Message-ID"))
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    digest_source = "\n".join(
        [sender.lower(), subject.strip().lower(), received_at.isoformat(), body_hash]
    )
    fingerprint = hashlib.sha256(digest_source.encode()).hexdigest()
    return MailMessage(
        uid=uid,
        message_id=message_id,
        fingerprint=fingerprint,
        subject=subject,
        sender=sender,
        received_at=received_at,
        body=body,
        urls=urls,
        references=parse_references(message),
    )


class QQMailbox:
    def __init__(self, email: str, auth_code: str) -> None:
        self.email = email
        self.auth_code = auth_code

    def fetch_recent(self, *, days: int, now: datetime | None = None) -> list[MailMessage]:
        current = (now or datetime.now(SHANGHAI)).astimezone(SHANGHAI)
        cutoff = current - timedelta(days=days)
        month = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
        since = f"{cutoff.day:02d}-{month[cutoff.month - 1]}-{cutoff.year:04d}"
        messages: list[MailMessage] = []
        with imaplib.IMAP4_SSL("imap.qq.com", 993) as client:
            client.login(self.email, self.auth_code)
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                raise RuntimeError("QQ IMAP could not select INBOX")
            status, data = client.uid("search", None, "SINCE", since)
            if status != "OK":
                raise RuntimeError("QQ IMAP search failed")
            uids = data[0].split() if data and data[0] else []
            for uid_bytes in uids:
                status, fetched = client.uid(
                    "fetch", uid_bytes, "(UID INTERNALDATE BODY.PEEK[])"
                )
                if status != "OK" or not fetched:
                    raise RuntimeError(f"QQ IMAP fetch failed for UID {uid_bytes.decode()}")
                tuple_part = next((item for item in fetched if isinstance(item, tuple)), None)
                if not tuple_part:
                    raise RuntimeError("QQ IMAP returned an unexpected fetch response")
                meta, raw = tuple_part
                parsed = parse_mail(uid_bytes.decode(), meta, raw)
                if cutoff <= parsed.received_at <= current:
                    messages.append(parsed)
        return sorted(messages, key=lambda item: item.received_at)

    def send_report(self, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self.email
        message["To"] = self.email
        message["Subject"] = subject
        message.set_content(body, subtype="plain", charset="utf-8")
        with smtplib.SMTP_SSL("smtp.qq.com", 465) as client:
            client.login(self.email, self.auth_code)
            client.send_message(message)
