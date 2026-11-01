"""SQLite persistence for MyCitiPad call logs, caller profiles, properties, and bookings.

Separates business call records (stored automatically under call recording notice)
from persistent caller profiles (strictly requiring explicit verbal consent).
"""

import difflib
import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from seed import seed_properties
except ImportError:
    from .seed import seed_properties

logger = logging.getLogger("mycitipad.db")

DEFAULT_DB_PATH = "backend/data/mycitipad.db"


def get_db_path() -> str:
    """Return the database file path from environment or default."""
    path = os.getenv("MCP_DB_PATH")
    if not path:
        path = "data/mycitipad.db" if Path("src/agent.py").exists() else DEFAULT_DB_PATH
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return str(p)


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with row factory enabled."""
    target_path = db_path or get_db_path()
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_phone(phone: str | None) -> str | None:
    """Normalise phone numbers: remove punctuation/spaces, convert local UK to E.164, preserve leading +."""
    if not phone:
        return None
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
    if cleaned.startswith("+440"):
        cleaned = "+44" + cleaned[4:]
    elif cleaned.startswith("0") and not cleaned.startswith("+"):
        cleaned = "+44" + cleaned[1:]
    elif not cleaned.startswith("+") and len(cleaned) >= 10:
        cleaned = "+" + cleaned
    return cleaned if cleaned else None


def is_valid_e164(phone: str | None) -> bool:
    """Validate E.164 phone format (+ followed by 1 to 15 digits, starting 1-9)."""
    if not phone:
        return False
    return bool(re.match(r"^\+[1-9]\d{1,14}$", phone.strip()))


def normalize_email(email: str | None) -> str | None:
    """Normalise email to lowercase stripped string."""
    if not email:
        return None
    cleaned = email.strip().lower()
    return cleaned if cleaned else None


def init_db(db_path: str | None = None) -> None:
    """Initialise SQLite tables, indexes, and property seed data."""
    try:
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            # 1. Callers (Persistent Profiles)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS callers (
                    user_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT,
                    phone TEXT,
                    language_preference TEXT DEFAULT 'en-GB',
                    facts TEXT NOT NULL DEFAULT '{}',
                    consent_at TEXT NOT NULL,
                    last_interaction TEXT NOT NULL
                );
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_callers_email ON callers(email);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_callers_phone ON callers(phone);"
            )

            # 2. Call Logs (Business Records)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS call_logs (
                    call_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    category TEXT,
                    outcome TEXT,
                    details TEXT DEFAULT '{}',
                    transcript TEXT DEFAULT ''
                );
                """
            )

            # 3. Properties
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS properties (
                    property_id TEXT PRIMARY KEY,
                    address TEXT NOT NULL,
                    postcode_area TEXT,
                    department TEXT NOT NULL,
                    status TEXT NOT NULL,
                    bedrooms INTEGER,
                    price_gbp INTEGER,
                    source_url TEXT,
                    is_demo INTEGER NOT NULL DEFAULT 0
                );
                """
            )

            # 4. Bookings
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS bookings (
                    booking_ref TEXT PRIMARY KEY,
                    property_id TEXT NOT NULL REFERENCES properties(property_id),
                    slot_start TEXT NOT NULL,
                    slot_end TEXT NOT NULL,
                    caller_name TEXT NOT NULL,
                    caller_email TEXT NOT NULL,
                    caller_phone TEXT,
                    call_id TEXT,
                    status TEXT NOT NULL DEFAULT 'confirmed',
                    created_at TEXT NOT NULL
                );
                """
            )
            # Enforce single confirmed viewing per slot start time
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_booking_slot
                ON bookings(slot_start) WHERE status = 'confirmed';
                """
            )

            # 5. Handover Requests
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS handover_requests (
                    request_id TEXT PRIMARY KEY,
                    call_id TEXT,
                    reason TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

            # 6. Callback Requests
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS callback_requests (
                    callback_id TEXT PRIMARY KEY,
                    call_id TEXT,
                    name TEXT,
                    phone TEXT NOT NULL,
                    reason TEXT,
                    priority TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL
                );
                """
            )

            # 7. Outbox (Mock Email Queue)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    outbox_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    to_email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL
                );
                """
            )

            # 8. Suppression List (Do-Not-Call)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS suppression_list (
                    phone TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

            # 9. Outbound Calls
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_calls (
                    outbound_id TEXT PRIMARY KEY,
                    call_id TEXT,
                    purpose TEXT NOT NULL,
                    booking_ref TEXT,
                    callback_id TEXT,
                    to_phone TEXT NOT NULL,
                    twilio_call_sid TEXT,
                    status TEXT NOT NULL,
                    failure_reason TEXT,
                    attempt_no INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    ended_at TEXT
                );
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbound_phone ON outbound_calls(to_phone);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbound_booking ON outbound_calls(booking_ref);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbound_callback ON outbound_calls(callback_id);"
            )
            conn.commit()

            # Seed default properties
            seed_properties(conn)

            logger.info(
                "Database initialized successfully at %s", db_path or get_db_path()
            )
    except Exception as exc:
        logger.error("Failed to initialize database: %s", exc)
        raise


def find_caller(
    *,
    user_id: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    """Find a caller profile by user_id, email, or phone (checked in that order)."""
    clean_email = normalize_email(email)
    clean_phone = normalize_phone(phone)

    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            row = None

            if user_id:
                cursor.execute("SELECT * FROM callers WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()

            if not row and clean_email:
                cursor.execute("SELECT * FROM callers WHERE email = ?", (clean_email,))
                row = cursor.fetchone()

            if not row and clean_phone:
                phone_candidates = [clean_phone]
                if clean_phone.startswith("+44") and len(clean_phone) > 3:
                    phone_candidates.append("0" + clean_phone[3:])
                elif clean_phone.startswith("0") and len(clean_phone) > 1:
                    phone_candidates.append("+44" + clean_phone[1:])

                for candidate in phone_candidates:
                    cursor.execute(
                        "SELECT * FROM callers WHERE phone = ?", (candidate,)
                    )
                    row = cursor.fetchone()
                    if row:
                        break

            if not row:
                return None

            data = dict(row)
            try:
                data["facts"] = json.loads(data.get("facts", "{}"))
            except Exception:
                data["facts"] = {}
            return data
    except Exception as exc:
        logger.error("Error finding caller: %s", exc)
        raise


def upsert_caller(
    name: str,
    email: str | None,
    phone: str | None,
    language_preference: str | None,
    facts: dict[str, Any],
    user_id: str | None = None,
    db_path: str | None = None,
) -> str:
    """Create or update caller profile. Merges facts and updates last_interaction."""
    clean_email = normalize_email(email)
    clean_phone = normalize_phone(phone)
    lang = language_preference or "en-GB"
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        init_db(db_path)
        existing = find_caller(
            user_id=user_id, email=clean_email, phone=clean_phone, db_path=db_path
        )

        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            if existing:
                actual_user_id = existing["user_id"]
                merged_facts = existing.get("facts", {})
                if isinstance(facts, dict):
                    merged_facts.update(facts)

                cursor.execute(
                    """
                    UPDATE callers
                    SET name = ?,
                        email = COALESCE(?, email),
                        phone = COALESCE(?, phone),
                        language_preference = ?,
                        facts = ?,
                        last_interaction = ?
                    WHERE user_id = ?
                    """,
                    (
                        name,
                        clean_email,
                        clean_phone,
                        lang,
                        json.dumps(merged_facts),
                        now_iso,
                        actual_user_id,
                    ),
                )
                conn.commit()
                return actual_user_id
            else:
                new_user_id = user_id or str(uuid.uuid4())
                facts_payload = json.dumps(facts or {})

                cursor.execute(
                    """
                    INSERT INTO callers (
                        user_id, name, email, phone, language_preference,
                        facts, consent_at, last_interaction
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_user_id,
                        name,
                        clean_email,
                        clean_phone,
                        lang,
                        facts_payload,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
                return new_user_id
    except Exception as exc:
        logger.error("Error upserting caller: %s", exc)
        raise


def delete_caller(user_id: str, db_path: str | None = None) -> bool:
    """Delete a caller profile and detach user_id on their call logs."""
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE call_logs SET user_id = NULL WHERE user_id = ?", (user_id,)
            )
            cursor.execute("DELETE FROM callers WHERE user_id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0
    except Exception as exc:
        logger.error("Error deleting caller %s: %s", user_id, exc)
        raise


def start_call_log(db_path: str | None = None) -> str:
    """Create a new call log entry and return its call_id uuid string."""
    call_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO call_logs (call_id, started_at) VALUES (?, ?)",
                (call_id, started_at),
            )
            conn.commit()
    except Exception as exc:
        logger.error("Error starting call log: %s", exc)
        raise

    return call_id


