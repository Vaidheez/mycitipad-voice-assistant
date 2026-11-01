"""Unit tests for Phase 6 Stage B: Outbound Telephony Foundations.

Tests:
- Pre-dial guard (8 blocking conditions in strict order + UK polite hours + DST)
- start_outbound_call with mocked LiveKit and Twilio clients
- watch_call_status mapping and room deletion for non-answered states
- Error sanitization (no Auth Tokens, Account SIDs, or full phone numbers)
- hang_up reliability (never raises on failures)
"""

import asyncio
import zoneinfo
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit import rtc
from livekit.agents.job import wait_for_participant
from twilio.base.exceptions import TwilioRestException

from db import (
    add_suppression,
    create_outbound,
    get_connection,
    get_recent_dialling_call,
    init_db,
)
from outbound_guard import check_can_call, mask_phone
from telephony_twilio import (
    get_call_notifications,
    get_twilio_client,
    hang_up,
    sanitize_error,
    start_outbound_call,
    watch_call_status,
)

DUMMY_SID = "AC" + "0" * 32
DUMMY_TOKEN = "f" * 32


@pytest.fixture
def temp_db(tmp_path):
    """Provide a clean temporary SQLite database."""
    db_file = str(tmp_path / "test_outbound.db")
    init_db(db_file)
    return db_file


# =============================================================================
# 1. Guard Tests (8 blocking conditions in order)
# =============================================================================


def test_guard_condition_1_missing_twilio_creds(temp_db, monkeypatch):
    """Guard blocks if Twilio credentials or from-number are missing."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "")

    ok, reason = check_can_call(
        to_phone="+447123456789",
        purpose="smoke_test",
        db_path=temp_db,
    )
    assert not ok
    assert "Twilio credentials" in reason


def test_guard_condition_2_invalid_e164(temp_db, monkeypatch):
    """Guard blocks if recipient phone number is not valid E.164."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", DUMMY_SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", DUMMY_TOKEN)
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+17372508034")

    # Invalid numbers (letters, too short, empty, starting with +0)
    invalid_phones = ["not_a_phone", "+012345678", "12345", "+", ""]
    for p in invalid_phones:
        ok, reason = check_can_call(
            to_phone=p,
            purpose="smoke_test",
            db_path=temp_db,
        )
        assert not ok
        assert "not valid E.164" in reason


def test_guard_condition_3_not_on_allowlist(temp_db, monkeypatch):
    """Guard blocks if number is not on the configured allowlist."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", DUMMY_SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", DUMMY_TOKEN)
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+17372508034")
    monkeypatch.setenv("MCP_OUTBOUND_ALLOWLIST", "+447999888777,+447111222333")

    ok, reason = check_can_call(
        to_phone="+447000111222",
        purpose="smoke_test",
        db_path=temp_db,
    )
    assert not ok
    assert "not on outbound allowlist" in reason


def test_guard_condition_4_suppression_list(temp_db, monkeypatch):
    """Guard blocks if number has opted out / is on suppression list."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", DUMMY_SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", DUMMY_TOKEN)
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+17372508034")
    monkeypatch.setenv("MCP_OUTBOUND_ALLOWLIST", "+447123456789")

    add_suppression("+447123456789", reason="opt_out", db_path=temp_db)

    ok, reason = check_can_call(
        to_phone="+447123456789",
        purpose="smoke_test",
        db_path=temp_db,
    )
    assert not ok
    assert "suppression list" in reason


