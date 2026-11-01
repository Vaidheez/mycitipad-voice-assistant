"""Tests for MyCitiPad receptionist domain function tools.

Covers all 13 Phase 4 requirements:
1. find_property matches, suppresses prices, handles nonsense.
2. find_property flags let_subject_to_references.
3. Slot generation respects days, hours, notice, max window, and BST/GMT transition.
4. check_viewing_availability slot limits, spoken text, and morning/afternoon preference.
5. book_viewing confirms, queues outbox message, creates booking record.
6. Double booking prevention via unique constraint and alternative slots return.
7. book_viewing rejection of invalid slots or malformed email.
8. save_call_record required fields validation and currency normalisation ('5 lakh' -> 500000).
9. Emergency partial record save with complete=false.
10. transfer_to_human availability toggling and handover_requests audit logging.
11. request_callback UK phone validation and omission of time estimates.
12. MCP_SIMULATE_FAILURE graceful failure messages across all tools without raising.
13. Timeout handling fallback within timeout duration.
"""

import asyncio
import contextlib
import json
import os
import tempfile
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import config
from agent import Assistant
from calendar_provider import MockCalendar
from db import (
    get_connection,
    init_db,
    start_call_log,
)
from memory_tools import SessionState
from tools import (
    FAILURE_MESSAGES,
    _execute_with_safety,
    normalize_pounds,
)


