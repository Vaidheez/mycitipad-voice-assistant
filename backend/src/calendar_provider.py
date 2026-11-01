"""Calendar provider protocol and SQLite mock implementation for MyCitiPad viewings.

Designed behind a clean protocol so a production Microsoft Graph Outlook provider
can replace the mock without touching the agent tools.
"""

import logging
import random
import sqlite3
import string
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from config import (
    MAX_DAYS_AHEAD,
    MAX_SLOTS_OFFERED,
    MIN_NOTICE_HOURS,
    TIMEZONE,
    VIEWING_DAYS,
    VIEWING_END_HOUR,
    VIEWING_LENGTH_MIN,
    VIEWING_START_HOUR,
)
from db import get_connection, get_property, init_db, queue_outbox_message

logger = logging.getLogger("mycitipad.calendar")

# Spoken ordinal numbers for UK English dates
ORDINALS = {
    1: "first",
    2: "second",
    3: "third",
    4: "fourth",
    5: "fifth",
    6: "sixth",
    7: "seventh",
    8: "eighth",
    9: "ninth",
    10: "tenth",
    11: "eleventh",
    12: "twelfth",
    13: "thirteenth",
    14: "fourteenth",
    15: "fifteenth",
    16: "sixteenth",
    17: "seventeenth",
    18: "eighteenth",
    19: "nineteenth",
    20: "twentieth",
    21: "twenty-first",
    22: "twenty-second",
    23: "twenty-third",
    24: "twenty-fourth",
    25: "twenty-fifth",
    26: "twenty-sixth",
    27: "twenty-seventh",
    28: "twenty-eighth",
    29: "twenty-ninth",
    30: "thirtieth",
    31: "thirty-first",
}

HOUR_WORDS = {
    10: "ten in the morning",
    11: "eleven in the morning",
    12: "twelve noon",
    13: "one in the afternoon",
    14: "two in the afternoon",
    15: "three in the afternoon",
    16: "four in the afternoon",
    17: "five in the afternoon",
}


def format_spoken_slot(dt_london: datetime) -> str:
    """Format slot in natural British English.

    Example: 'Tuesday the third of November at two in the afternoon'
    """
    weekday = dt_london.strftime("%A")
    day_num = dt_london.day
    day_word = ORDINALS.get(day_num, f"{day_num}th")
    month = dt_london.strftime("%B")
    time_word = HOUR_WORDS.get(dt_london.hour, dt_london.strftime("%I:%M %p").lower())

    return f"{weekday} the {day_word} of {month} at {time_word}"


@dataclass
class Slot:
    """A viewing slot with UTC timestamps and human spoken representation."""

    slot_start_utc: str
    slot_end_utc: str
    spoken: str


@dataclass
class BookingRequest:
    """Request payload to book a viewing slot."""

    property_id: str
    caller_name: str
    caller_email: str
    caller_phone: str | None = None
    call_id: str | None = None


@dataclass
class BookingResult:
    """Outcome of attempting to book a viewing slot."""

    status: str  # "confirmed" | "slot_taken" | "failed"
    booking_ref: str | None = None
    property_address: str | None = None
    slot_start_utc: str | None = None
    spoken_time: str | None = None
    confirmation_queued: bool = False
    alternative_slots: list[Slot] | None = None
    error_message: str | None = None


class CalendarProvider(Protocol):
    """Calendar interface for checking availability and scheduling property viewings."""

    async def free_slots(
        self,
        earliest: date,
        latest: date,
        preferred_part_of_day: str | None = None,
        now_dt: datetime | None = None,
    ) -> list[Slot]: ...

    async def book(
        self,
        slot_start_utc: datetime,
        booking: BookingRequest,
        now_dt: datetime | None = None,
    ) -> BookingResult: ...