def update_call_log(call_id: str, db_path: str | None = None, **fields: Any) -> None:
    """Update call log fields (user_id, ended_at, category, outcome, details, transcript)."""
    allowed_cols = {
        "user_id",
        "ended_at",
        "category",
        "outcome",
        "details",
        "transcript",
    }
    update_data = {k: v for k, v in fields.items() if k in allowed_cols}

    if not update_data:
        return

    if "details" in update_data and isinstance(update_data["details"], dict):
        update_data["details"] = json.dumps(update_data["details"])

    set_clause = ", ".join(f"{col} = ?" for col in update_data)
    values = [*list(update_data.values()), call_id]

    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE call_logs SET {set_clause} WHERE call_id = ?", values
            )
            conn.commit()
    except Exception as exc:
        logger.error("Error updating call log %s: %s", call_id, exc)
        raise


def append_transcript(
    call_id: str, speaker: str, text: str, db_path: str | None = None
) -> None:
    """Append a speech turn to the call transcript."""
    line = f"{speaker}: {text.strip()}\n"

    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE call_logs
                SET transcript = COALESCE(transcript, '') || ?
                WHERE call_id = ?
                """,
                (line, call_id),
            )
            conn.commit()
    except Exception as exc:
        logger.error("Error appending transcript for %s: %s", call_id, exc)
        raise


# =============================================================================
# Phase 4 Domain Helpers
# =============================================================================


def _clean_search_text(text: str) -> str:
    """Strip punctuation and lowercase text for fuzzy matching."""
    return re.sub(r"[^\w\s]", " ", text.lower()).strip()


def search_properties(
    query: str, department: str | None = None, db_path: str | None = None
) -> list[dict[str, Any]]:
    """Match properties by address or postcode area.

    Crucial: NEVER returns price_gbp in results to ensure model cannot quote prices.
    Returns up to 3 matches with property_id, address, department, and status.
    """
    clean_query = _clean_search_text(query)
    if not clean_query:
        return []

    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            if department and department.lower() in ("lettings", "sales"):
                cursor.execute(
                    """
                    SELECT property_id, address, postcode_area, department, status, bedrooms
                    FROM properties WHERE department = ?
                    """,
                    (department.lower(),),
                )
            else:
                cursor.execute(
                    """
                    SELECT property_id, address, postcode_area, department, status, bedrooms
                    FROM properties
                    """
                )
            rows = [dict(r) for r in cursor.fetchall()]

            # 1. Exact or substring matching
            direct_matches = []
            for r in rows:
                addr_clean = _clean_search_text(r["address"])
                post_clean = _clean_search_text(r["postcode_area"] or "")
                if (
                    clean_query in addr_clean
                    or addr_clean in clean_query
                    or (post_clean and post_clean in clean_query)
                ):
                    direct_matches.append(r)

            if direct_matches:
                return direct_matches[:3]

            # 2. Fuzzy matching with difflib
            address_map = {_clean_search_text(r["address"]): r for r in rows}
            close_keys = difflib.get_close_matches(
                clean_query, list(address_map.keys()), n=3, cutoff=0.5
            )
            return [address_map[k] for k in close_keys]
    except Exception as exc:
        logger.error("Error searching properties: %s", exc)
        raise


def get_property(property_id: str, db_path: str | None = None) -> dict[str, Any] | None:
    """Fetch property details without price_gbp."""
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT property_id, address, postcode_area, department, status, bedrooms
                FROM properties WHERE property_id = ?
                """,
                (property_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("Error getting property %s: %s", property_id, exc)
        raise


def create_handover_request(
    call_id: str, reason: str, status: str, db_path: str | None = None
) -> str:
    """Log an agent-to-human handover request."""
    request_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO handover_requests (request_id, call_id, reason, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (request_id, call_id, reason, status, now_iso),
            )
            conn.commit()
        return request_id
    except Exception as exc:
        logger.error("Error creating handover request: %s", exc)
        raise


def create_callback_request(
    call_id: str,
    name: str | None,
    phone: str,
    reason: str,
    priority: str = "normal",
    db_path: str | None = None,
) -> str:
    """Log a caller callback request."""
    callback_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    clean_phone = normalize_phone(phone) or phone
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO callback_requests (
                    callback_id, call_id, name, phone, reason, priority, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)
                """,
                (callback_id, call_id, name, clean_phone, reason, priority, now_iso),
            )
            conn.commit()
        return callback_id
    except Exception as exc:
        logger.error("Error creating callback request: %s", exc)
        raise


def queue_outbox_message(
    kind: str,
    to_email: str,
    subject: str,
    body: str,
    db_path: str | None = None,
) -> str:
    """Queue an automated email into outbox."""
    outbox_id = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()
    clean_email = normalize_email(to_email) or to_email
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO outbox (outbox_id, kind, to_email, subject, body, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'queued', ?)
                """,
                (outbox_id, kind, clean_email, subject, body, now_iso),
            )
            conn.commit()
        return outbox_id
    except Exception as exc:
        logger.error("Error queueing outbox message: %s", exc)
        raise