@pytest.fixture
def temp_db(monkeypatch):
    """Provide isolated temporary database seeded with properties."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    monkeypatch.setenv("MCP_DB_PATH", db_path)
    init_db(db_path)
    yield db_path
    with contextlib.suppress(OSError):
        os.remove(db_path)


@pytest.mark.asyncio
async def test_1_find_property_search_and_price_omission(temp_db):
    """Test 1: Matches 'fisher close' and 'Fisher Close SE16', no price in result, 'no match' on nonsense."""
    assistant = Assistant()

    # Match lowercase
    res1 = await assistant.find_property(query="fisher close")
    data1 = json.loads(res1)
    assert len(data1) > 0
    assert any("Fisher Close" in d["address"] for d in data1)

    # Strictly confirm no price field or value is exposed to the model
    assert "price" not in res1.lower()
    for d in data1:
        assert "price_gbp" not in d
        assert "price" not in d

    # Match address with postcode area
    res2 = await assistant.find_property(query="Fisher Close SE16")
    data2 = json.loads(res2)
    assert len(data2) > 0
    assert any("Fisher Close" in d["address"] for d in data2)

    # Nonsense returns helpful guidance without hallucinated properties
    res3 = await assistant.find_property(query="NonExistentRoad999 SE99")
    assert "no matching property" in res3.lower() or "not found" in res3.lower()


@pytest.mark.asyncio
async def test_2_find_property_flags_let_subject_to_references(temp_db):
    """Test 2: Flags 'let_subject_to_references' and informs that a viewing cannot be booked."""
    assistant = Assistant()
    res = await assistant.find_property(query="Fisher Close")
    data = json.loads(res)
    item = next(d for d in data if "Fisher Close" in d["address"])

    assert item["status"] == "let_subject_to_references"
    assert "not available for viewings" in item.get("note", "").lower()


@pytest.mark.asyncio
async def test_3_slot_generation_days_hours_notice_and_dst_change(temp_db):
    """Test 3: Slot generation respects days, hours, notice, max window, and BST/GMT transition."""
    cal = MockCalendar(temp_db)
    tz_london = ZoneInfo("Europe/London")

    # In 2025, clock change is Sunday 26 October (BST -> GMT)
    # Frozen time: Wednesday 22 October 2025, 09:00 London
    now_frozen = datetime(2025, 10, 22, 9, 0, tzinfo=tz_london)

    # Friday 24 October 2025 is BST (UTC+1)
    slots_bst = await cal.free_slots(
        earliest=date(2025, 10, 24),
        latest=date(2025, 10, 24),
        now_dt=now_frozen,
    )
    assert len(slots_bst) > 0
    # First slot at 10:00 London BST must be 09:00:00Z in UTC
    first_bst = slots_bst[0]
    assert first_bst.slot_start_utc.startswith("2025-10-24T09:00:00")
    assert (
        "Friday the twenty-fourth of October at ten in the morning" in first_bst.spoken
    )

    # Sunday 26 October should have NO slots (viewings Mon-Sat only, Sunday=6 excluded)
    slots_sunday = await cal.free_slots(
        earliest=date(2025, 10, 26),
        latest=date(2025, 10, 26),
        now_dt=now_frozen,
    )
    assert len(slots_sunday) == 0

    # Tuesday 28 October 2025 is GMT (UTC+0)
    slots_gmt = await cal.free_slots(
        earliest=date(2025, 10, 28),
        latest=date(2025, 10, 28),
        now_dt=now_frozen,
    )
    assert len(slots_gmt) > 0
    # First slot at 10:00 London GMT must be 10:00:00Z in UTC
    first_gmt = slots_gmt[0]
    assert first_gmt.slot_start_utc.startswith("2025-10-28T10:00:00")
    assert (
        "Tuesday the twenty-eighth of October at ten in the morning" in first_gmt.spoken
    )

    # Notice period check: slots within MIN_NOTICE_HOURS (2h) are rejected
    # Frozen at 09:30 on Friday 24 Oct -> 10:00 slot is within 2h notice and must be omitted
    now_short_notice = datetime(2025, 10, 24, 9, 30, tzinfo=tz_london)
    slots_notice = await cal.free_slots(
        earliest=date(2025, 10, 24),
        latest=date(2025, 10, 24),
        now_dt=now_short_notice,
    )
    assert all(
        not s.slot_start_utc.startswith("2025-10-24T09:00:00") for s in slots_notice
    )

    # Maximum days ahead check: dates beyond MAX_DAYS_AHEAD (14 days) return no slots
    slots_far = await cal.free_slots(
        earliest=date(2025, 11, 20),
        latest=date(2025, 11, 25),
        now_dt=now_frozen,
    )
    assert len(slots_far) == 0


@pytest.mark.asyncio
async def test_4_check_viewing_availability_limits_and_preference(temp_db):
    """Test 4: check_viewing_availability returns at most MAX_SLOTS_OFFERED with spoken text and part_of_day."""
    with get_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT property_id FROM properties WHERE department = 'lettings' AND status = 'available' LIMIT 1"
        )
        prop_id = cursor.fetchone()["property_id"]

    assistant = Assistant()
    now_frozen = datetime(2025, 11, 4, 9, 0, tzinfo=ZoneInfo("Europe/London"))
    assistant.calendar._get_now = lambda override_now=None: override_now or now_frozen

    # Morning preference
    res_morn = await assistant.check_viewing_availability(
        property_id=prop_id,
        earliest_date="2025-11-06",
        latest_date="2025-11-10",
        part_of_day="morning",
    )
    slots_morn = json.loads(res_morn)
    assert 0 < len(slots_morn) <= config.MAX_SLOTS_OFFERED
    assert all("morning" in s["spoken"].lower() for s in slots_morn)

    # Afternoon preference
    res_aft = await assistant.check_viewing_availability(
        property_id=prop_id,
        earliest_date="2025-11-06",
        latest_date="2025-11-10",
        part_of_day="afternoon",
    )
    slots_aft = json.loads(res_aft)
    assert 0 < len(slots_aft) <= config.MAX_SLOTS_OFFERED
    assert all(
        "afternoon" in s["spoken"].lower() or "noon" in s["spoken"].lower()
        for s in slots_aft
    )


@pytest.mark.asyncio
async def test_5_book_viewing_confirmed_and_outbox(temp_db):
    """Test 5: book_viewing confirms free slot, creates booking row & outbox row with confirmation_queued=true."""
    with get_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT property_id, address FROM properties WHERE department = 'lettings' AND status = 'available' LIMIT 1"
        )
        row = cursor.fetchone()
        prop_id, address = row["property_id"], row["address"]

    assistant = Assistant()
    now_frozen = datetime(2025, 11, 4, 9, 0, tzinfo=ZoneInfo("Europe/London"))
    assistant.calendar._get_now = lambda override_now=None: override_now or now_frozen

    slot_utc = datetime(2025, 11, 6, 11, 0, tzinfo=timezone.utc).isoformat()

    res = await assistant.book_viewing(
        property_id=prop_id,
        slot_start=slot_utc,
        name="Sarah Connor",
        email="sarah@example.co.uk",
        phone="07700 900111",
    )
    data = json.loads(res)
    assert data["status"] == "confirmed"
    assert data["confirmation_queued"] is True
    assert data["booking_ref"].startswith("VW-")
    assert "November" in data["spoken_time"]

    # Verify database booking record
    with get_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bookings WHERE booking_ref = ?", (data["booking_ref"],)
        )
        b_row = cursor.fetchone()
        assert b_row is not None
        assert b_row["caller_name"] == "Sarah Connor"
        assert b_row["status"] == "confirmed"

        # Verify queued outbox message
        cursor.execute(
            "SELECT * FROM outbox WHERE to_email = ?", ("sarah@example.co.uk",)
        )
        o_row = cursor.fetchone()
        assert o_row is not None
        assert o_row["kind"] == "viewing_confirmation"
        assert data["booking_ref"] in o_row["body"]
        assert address in o_row["subject"]


@pytest.mark.asyncio
async def test_6_double_booking_prevention(temp_db):
    """Test 6: Booking same slot twice returns slot_taken with alternatives, exactly one row confirmed."""
    with get_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT property_id FROM properties WHERE department = 'lettings' AND status = 'available' LIMIT 1"
        )
        prop_id = cursor.fetchone()["property_id"]

    assistant = Assistant()
    now_frozen = datetime(2025, 11, 4, 9, 0, tzinfo=ZoneInfo("Europe/London"))
    assistant.calendar._get_now = lambda override_now=None: override_now or now_frozen

    slot_utc = datetime(2025, 11, 6, 14, 0, tzinfo=timezone.utc).isoformat()

    # First booking succeeds
    res1 = await assistant.book_viewing(
        property_id=prop_id,
        slot_start=slot_utc,
        name="First Caller",
        email="first@example.co.uk",
    )
    assert json.loads(res1)["status"] == "confirmed"

    # Second booking on the exact same slot start must return slot_taken
    res2 = await assistant.book_viewing(
        property_id=prop_id,
        slot_start=slot_utc,
        name="Second Caller",
        email="second@example.co.uk",
    )
    data2 = json.loads(res2)
    assert data2["status"] == "slot_taken"
    assert "alternative_slots" in data2
    assert isinstance(data2["alternative_slots"], list)

    # Exactly 1 confirmed row exists in DB
    with get_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM bookings WHERE slot_start = ? AND status = 'confirmed'",
            (slot_utc,),
        )
        assert cursor.fetchone()["cnt"] == 1


@pytest.mark.asyncio
async def test_7_book_viewing_rejects_unoffered_slots_and_bad_email(temp_db):
    """Test 7: book_viewing rejects out-of-bounds slot and malformed email."""
    with get_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT property_id FROM properties WHERE department = 'lettings' AND status = 'available' LIMIT 1"
        )
        prop_id = cursor.fetchone()["property_id"]

    assistant = Assistant()
    now_frozen = datetime(2025, 11, 4, 9, 0, tzinfo=ZoneInfo("Europe/London"))
    assistant.calendar._get_now = lambda override_now=None: override_now or now_frozen

    valid_slot = datetime(2025, 11, 6, 14, 0, tzinfo=timezone.utc).isoformat()

    # Malformed email
    res_bad_email = await assistant.book_viewing(
        property_id=prop_id,
        slot_start=valid_slot,
        name="Test Caller",
        email="not-an-email",
    )
    assert "Error:" in res_bad_email and "valid email" in res_bad_email

    # Out of hours (e.g. 03:00 UTC)
    ooh_slot = datetime(2025, 11, 6, 3, 0, tzinfo=timezone.utc).isoformat()
    res_ooh = await assistant.book_viewing(
        property_id=prop_id,
        slot_start=ooh_slot,
        name="Test Caller",
        email="test@example.com",
    )
    data_ooh = json.loads(res_ooh)
    assert data_ooh["status"] == "failed"


@pytest.mark.asyncio
async def test_8_save_call_record_validation_and_currency_normalisation(temp_db):
    """Test 8: Validates required fields, normalises '5 lakh' and '500,000' to 500000 GBP integer."""
    call_id = start_call_log(temp_db)
    assistant = Assistant(session_state=SessionState(call_id=call_id))

    # Incomplete input returns missing field list
    res_inc = await assistant.save_call_record(
        category="lettings_enquiry",
        details={"full_name": "John Smith"},
        outcome="in_progress",
    )
    data_inc = json.loads(res_inc)
    assert data_inc["status"] == "missing_fields"
    assert "email" in data_inc["missing"]
    assert "move_in_date" in data_inc["missing"]

    # Complete input with '5 lakh'
    res_lakh = await assistant.save_call_record(
        category="lettings_enquiry",
        details={
            "full_name": "John Smith",
            "email": "john@example.com",
            "property": "1 Example Street",
            "move_in_date": "2025-12-01",
            "occupants": "1 person",
            "enquiry_for_self": "yes",
            "employment_status": "employed",
            "annual_income_gbp": "5 lakh",
            "needs_uk_guarantor": "no",
            "viewing_availability": "Friday afternoons",
        },
        outcome="enquiry_logged",
    )
    data_lakh = json.loads(res_lakh)
    assert data_lakh["status"] == "saved"

    # Verify normalisation in SQLite
    with get_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT details FROM call_logs WHERE call_id = ?", (call_id,))
        saved_details = json.loads(cursor.fetchone()["details"])
        assert saved_details["annual_income_gbp"] == 500000

    # Test direct normalisation helper
    assert normalize_pounds("500,000") == 500000
    assert normalize_pounds("£750,000") == 750000
    assert normalize_pounds("5 lakh") == 500000


@pytest.mark.asyncio
async def test_9_emergency_partial_save_when_incomplete(temp_db):
    """Test 9: An emergency record can be saved with complete=false even if fields are missing."""
    call_id = start_call_log(temp_db)
    assistant = Assistant(session_state=SessionState(call_id=call_id))

    res = await assistant.save_call_record(
        category="emergency",
        details={
            "full_name": "Bob Emergency",
            "contact_number": "07700 900999",
            "requirement": "Smelled gas in kitchen, called gas board",
        },
        outcome="emergency_logged",
        complete=False,
    )
    data = json.loads(res)
    assert data["status"] == "saved"
    assert data["category"] == "emergency"

    with get_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT category, outcome FROM call_logs WHERE call_id = ?", (call_id,)
        )
        row = cursor.fetchone()
        assert row["category"] == "emergency"
        assert row["outcome"] == "emergency_logged"


@pytest.mark.asyncio
async def test_10_transfer_to_human_availability_and_audit(temp_db, monkeypatch):
    """Test 10: transfer_to_human returns unavailable by default and accepted when MCP_HUMAN_AVAILABLE=true."""
    call_id = start_call_log(temp_db)
    assistant = Assistant(session_state=SessionState(call_id=call_id))

    # Default unavailable
    monkeypatch.setattr(config, "HUMAN_AVAILABLE", False)
    monkeypatch.delenv("MCP_HUMAN_AVAILABLE", raising=False)
    res_unavail = await assistant.transfer_to_human(reason="Wants to speak to sales")
    data_unavail = json.loads(res_unavail)
    assert data_unavail["status"] == "unavailable"

    # Enabled
    monkeypatch.setenv("MCP_HUMAN_AVAILABLE", "true")
    res_avail = await assistant.transfer_to_human(reason="Emergency transfer")
    data_avail = json.loads(res_avail)
    assert data_avail["status"] == "accepted"

    # Handover rows recorded in both cases
    with get_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM handover_requests WHERE call_id = ? ORDER BY created_at ASC",
            (call_id,),
        )
        rows = cursor.fetchall()
        assert len(rows) == 2
        assert rows[0]["status"] == "unavailable"
        assert rows[1]["status"] == "accepted"


@pytest.mark.asyncio
async def test_11_request_callback_uk_numbers_and_no_time_promise(temp_db):
    """Test 11: request_callback accepts valid UK numbers, rejects invalid, never promises a time."""
    call_id = start_call_log(temp_db)
    assistant = Assistant(session_state=SessionState(call_id=call_id))

    # Valid UK mobile
    res1 = await assistant.request_callback(
        phone="07700 900123",
        name="Arthur Dent",
        reason="Viewing enquiry",
    )
    data1 = json.loads(res1)
    assert data1["status"] == "queued"
    assert data1["priority"] == "normal"
    # Never promise arrival or callback time
    assert "in 10 minutes" not in res1.lower()
    assert "within" not in res1.lower()

    # Valid UK landline
    res2 = await assistant.request_callback(
        phone="+44 20 7946 0000",
        name="Ford Prefect",
        reason="Emergency callback",
        urgent=True,
    )
    data2 = json.loads(res2)
    assert data2["status"] == "queued"
    assert data2["priority"] == "urgent"

    # Reject invalid number
    res_inv = await assistant.request_callback(
        phone="12345",
        name="Zaphod",
        reason="General",
    )
    assert "does not appear to be a complete" in res_inv

    with get_connection(temp_db) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM callback_requests WHERE call_id = ?",
            (call_id,),
        )
        assert cursor.fetchone()["cnt"] == 2


@pytest.mark.asyncio
async def test_12_mcp_simulate_failure_handling(temp_db, monkeypatch):
    """Test 12: For each MCP_SIMULATE_FAILURE flag, matching tool returns failure table string without raising."""
    assistant = Assistant()

    # properties -> find_property
    monkeypatch.setenv("MCP_SIMULATE_FAILURE", "properties")
    res_p = await assistant.find_property(query="Fisher Close")
    assert res_p == FAILURE_MESSAGES["find_property"]

    # calendar -> check_viewing_availability & book_viewing
    monkeypatch.setenv("MCP_SIMULATE_FAILURE", "calendar")
    res_c1 = await assistant.check_viewing_availability(
        property_id="demo-1", earliest_date="2025-11-06", latest_date="2025-11-10"
    )
    assert res_c1 == FAILURE_MESSAGES["check_viewing_availability"]

    res_c2 = await assistant.book_viewing(
        property_id="demo-1",
        slot_start="2025-11-06T10:00:00Z",
        name="A",
        email="a@b.com",
    )
    assert res_c2 == FAILURE_MESSAGES["book_viewing"]

    # records -> save_call_record
    monkeypatch.setenv("MCP_SIMULATE_FAILURE", "records")
    res_r = await assistant.save_call_record(category="lettings_enquiry", details={})
    assert res_r == FAILURE_MESSAGES["save_call_record"]

    # handover -> transfer_to_human
    monkeypatch.setenv("MCP_SIMULATE_FAILURE", "handover")
    res_h = await assistant.transfer_to_human(reason="Test")
    assert res_h == FAILURE_MESSAGES["transfer_to_human"]

    # callback -> request_callback
    monkeypatch.setenv("MCP_SIMULATE_FAILURE", "callback")
    res_cb = await assistant.request_callback(phone="07700900123")
    assert res_cb == FAILURE_MESSAGES["request_callback"]


@pytest.mark.asyncio
async def test_13_tool_timeout_handling():
    """Test 13: Tool exceeding timeout returns the failure string within roughly the timeout."""

    async def slow_operation():
        await asyncio.sleep(0.5)
        return "completed"

    res = await _execute_with_safety("find_property", slow_operation(), timeout_s=0.05)
    assert res == FAILURE_MESSAGES["find_property"]