def test_guard_condition_5_no_matching_booking_or_callback(temp_db, monkeypatch):
    """Guard blocks if no matching booking or callback record exists (skipped for smoke_test)."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", DUMMY_SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", DUMMY_TOKEN)
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+17372508034")
    monkeypatch.setenv("MCP_OUTBOUND_ALLOWLIST", "+447123456789")
    monkeypatch.setenv("MCP_OUTBOUND_IGNORE_HOURS", "true")

    # Smoke test skips record check
    ok_smoke, _ = check_can_call(
        to_phone="+447123456789",
        purpose="smoke_test",
        db_path=temp_db,
    )
    assert ok_smoke

    # viewing_confirmation requires matching booking
    ok_viewing, reason_v = check_can_call(
        to_phone="+447123456789",
        purpose="viewing_confirmation",
        ref="VW-NONEXISTENT",
        db_path=temp_db,
    )
    assert not ok_viewing
    assert "No matching viewing booking" in reason_v

    # callback_followup requires matching callback request
    ok_cb, reason_cb = check_can_call(
        to_phone="+447123456789",
        purpose="callback_followup",
        ref="CB-NONEXISTENT",
        db_path=temp_db,
    )
    assert not ok_cb
    assert "No matching queued callback request" in reason_cb


def test_guard_condition_6_attempt_limit_reached(temp_db, monkeypatch):
    """Guard blocks if maximum attempts (1) already reached."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", DUMMY_SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", DUMMY_TOKEN)
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+17372508034")
    monkeypatch.setenv("MCP_OUTBOUND_ALLOWLIST", "+447123456789")
    monkeypatch.setenv("MCP_OUTBOUND_IGNORE_HOURS", "true")

    # Record 1 prior attempt
    create_outbound(
        outbound_id="prior-1",
        purpose="smoke_test",
        to_phone="+447123456789",
        status="no_answer",
        attempt_no=1,
        db_path=temp_db,
    )

    ok, reason = check_can_call(
        to_phone="+447123456789",
        purpose="smoke_test",
        db_path=temp_db,
    )
    assert not ok
    assert "Maximum attempt limit" in reason

    # With skip_attempts=True (used inside agent worker), it allows
    ok_skip, _ = check_can_call(
        to_phone="+447123456789",
        purpose="smoke_test",
        db_path=temp_db,
        skip_attempts=True,
    )
    assert ok_skip


def test_guard_condition_7_and_8_uk_hours_and_dst(temp_db, monkeypatch):
    """Guard enforces 09:00-20:00 UK time, blocks Sunday, and handles DST properly."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", DUMMY_SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", DUMMY_TOKEN)
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+17372508034")
    monkeypatch.setenv("MCP_OUTBOUND_ALLOWLIST", "+447123456789")
    monkeypatch.setenv("MCP_OUTBOUND_IGNORE_HOURS", "false")

    uk_tz = zoneinfo.ZoneInfo("Europe/London")

    # Tuesday at 14:00 UK (Summer / BST) -> Allowed
    bst_tuesday_afternoon = datetime(2026, 7, 14, 14, 0, tzinfo=uk_tz)
    ok, _ = check_can_call(
        to_phone="+447123456789",
        purpose="smoke_test",
        now=bst_tuesday_afternoon,
        db_path=temp_db,
    )
    assert ok

    # Tuesday at 08:30 UK (Summer / BST) -> Blocked (before 09:00)
    bst_tuesday_early = datetime(2026, 7, 14, 8, 30, tzinfo=uk_tz)
    ok, reason = check_can_call(
        to_phone="+447123456789",
        purpose="smoke_test",
        now=bst_tuesday_early,
        db_path=temp_db,
    )
    assert not ok
    assert "Outside UK calling hours" in reason

    # Wednesday at 20:30 UK (Winter / GMT) -> Blocked (after 20:00)
    gmt_wednesday_late = datetime(2026, 12, 9, 20, 30, tzinfo=uk_tz)
    ok, reason = check_can_call(
        to_phone="+447123456789",
        purpose="smoke_test",
        now=gmt_wednesday_late,
        db_path=temp_db,
    )
    assert not ok
    assert "Outside UK calling hours" in reason

    # Sunday at 14:00 UK -> Blocked (Sunday prohibition)
    sunday_afternoon = datetime(2026, 7, 19, 14, 0, tzinfo=uk_tz)
    ok, reason = check_can_call(
        to_phone="+447123456789",
        purpose="smoke_test",
        now=sunday_afternoon,
        db_path=temp_db,
    )
    assert not ok
    assert "Sundays" in reason

    # When MCP_OUTBOUND_IGNORE_HOURS=true -> Sunday is allowed
    monkeypatch.setenv("MCP_OUTBOUND_IGNORE_HOURS", "true")
    ok_override, _ = check_can_call(
        to_phone="+447123456789",
        purpose="smoke_test",
        now=sunday_afternoon,
        db_path=temp_db,
    )
    assert ok_override


def test_guard_records_blocked_call_row(temp_db, monkeypatch):
    """When record_blocked=True, blocked calls write a row to outbound_calls table."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", DUMMY_SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", DUMMY_TOKEN)
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+17372508034")
    monkeypatch.setenv("MCP_OUTBOUND_ALLOWLIST", "+447123456789")

    add_suppression("+447123456789", reason="opt_out", db_path=temp_db)

    ok, _ = check_can_call(
        to_phone="+447123456789",
        purpose="smoke_test",
        db_path=temp_db,
        record_blocked=True,
        outbound_id="blocked-test-1",
    )
    assert not ok

    with get_connection(temp_db) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT status, failure_reason FROM outbound_calls WHERE outbound_id = 'blocked-test-1'"
        )
        row = cur.fetchone()
        assert row is not None
        assert row["status"] == "blocked"
        assert "suppression list" in row["failure_reason"]