# =============================================================================
# Outbound Telephony Helpers
# =============================================================================


def is_suppressed(phone: str, db_path: str | None = None) -> bool:
    """Check if a phone number is on the suppression (do-not-call) list."""
    clean = normalize_phone(phone) or phone
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM suppression_list WHERE phone = ?", (clean,))
            return cur.fetchone() is not None
    except Exception as exc:
        logger.error("Error checking suppression for phone: %s", exc)
        return False


def add_suppression(phone: str, reason: str, db_path: str | None = None) -> None:
    """Add a phone number to the suppression list."""
    clean = normalize_phone(phone) or phone
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO suppression_list (phone, reason, created_at)
                VALUES (?, ?, ?)
                """,
                (clean, reason, now_iso),
            )
            conn.commit()
            logger.info("Added %s to suppression list (reason=%s)", clean[-4:], reason)
    except Exception as exc:
        logger.error("Error adding suppression: %s", exc)
        raise


def create_outbound(
    outbound_id: str,
    purpose: str,
    to_phone: str,
    booking_ref: str | None = None,
    callback_id: str | None = None,
    attempt_no: int = 1,
    status: str = "dialling",
    failure_reason: str | None = None,
    twilio_call_sid: str | None = None,
    call_id: str | None = None,
    db_path: str | None = None,
) -> None:
    """Create a new outbound call record."""
    clean = normalize_phone(to_phone) or to_phone
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO outbound_calls (
                    outbound_id, call_id, purpose, booking_ref, callback_id,
                    to_phone, twilio_call_sid, status, failure_reason, attempt_no,
                    created_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    outbound_id,
                    call_id,
                    purpose,
                    booking_ref,
                    callback_id,
                    clean,
                    twilio_call_sid,
                    status,
                    failure_reason,
                    attempt_no,
                    now_iso,
                ),
            )
            conn.commit()
    except Exception as exc:
        logger.error("Error creating outbound call record: %s", exc)
        raise


def update_outbound(
    outbound_id: str,
    db_path: str | None = None,
    **kwargs: Any,
) -> None:
    """Update fields on an outbound call record."""
    if not kwargs:
        return
    valid_cols = {
        "call_id",
        "purpose",
        "booking_ref",
        "callback_id",
        "to_phone",
        "twilio_call_sid",
        "status",
        "failure_reason",
        "attempt_no",
        "ended_at",
    }
    updates = []
    values = []
    for k, v in kwargs.items():
        if k in valid_cols:
            updates.append(f"{k} = ?")
            values.append(v)
    if not updates:
        return
    values.append(outbound_id)
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cur = conn.cursor()
            query = (
                f"UPDATE outbound_calls SET {', '.join(updates)} WHERE outbound_id = ?"
            )
            cur.execute(query, tuple(values))
            conn.commit()
    except Exception as exc:
        logger.error("Error updating outbound call record: %s", exc)
        raise


def count_attempts(
    purpose: str,
    to_phone: str,
    booking_ref: str | None = None,
    callback_id: str | None = None,
    db_path: str | None = None,
) -> int:
    """Count prior outbound call attempts (excluding blocked calls)."""
    clean = normalize_phone(to_phone) or to_phone
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cur = conn.cursor()
            if booking_ref:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM outbound_calls
                    WHERE booking_ref = ? AND status != 'blocked'
                    """,
                    (booking_ref,),
                )
            elif callback_id:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM outbound_calls
                    WHERE callback_id = ? AND status != 'blocked'
                    """,
                    (callback_id,),
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM outbound_calls
                    WHERE to_phone = ? AND purpose = ? AND status != 'blocked'
                    """,
                    (clean, purpose),
                )
            row = cur.fetchone()
            return row[0] if row else 0
    except Exception as exc:
        logger.error("Error counting attempts: %s", exc)
        return 0


