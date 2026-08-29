from __future__ import annotations

from datetime import UTC, datetime, timedelta

import caldav
from icalendar import Alarm, Calendar, Event


class CalendarError(RuntimeError):
    pass


def build_ical(
    *,
    uid: str,
    title: str,
    start: datetime,
    end: datetime,
    description: str,
    url: str | None,
) -> bytes:
    calendar = Calendar()
    calendar.add("prodid", "-//Job Mail Assistant//CN")
    calendar.add("version", "2.0")
    event = Event()
    event.add("uid", uid)
    event.add("dtstamp", datetime.now(UTC))
    event.add("dtstart", start)
    event.add("dtend", end)
    event.add("summary", title)
    event.add("description", description)
    if url:
        event.add("url", url)
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", title)
    alarm.add("trigger", timedelta(hours=-24))
    event.add_component(alarm)
    calendar.add_component(event)
    return calendar.to_ical()


class AppleCalendar:
    def __init__(self, username: str, password: str, calendar_name: str) -> None:
        self.client = caldav.DAVClient(
            url="https://caldav.icloud.com/",
            username=username.strip(),
            password=password.strip(),
            auth_type="basic",
            timeout=30,
        )
        principal = self.client.principal()
        calendars = principal.calendars()
        matches = [item for item in calendars if item.name == calendar_name]
        if len(matches) != 1:
            self.client.close()
            raise CalendarError(
                f"Expected exactly one iCloud calendar named {calendar_name!r}, "
                f"found {len(matches)}"
            )
        self.calendar = matches[0]

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> AppleCalendar:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def upsert_event(
        self,
        *,
        uid: str,
        title: str,
        start: datetime,
        end: datetime,
        description: str,
        url: str | None,
    ) -> str:
        data = build_ical(
            uid=uid,
            title=title,
            start=start,
            end=end,
            description=description,
            url=url,
        )
        # iCloud rejects UID REPORT queries. python-caldav derives a
        # deterministic ``<quoted UID>.ics`` URL from the VEVENT UID, so this
        # PUT is an idempotent upsert and safely overwrites a previous retry.
        self.calendar.save_event(data)
        return "created"