# =============================================================================
# 2. Twilio & LiveKit Connector: start_outbound_call tests
# =============================================================================


@pytest.mark.asyncio
async def test_start_outbound_call_converts_wss_to_https(temp_db, monkeypatch):
    """start_outbound_call converts LiveKit wss:// connect_url to https:// for Twilio Media Streams."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", DUMMY_SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", DUMMY_TOKEN)
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+17372508034")
    monkeypatch.setenv("AGENT_NAME", "my-agent")

    mock_lk = MagicMock()
    mock_connect_res = MagicMock()
    mock_connect_res.connect_url = "wss://murf-test.livekit.cloud/tw/stream/abc"
    mock_lk.connector.connect_twilio_call = AsyncMock(return_value=mock_connect_res)
    mock_lk.room.delete_room = AsyncMock()

    mock_tw = MagicMock()
    mock_call_obj = MagicMock()
    mock_call_obj.sid = "CA1234567890"
    mock_tw.calls.create = MagicMock(return_value=mock_call_obj)

    handle = await start_outbound_call(
        to_phone="+447123456789",
        outbound_id="ob-test-1",
        metadata={"mode": "smoke"},
        with_agent=True,
        livekit_api_client=mock_lk,
        twilio_client=mock_tw,
        db_path=temp_db,
    )

    assert handle.room_name == "outbound-ob-test-1"
    assert handle.call_sid == "CA1234567890"

    # Verify Twilio was called with https:// instead of wss://
    mock_tw.calls.create.assert_called_once()
    _, kwargs = mock_tw.calls.create.call_args
    assert kwargs["url"] == "https://murf-test.livekit.cloud/tw/stream/abc"
    assert kwargs["to"] == "+447123456789"
    assert kwargs["from_"] == "+17372508034"

    # Verify LiveKit connect_twilio_call dispatch
    mock_lk.connector.connect_twilio_call.assert_called_once()
    req = mock_lk.connector.connect_twilio_call.call_args[0][0]
    assert len(req.agents) == 1
    assert req.agents[0].agent_name == "my-agent"
    assert "smoke" in req.agents[0].metadata


@pytest.mark.asyncio
async def test_start_outbound_call_deletes_room_on_twilio_error(temp_db, monkeypatch):
    """If Twilio rejects the call, the LiveKit room must be deleted immediately."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", DUMMY_SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", DUMMY_TOKEN)
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+17372508034")

    mock_lk = MagicMock()
    mock_connect_res = MagicMock()
    mock_connect_res.connect_url = "wss://murf-test.livekit.cloud/tw/stream/xyz"
    mock_lk.connector.connect_twilio_call = AsyncMock(return_value=mock_connect_res)
    mock_lk.room.delete_room = AsyncMock()

    mock_tw = MagicMock()
    mock_tw.calls.create = MagicMock(
        side_effect=Exception("Twilio Error 21211: Invalid To phone number")
    )

    with pytest.raises(RuntimeError, match="Twilio rejected call"):
        await start_outbound_call(
            to_phone="+447123456789",
            outbound_id="ob-fail-1",
            metadata={"mode": "smoke"},
            livekit_api_client=mock_lk,
            twilio_client=mock_tw,
            db_path=temp_db,
        )

    # Room must be deleted
    mock_lk.room.delete_room.assert_called_once()


# =============================================================================
# 3. Status Polling & Room Cleanup: watch_call_status
# =============================================================================


@pytest.mark.asyncio
async def test_watch_call_status_maps_in_progress_to_answered(temp_db):
    """watch_call_status maps Twilio 'in-progress' status to 'answered'."""
    mock_tw = MagicMock()
    call_mock = MagicMock()
    call_mock.status = "in-progress"
    mock_tw.calls.return_value.fetch.return_value = call_mock

    mock_lk = MagicMock()
    mock_lk.room.delete_room = AsyncMock()

    status = await watch_call_status(
        call_sid="CA999",
        outbound_id="ob-ans-1",
        room_name="outbound-ob-ans-1",
        twilio_client=mock_tw,
        livekit_api_client=mock_lk,
        db_path=temp_db,
        poll_interval=0.01,
    )

    assert status == "answered"
    # Room should NOT be deleted when answered
    mock_lk.room.delete_room.assert_not_called()


