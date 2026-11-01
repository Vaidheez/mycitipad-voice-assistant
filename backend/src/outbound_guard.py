"""Pre-dial guard for MyCitiPad outbound telephony.

Enforces compliance, privacy, do-not-call suppression, allowlist, attempt limits,
and UK polite calling hours before any outbound call is placed.
"""

import contextlib
import os
import uuid
import zoneinfo
from datetime import datetime, timezone

try:
    from config import (
        OUTBOUND_ALLOWLIST,
        OUTBOUND_HOURS_UK,
        OUTBOUND_IGNORE_HOURS,
        OUTBOUND_MAX_ATTEMPTS,
        TIMEZONE,
        TWILIO_ACCOUNT_SID,
        TWILIO_AUTH_TOKEN,
        TWILIO_FROM_NUMBER,
    )
    from db import (
        count_attempts,
        create_outbound,
        get_connection,
        init_db,
        is_suppressed,
        is_valid_e164,
        normalize_phone,
    )
except ImportError:
    from .config import (
        OUTBOUND_ALLOWLIST,
        OUTBOUND_HOURS_UK,
        OUTBOUND_IGNORE_HOURS,
        OUTBOUND_MAX_ATTEMPTS,
        TIMEZONE,
        TWILIO_ACCOUNT_SID,
        TWILIO_AUTH_TOKEN,
        TWILIO_FROM_NUMBER,
    )
    from .db import (
        count_attempts,
        create_outbound,
        get_connection,
        init_db,
        is_suppressed,
        is_valid_e164,
        normalize_phone,
    )


def mask_phone(phone: str | None) -> str:
    """Mask phone number preserving only the last 4 digits (e.g. ***45580)."""
    if not phone:
        return "****"
    clean = str(phone).strip()
    if len(clean) <= 4:
        return "****"
    return f"***{clean[-4:]}"