def get_outbound_call(outbound_id: str, db_path: str | None = None) -> dict | None:
    """Fetch an outbound call record by ID."""
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM outbound_calls WHERE outbound_id = ?",
                (outbound_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        logger.error("Error fetching outbound call: %s", exc)
        return None


def get_recent_dialling_call(
    phone: str,
    within_seconds: float = 120,
    db_path: str | None = None,
) -> dict | None:
    """Check if a call to this phone is currently 'dialling' and was created within within_seconds.

    Returns the call dict if found, None otherwise.
    """
    clean = normalize_phone(phone) or phone
    try:
        init_db(db_path)
        with get_connection(db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM outbound_calls
                WHERE to_phone = ? AND status = 'dialling'
                ORDER BY created_at DESC LIMIT 1
                """,
                (clean,),
            )
            row = cur.fetchone()
            if not row:
                return None
            record = dict(row)
            created_at_str = record.get("created_at")
            if not created_at_str:
                return None
            try:
                created_dt = datetime.fromisoformat(created_at_str)
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                age_s = (now - created_dt).total_seconds()
                if 0 <= age_s <= within_seconds:
                    record["age_seconds"] = age_s
                    return record
            except Exception as parse_err:
                logger.warning(
                    "Error parsing created_at '%s': %s", created_at_str, parse_err
                )
            return None
    except Exception as exc:
        logger.error("Error checking recent dialling call: %s", exc)
        return None