@pytest.mark.asyncio
async def test_watch_call_status_deletes_room_on_rejected_and_no_answer(temp_db):
    """Non-answered states (busy, no-answer, failed) map correctly and delete the room."""
    mock_lk = MagicMock()
    mock_lk.room.delete_room = AsyncMock()

    # Case A: Busy -> rejected
    mock_tw_busy = MagicMock()
    c1 = MagicMock(status="busy")
    mock_tw_busy.calls.return_value.fetch.return_value = c1

    s1 = await watch_call_status(
        call_sid="CA-busy",
        outbound_id="ob-busy",
        room_name="outbound-ob-busy",
        twilio_client=mock_tw_busy,
        livekit_api_client=mock_lk,
        db_path=temp_db,
        poll_interval=0.01,
    )
    assert s1 == "rejected"
    assert mock_lk.room.delete_room.call_count == 1

    # Case B: No-answer -> no_answer
    mock_tw_noans = MagicMock()
    c2 = MagicMock(status="no-answer")
    mock_tw_noans.calls.return_value.fetch.return_value = c2

    s2 = await watch_call_status(
        call_sid="CA-noans",
        outbound_id="ob-noans",
        room_name="outbound-ob-noans",
        twilio_client=mock_tw_noans,
        livekit_api_client=mock_lk,
        db_path=temp_db,
        poll_interval=0.01,
    )
    assert s2 == "no_answer"
    assert mock_lk.room.delete_room.call_count == 2


# =============================================================================
# 4. Error Sanitization & Masking
# =============================================================================


def test_sanitize_error_redacts_credentials_and_full_phones():
    """Sanitizer scrubs Twilio Account SIDs, Auth Tokens, and full phone numbers."""
    raw_error = (
        f"Failed authentication for SID {DUMMY_SID} "
        f"with token {DUMMY_TOKEN} while calling +919486645580"
    )
    sanitized = sanitize_error(raw_error)

    assert DUMMY_SID not in sanitized
    assert DUMMY_TOKEN not in sanitized
    assert "+919486645580" not in sanitized
    assert "AC[REDACTED_SID]" in sanitized
    assert "[REDACTED_TOKEN]" in sanitized
    assert "***5580" in sanitized


def test_mask_phone():
    """mask_phone preserves only last 4 digits."""
    assert mask_phone("+919486645580") == "***5580"
    assert mask_phone("+447123456789") == "***6789"
    assert mask_phone("123") == "****"
    assert mask_phone("") == "****"


# =============================================================================
# 5. Hang Up Reliability & Order
# =============================================================================


@pytest.mark.asyncio
async def test_hang_up_never_raises_on_errors():
    """hang_up calls Twilio update and LiveKit room deletion, catching any errors without raising."""
    failing_tw = MagicMock()
    mock_call = MagicMock()
    mock_call.status = "in-progress"
    failing_tw.calls.return_value.fetch.return_value = mock_call
    failing_tw.calls.return_value.update.side_effect = Exception("Twilio network error")

    failing_lk = MagicMock()
    failing_lk.room.delete_room = AsyncMock(
        side_effect=Exception("LiveKit connection closed")
    )

    # Must complete cleanly without raising
    clean = await hang_up(
        call_sid="CA-broken",
        room_name="outbound-broken",
        twilio_client=failing_tw,
        livekit_api_client=failing_lk,
        verify_timeout=0.1,
    )

    failing_tw.calls.return_value.update.assert_called_once_with(status="completed")
    failing_lk.room.delete_room.assert_called_once()
    assert clean is False


@pytest.mark.asyncio
async def test_hang_up_already_completed_call():
    """If Twilio call is already completed, hang_up skips update and still deletes room cleanly."""
    mock_tw = MagicMock()
    completed_call = MagicMock()
    completed_call.status = "completed"
    completed_call.duration = "24"
    mock_tw.calls.return_value.fetch.return_value = completed_call

    mock_lk = MagicMock()
    mock_lk.room.delete_room = AsyncMock()

    clean = await hang_up(
        call_sid="CA-already-done",
        room_name="outbound-already-done",
        twilio_client=mock_tw,
        livekit_api_client=mock_lk,
        verify_timeout=0.2,
    )

    # 1. Fetch was called
    assert mock_tw.calls.return_value.fetch.called
    # 2. Update status=completed was SKIPPED because it was already completed
    assert not mock_tw.calls.return_value.update.called
    # 3. Room was deleted
    mock_lk.room.delete_room.assert_called_once()
    # 4. Result is clean
    assert clean is True