class MockCalendar:
    """SQLite-backed mock calendar honoring UK business hours, notice, and daylight savings."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path
        self.tz = ZoneInfo(TIMEZONE)

    def _get_now(self, override_now: datetime | None = None) -> datetime:
        if override_now is not None:
            if override_now.tzinfo is None:
                return override_now.replace(tzinfo=self.tz)
            return override_now.astimezone(self.tz)
        return datetime.now(self.tz)

    async def free_slots(
        self,
        earliest: date,
        latest: date,
        preferred_part_of_day: str | None = None,
        now_dt: datetime | None = None,
    ) -> list[Slot]:
        """Generate open viewing slots respecting operating hours, notice, and prior bookings."""
        init_db(self.db_path)
        now = self._get_now(now_dt)
        min_start_time = now + timedelta(hours=MIN_NOTICE_HOURS)
        max_allowed_date = now.date() + timedelta(days=MAX_DAYS_AHEAD)

        actual_start_date = max(earliest, now.date())
        actual_end_date = min(latest, max_allowed_date)

        if actual_start_date > actual_end_date:
            return []

        # Retrieve all currently confirmed bookings across the window
        with get_connection(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT slot_start FROM bookings WHERE status = 'confirmed'")
            booked_utc_set = {row["slot_start"] for row in cursor.fetchall()}

        candidate_slots: list[tuple[datetime, Slot]] = []
        cur_date = actual_start_date

        while cur_date <= actual_end_date:
            if cur_date.weekday() in VIEWING_DAYS:
                # Generate slots starting each hour between START_HOUR and END_HOUR - 1
                for hour in range(VIEWING_START_HOUR, VIEWING_END_HOUR):
                    slot_local_start = datetime.combine(
                        cur_date, time(hour, 0), tzinfo=self.tz
                    )
                    slot_local_end = slot_local_start + timedelta(
                        minutes=VIEWING_LENGTH_MIN
                    )

                    # Check minimum notice
                    if slot_local_start < min_start_time:
                        continue

                    slot_start_utc = slot_local_start.astimezone(timezone.utc)
                    slot_end_utc = slot_local_end.astimezone(timezone.utc)
                    start_utc_iso = slot_start_utc.isoformat()
                    end_utc_iso = slot_end_utc.isoformat()

                    if start_utc_iso not in booked_utc_set:
                        spoken = format_spoken_slot(slot_local_start)
                        slot = Slot(
                            slot_start_utc=start_utc_iso,
                            slot_end_utc=end_utc_iso,
                            spoken=spoken,
                        )
                        candidate_slots.append((slot_local_start, slot))

            cur_date += timedelta(days=1)

        # Sort and apply morning/afternoon preference
        part = (preferred_part_of_day or "").lower().strip()

        def preference_key(item: tuple[datetime, Slot]) -> tuple[int, datetime]:
            dt, _ = item
            is_morning = dt.hour < 12
            if part == "morning":
                return (0 if is_morning else 1, dt)
            elif part == "afternoon":
                return (0 if not is_morning else 1, dt)
            return (0, dt)

        candidate_slots.sort(key=preference_key)
        selected = [slot for _, slot in candidate_slots[:MAX_SLOTS_OFFERED]]
        return selected

    async def book(
        self,
        slot_start_utc: datetime,
        booking: BookingRequest,
        now_dt: datetime | None = None,
    ) -> BookingResult:
        """Attempt to book a slot. Enforces unique slot constraint and queues outbox confirmation."""
        init_db(self.db_path)
        now = self._get_now(now_dt)

        # Ensure slot is UTC aware
        if slot_start_utc.tzinfo is None:
            slot_start_utc = slot_start_utc.replace(tzinfo=timezone.utc)
        else:
            slot_start_utc = slot_start_utc.astimezone(timezone.utc)

        slot_start_iso = slot_start_utc.isoformat()
        slot_end_utc = slot_start_utc + timedelta(minutes=VIEWING_LENGTH_MIN)
        slot_end_iso = slot_end_utc.isoformat()

        # Validate property exists
        prop = get_property(booking.property_id, self.db_path)
        if not prop:
            return BookingResult(
                status="failed",
                error_message=f"Property {booking.property_id} not found.",
            )

        # Check operating bounds in London time
        dt_london = slot_start_utc.astimezone(self.tz)
        if (
            dt_london.weekday() not in VIEWING_DAYS
            or dt_london.hour < VIEWING_START_HOUR
            or dt_london.hour >= VIEWING_END_HOUR
            or slot_start_utc
            < (now.astimezone(timezone.utc) + timedelta(hours=MIN_NOTICE_HOURS))
        ):
            return BookingResult(
                status="failed",
                error_message="The requested slot is outside of allowed viewing operating hours or notice window.",
            )

        spoken_time = format_spoken_slot(dt_london)
        booking_ref = "VW-" + "".join(
            random.choices(string.ascii_uppercase + string.digits, k=6)
        )
        created_at_iso = datetime.now(timezone.utc).isoformat()

        try:
            with get_connection(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO bookings (
                        booking_ref, property_id, slot_start, slot_end,
                        caller_name, caller_email, caller_phone, call_id,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)
                    """,
                    (
                        booking_ref,
                        booking.property_id,
                        slot_start_iso,
                        slot_end_iso,
                        booking.caller_name,
                        booking.caller_email,
                        booking.caller_phone,
                        booking.call_id,
                        created_at_iso,
                    ),
                )
                conn.commit()

            # Queue outbox confirmation email
            subject = f"Your viewing with MyCitiPad: {prop['address']}"
            body = (
                f"Dear {booking.caller_name},\n\n"
                f"Your viewing for {prop['address']} has been booked.\n\n"
                f"Date & Time: {spoken_time}\n"
                f"Booking Reference: {booking_ref}\n\n"
                "If you need to reschedule or cancel, please call +44 (0)20 3576 6575.\n\n"
                "Kind regards,\nMyCitiPad Lettings Team"
            )
            queue_outbox_message(
                kind="viewing_confirmation",
                to_email=booking.caller_email,
                subject=subject,
                body=body,
                db_path=self.db_path,
            )

            return BookingResult(
                status="confirmed",
                booking_ref=booking_ref,
                property_address=prop["address"],
                slot_start_utc=slot_start_iso,
                spoken_time=spoken_time,
                confirmation_queued=True,
            )

        except sqlite3.IntegrityError:
            # Slot is already booked (uq_booking_slot index fired)
            logger.info(
                "Slot %s already booked. Offering alternative slots.", slot_start_iso
            )
            alternatives = await self.free_slots(
                earliest=dt_london.date(),
                latest=dt_london.date() + timedelta(days=5),
                now_dt=now,
            )
            return BookingResult(
                status="slot_taken",
                alternative_slots=alternatives,
                error_message="That slot has just been taken by another caller.",
            )
        except Exception as exc:
            logger.error("Error booking viewing: %s", exc)
            return BookingResult(
                status="failed",
                error_message="Database error occurred while saving the booking.",
            )


# TODO: OutlookCalendar(CalendarProvider) via Microsoft Graph API
# In production, replace MockCalendar with OutlookCalendar using the Graph API
# (https://graph.microsoft.com/v1.0/me/calendar/getSchedule)