def check_can_call(
    to_phone: str,
    purpose: str,
    ref: str | None = None,
    now: datetime | None = None,
    db_path: str | None = None,
    skip_attempts: bool = False,
    record_blocked: bool = False,
    outbound_id: str | None = None,
) -> tuple[bool, str]:
    """Check whether an outbound call can be placed.

    Checks in strict order:
    1. Twilio credentials or from-number missing
    2. Number not valid E.164
    3. Number not on OUTBOUND_ALLOWLIST (when set)
    4. Number on suppression_list
    5. No matching booking or callback record for that number and reference
       (skipped for smoke_test)
    6. Attempts already at OUTBOUND_MAX_ATTEMPTS
    7. Outside OUTBOUND_HOURS_UK in Europe/London (unless OUTBOUND_IGNORE_HOURS)
    8. Sunday in Europe/London (unless OUTBOUND_IGNORE_HOURS)

    Returns:
        (ok, reason): (True, "allowed") or (False, reason_description)
    """
    # 1. Twilio credentials or from-number missing
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", TWILIO_ACCOUNT_SID)
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", TWILIO_AUTH_TOKEN)
    from_number = os.environ.get("TWILIO_FROM_NUMBER", TWILIO_FROM_NUMBER)

    if not account_sid or not auth_token or not from_number:
        reason = (
            "Twilio credentials (ACCOUNT_SID, AUTH_TOKEN, FROM_NUMBER) not configured"
        )
        _maybe_record_blocked(
            record_blocked, outbound_id, purpose, to_phone, ref, reason, db_path
        )
        return False, reason

    # 2. Number not valid E.164
    raw_phone = (to_phone or "").strip()
    clean_phone = normalize_phone(raw_phone)
    if not clean_phone or not is_valid_e164(clean_phone):
        reason = f"Recipient phone '{mask_phone(raw_phone)}' is not valid E.164 format"
        _maybe_record_blocked(
            record_blocked, outbound_id, purpose, raw_phone, ref, reason, db_path
        )
        return False, reason

    # 3. Number not on OUTBOUND_ALLOWLIST (when set)
    if "MCP_OUTBOUND_ALLOWLIST" in os.environ:
        allowlist_str = os.environ["MCP_OUTBOUND_ALLOWLIST"]
        allowlist = [
            normalize_phone(p.strip()) for p in allowlist_str.split(",") if p.strip()
        ]
    else:
        allowlist = [normalize_phone(p) for p in OUTBOUND_ALLOWLIST if p]

    if allowlist and clean_phone not in allowlist:
        reason = (
            f"Recipient phone '{mask_phone(clean_phone)}' not on outbound allowlist"
        )
        _maybe_record_blocked(
            record_blocked, outbound_id, purpose, clean_phone, ref, reason, db_path
        )
        return False, reason

    # 4. Number on suppression_list (do-not-call)
    if is_suppressed(clean_phone, db_path=db_path):
        reason = f"Recipient phone '{mask_phone(clean_phone)}' is on suppression list (opted out)"
        _maybe_record_blocked(
            record_blocked, outbound_id, purpose, clean_phone, ref, reason, db_path
        )
        return False, reason

    # 5. Matching booking or callback record (skip for smoke_test)
    if purpose == "smoke_test":
        pass
    elif purpose == "viewing_confirmation":
        if not ref:
            reason = "Missing booking reference for viewing_confirmation"
            _maybe_record_blocked(
                record_blocked, outbound_id, purpose, clean_phone, ref, reason, db_path
            )
            return False, reason
        if not _has_matching_booking(clean_phone, ref, db_path=db_path):
            reason = f"No matching viewing booking '{ref}' found for phone {mask_phone(clean_phone)}"
            _maybe_record_blocked(
                record_blocked, outbound_id, purpose, clean_phone, ref, reason, db_path
            )
            return False, reason
    elif purpose == "callback_followup":
        if not ref:
            reason = "Missing callback_id for callback_followup"
            _maybe_record_blocked(
                record_blocked, outbound_id, purpose, clean_phone, ref, reason, db_path
            )
            return False, reason
        if not _has_matching_callback(clean_phone, ref, db_path=db_path):
            reason = f"No matching queued callback request '{ref}' found for phone {mask_phone(clean_phone)}"
            _maybe_record_blocked(
                record_blocked, outbound_id, purpose, clean_phone, ref, reason, db_path
            )
            return False, reason
    else:
        reason = f"Invalid outbound purpose '{purpose}'"
        _maybe_record_blocked(
            record_blocked, outbound_id, purpose, clean_phone, ref, reason, db_path
        )
        return False, reason

    # 6. Attempts already at OUTBOUND_MAX_ATTEMPTS
    if not skip_attempts:
        booking_ref = ref if purpose == "viewing_confirmation" else None
        callback_id = ref if purpose == "callback_followup" else None
        attempts = count_attempts(
            purpose=purpose,
            to_phone=clean_phone,
            booking_ref=booking_ref,
            callback_id=callback_id,
            db_path=db_path,
        )
        if attempts >= OUTBOUND_MAX_ATTEMPTS:
            reason = (
                f"Maximum attempt limit ({OUTBOUND_MAX_ATTEMPTS}) reached for {purpose}"
            )
            _maybe_record_blocked(
                record_blocked, outbound_id, purpose, clean_phone, ref, reason, db_path
            )
            return False, reason

    # 7 & 8: UK Polite Hours (09:00 - 20:00, Mon-Sat)
    if "MCP_OUTBOUND_IGNORE_HOURS" in os.environ:
        ignore_hours = os.environ["MCP_OUTBOUND_IGNORE_HOURS"].lower() in (
            "true",
            "1",
            "yes",
        )
    else:
        ignore_hours = OUTBOUND_IGNORE_HOURS

    if not ignore_hours:
        uk_tz = zoneinfo.ZoneInfo(TIMEZONE)
        if now is None:
            now_uk = datetime.now(uk_tz)
        elif now.tzinfo is None:
            now_uk = now.replace(tzinfo=timezone.utc).astimezone(uk_tz)
        else:
            now_uk = now.astimezone(uk_tz)

        # 7. Outside calling hours (09:00 to 20:00)
        start_hour, end_hour = OUTBOUND_HOURS_UK
        if not (start_hour <= now_uk.hour < end_hour):
            reason = (
                f"Outside UK calling hours ({start_hour:02d}:00-{end_hour:02d}:00 UK). "
                f"Current UK time: {now_uk.strftime('%H:%M %Z')}"
            )
            _maybe_record_blocked(
                record_blocked, outbound_id, purpose, clean_phone, ref, reason, db_path
            )
            return False, reason

        # 8. Sunday
        if now_uk.weekday() == 6:  # 6 = Sunday
            reason = "Outbound calls prohibited on Sundays in the UK"
            _maybe_record_blocked(
                record_blocked, outbound_id, purpose, clean_phone, ref, reason, db_path
            )
            return False, reason

    return True, "allowed"


def _maybe_record_blocked(
    record_blocked: bool,
    outbound_id: str | None,
    purpose: str,
    to_phone: str,
    ref: str | None,
    reason: str,
    db_path: str | None,
) -> None:
    """Record a blocked outbound call row if requested."""
    if not record_blocked:
        return
    oid = outbound_id or str(uuid.uuid4())
    booking_ref = ref if purpose == "viewing_confirmation" else None
    callback_id = ref if purpose == "callback_followup" else None
    with contextlib.suppress(Exception):
        create_outbound(
            outbound_id=oid,
            purpose=purpose,
            to_phone=to_phone,
            booking_ref=booking_ref,
            callback_id=callback_id,
            status="blocked",
            failure_reason=reason,
            db_path=db_path,
        )


def _has_matching_booking(phone: str, booking_ref: str, db_path: str | None) -> bool:
    """Check if confirmed viewing exists for booking_ref and phone."""
    init_db(db_path)
    clean_phone = normalize_phone(phone) or phone
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT caller_phone FROM bookings
            WHERE booking_ref = ? AND status = 'confirmed'
            """,
            (booking_ref,),
        )
        row = cur.fetchone()
        if not row:
            return False
        b_phone = normalize_phone(row["caller_phone"])
        return b_phone == clean_phone


def _has_matching_callback(phone: str, callback_id: str, db_path: str | None) -> bool:
    """Check if queued callback request exists for callback_id and phone."""
    init_db(db_path)
    clean_phone = normalize_phone(phone) or phone
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT phone FROM callback_requests
            WHERE callback_id = ? AND status = 'queued'
            """,
            (callback_id,),
        )
        row = cur.fetchone()
        if not row:
            return False
        c_phone = normalize_phone(row["phone"])
        return c_phone == clean_phone