@pytest.mark.asyncio
async def test_hang_up_on_404_not_found():
    """If Twilio returns 404 on fetch, hang_up catches it, deletes room, and returns False without raising."""
    mock_tw = MagicMock()
    mock_tw.calls.return_value.fetch.side_effect = TwilioRestException(
        404,
        "/Calls/CA-missing.json",
        "Unable to update record: The requested resource /2010-04-01/Accounts/AC123/Calls/CA-missing.json was not found",
        code=20404,
    )

    mock_lk = MagicMock()
    mock_lk.room.delete_room = AsyncMock()

    clean = await hang_up(
        call_sid="CA-missing",
        room_name="outbound-missing",
        twilio_client=mock_tw,
        livekit_api_client=mock_lk,
        verify_timeout=0.2,
    )

    # Room is still deleted
    mock_lk.room.delete_room.assert_called_once()
    # Does not raise, but returns False since call was not verified
    assert clean is False


@pytest.mark.asyncio
async def test_hang_up_order_twilio_first_then_room():
    """Verify that Twilio call is inspected and updated BEFORE deleting the LiveKit room."""
    execution_order = []

    mock_tw = MagicMock()

    def fake_fetch():
        execution_order.append("twilio_fetch")
        call_mock = MagicMock()
        # If fetch is called during verify phase, return completed
        call_mock.status = (
            "completed" if "delete_room" in execution_order else "in-progress"
        )
        call_mock.duration = "10"
        return call_mock

    def fake_update(**kwargs):
        execution_order.append("twilio_update")

    mock_tw.calls.return_value.fetch.side_effect = fake_fetch
    mock_tw.calls.return_value.update.side_effect = fake_update

    mock_lk = MagicMock()

    async def fake_delete_room(*args, **kwargs):
        execution_order.append("delete_room")

    mock_lk.room.delete_room = AsyncMock(side_effect=fake_delete_room)

    clean = await hang_up(
        call_sid="CA-ordered",
        room_name="outbound-ordered",
        twilio_client=mock_tw,
        livekit_api_client=mock_lk,
        verify_timeout=0.5,
    )

    assert clean is True
    # Verify strict order: Twilio operations precede room deletion
    assert execution_order == [
        "twilio_fetch",
        "twilio_update",
        "delete_room",
        "twilio_fetch",
    ]


def test_sanitize_error_twilio_single_line():
    """Twilio error sanitization prints code and single-line summary without raw HTTP dumps or paths."""
    exc = TwilioRestException(
        404,
        f"/2010-04-01/Accounts/{DUMMY_SID}/Calls/CA123.json",
        f"Unable to update record: The requested resource /2010-04-01/Accounts/{DUMMY_SID}/Calls/CA123.json was not found",
        code=20404,
    )
    sanitized = sanitize_error(exc)

    assert "\n" not in sanitized
    assert "Twilio error 20404:" in sanitized
    assert "Unable to update record: The requested resource was not found" in sanitized
    assert DUMMY_SID not in sanitized
    assert "/2010-04-01/Accounts" not in sanitized


