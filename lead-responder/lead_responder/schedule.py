"""When we may bother people: quiet hours at night and over Shabbat (reminders and follow-ups only;
the instant reply to a lead who just filled a form always goes out)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Rules, parse_clock, parse_quiet_hours

FRIDAY, SATURDAY = 4, 5


@dataclass(frozen=True)
class QuietTimes:
    tz: ZoneInfo
    night: tuple[time, time] | None
    shabbat: tuple[time, time] | None  # Friday start, Saturday end

    @classmethod
    def from_rules(cls, rules: Rules, tz: ZoneInfo) -> QuietTimes:
        shabbat = None
        if rules.shabbat:
            shabbat = (parse_clock(rules.shabbat_start, "shabbat_start"), parse_clock(rules.shabbat_end, "shabbat_end"))
        return cls(tz, parse_quiet_hours(rules.quiet_hours, "quiet_hours"), shabbat)

    def _at(self, day: datetime, clock: time, days: int = 0) -> datetime:
        return datetime.combine(day.date() + timedelta(days=days), clock, tzinfo=self.tz)

    def _quiet_until(self, when: datetime) -> datetime | None:
        """End of the quiet window ``when`` falls in, or None when it's fine to send."""
        local = when.astimezone(self.tz)
        now = local.time()
        if self.shabbat:
            start, end = self.shabbat
            if local.weekday() == FRIDAY and now >= start:
                return self._at(local, end, days=1)
            if local.weekday() == SATURDAY and now < end:
                return self._at(local, end)
        if self.night:
            start, end = self.night
            if start > end:  # crosses midnight, e.g. 21:00-08:00
                if now >= start:
                    return self._at(local, end, days=1)
                if now < end:
                    return self._at(local, end)
            elif start <= now < end:
                return self._at(local, end)
        return None

    def is_quiet(self, when: datetime) -> bool:
        return self._quiet_until(when) is not None

    def next_allowed(self, when: datetime) -> datetime:
        """``when`` itself, or the first moment after it that isn't quiet (in UTC)."""
        for _ in range(4):  # Shabbat can end inside the night window, so a couple of hops
            until = self._quiet_until(when)
            if until is None:
                break
            when = until
        return when.astimezone(timezone.utc)