def test_get_twilio_client_reuses_shared_instance(monkeypatch):
    """get_twilio_client returns the same client instance when credentials match."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", DUMMY_SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", DUMMY_TOKEN)

    c1 = get_twilio_client()
    c2 = get_twilio_client()
    assert c1 is c2


# =============================================================================
# 6. Connector Participant Wait & In-Flight Guards
# =============================================================================


@pytest.mark.asyncio
async def test_wait_for_participant_connector_returns():
    """wait_for_participant successfully matches and returns a CONNECTOR participant."""
    room = MagicMock()
    room.isconnected.return_value = True

    p = MagicMock()
    p.identity = "phone-smoke-test-123"
    p.kind = rtc.ParticipantKind.PARTICIPANT_KIND_CONNECTOR  # 5
    p.state = rtc.ParticipantState.PARTICIPANT_STATE_ACTIVE

    room.remote_participants = {p.identity: p}

    target_kinds = [
        rtc.ParticipantKind.PARTICIPANT_KIND_CONNECTOR,
        rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
        rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
        5,
        7,
    ]

    res = await wait_for_participant(
        room,
        identity="phone-smoke-test-123",
        kind=target_kinds,
    )
    assert res is p
    assert res.identity == "phone-smoke-test-123"
    assert res.kind == rtc.ParticipantKind.PARTICIPANT_KIND_CONNECTOR


@pytest.mark.asyncio
async def test_wait_for_participant_connector_dynamic_join():
    """wait_for_participant waits for and returns a dynamically joining CONNECTOR participant."""
    callbacks = {}
    room = MagicMock()
    room.isconnected.return_value = True
    room.remote_participants = {}

    def mock_on(event, cb):
        callbacks[event] = cb

    def mock_off(event, cb):
        callbacks.pop(event, None)

    room.on.side_effect = mock_on
    room.off.side_effect = mock_off

    p = MagicMock()
    p.identity = "phone-smoke-dyn"
    p.kind = rtc.ParticipantKind.PARTICIPANT_KIND_CONNECTOR
    p.state = rtc.ParticipantState.PARTICIPANT_STATE_ACTIVE

    async def emit_later():
        await asyncio.sleep(0.05)
        if "participant_active" in callbacks:
            callbacks["participant_active"](p)

    _task = asyncio.create_task(emit_later())

    res = await wait_for_participant(
        room,
        identity="phone-smoke-dyn",
        kind=[rtc.ParticipantKind.PARTICIPANT_KIND_CONNECTOR, 5, 7],
    )
    await _task
    assert res is p
    assert res.identity == "phone-smoke-dyn"


def test_get_recent_dialling_call_within_two_minutes(temp_db):
    """get_recent_dialling_call detects recent dialling calls and ignores older/settled ones."""
    # 1. New dialling call -> Detected
    create_outbound(
        outbound_id="call-recent-dialling",
        purpose="smoke_test",
        to_phone="+447123456789",
        status="dialling",
        db_path=temp_db,
    )

    found = get_recent_dialling_call(
        "+447123456789", within_seconds=120, db_path=temp_db
    )
    assert found is not None
    assert found["outbound_id"] == "call-recent-dialling"
    assert found["status"] == "dialling"

    # 2. Settled call (answered) -> Ignored
    create_outbound(
        outbound_id="call-answered",
        purpose="smoke_test",
        to_phone="+447999888777",
        status="answered",
        db_path=temp_db,
    )
    assert (
        get_recent_dialling_call("+447999888777", within_seconds=120, db_path=temp_db)
        is None
    )


def test_sanitize_error_distinguishes_livekit_and_twilio():
    """LiveKit errors are labeled LiveKit error and Twilio errors as Twilio error."""
    from livekit.api.twirp_client import ServerError

    lk_err = ServerError("not_found", "requested room does not exist", status=404)
    sanitized_lk = sanitize_error(lk_err)
    assert sanitized_lk.startswith("LiveKit error not_found:")
    assert "Twilio" not in sanitized_lk

    tw_err = TwilioRestException(
        404, "/Calls/CA123.json", "Record not found", code=20404
    )
    sanitized_tw = sanitize_error(tw_err)
    assert sanitized_tw.startswith("Twilio error 20404:")
    assert "LiveKit" not in sanitized_tw


@pytest.mark.asyncio
async def test_safe_delete_room_missing_room_treated_as_already_closed(caplog):
    """Missing room is treated as already closed without warning."""
    import logging

    from livekit.api.twirp_client import ServerError

    from telephony_twilio import _safe_delete_room

    mock_lk = MagicMock()
    mock_lk.room.delete_room = AsyncMock(
        side_effect=ServerError(
            "not_found", "requested room does not exist", status=404
        )
    )

    with caplog.at_level(logging.INFO):
        await _safe_delete_room(mock_lk, "outbound-already-gone")

    # Should log INFO that room is already closed, not WARNING
    assert "already closed" in caplog.text
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_get_call_notifications_extracts_and_sanitizes():
    """get_call_notifications returns clean error code and sanitized summary."""
    mock_tw = MagicMock()
    notif = MagicMock()
    notif.error_code = "32001"
    notif.message_text = f"Call rejected by carrier for +919486645580 with SID {DUMMY_SID}"
    mock_tw.calls.return_value.notifications.list.return_value = [notif]

    results = get_call_notifications("CA12345", twilio_client=mock_tw)
    assert len(results) == 1
    assert results[0]["error_code"] == "32001"
    assert DUMMY_SID not in results[0]["summary"]
    assert "+919486645580" not in results[0]["summary"]
    assert "***5580" in results[0]["summary"]
