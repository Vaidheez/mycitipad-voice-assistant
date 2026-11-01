import os
import tempfile

import pytest

from agent import Assistant
from db import (
    find_caller,
    get_connection,
    init_db,
    start_call_log,
)
from memory_tools import SessionState
from prompt import get_returning_caller_greeting


@pytest.fixture
def temp_db(monkeypatch):
    """Fixture providing an isolated temporary SQLite database for tests."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    monkeypatch.setenv("MCP_DB_PATH", db_path)
    init_db(db_path)

    yield db_path

    if os.path.exists(db_path):
        import contextlib

        with contextlib.suppress(OSError):
            os.remove(db_path)


@pytest.mark.asyncio
async def test_save_caller_profile_refuses_without_consent(temp_db):
    """1. save_caller_profile refuses and writes nothing when consent is None or False."""
    session_state = SessionState(call_id="call-1", memory_consent=None)
    assistant = Assistant(session_state=session_state)

    result_none = await assistant.save_caller_profile(
        name="Arthur Dent",
        email="arthur@example.co.uk",
        phone="02035766575",
        facts={"interest": "lettings"},
    )
    assert (
        "Refusal: Caller has not granted explicit verbal memory consent" in result_none
    )
    assert find_caller(email="arthur@example.co.uk") is None

    # Explicit False
    session_state.memory_consent = False
    result_false = await assistant.save_caller_profile(
        name="Arthur Dent",
        email="arthur@example.co.uk",
        phone="02035766575",
        facts={"interest": "lettings"},
    )
    assert (
        "Refusal: Caller has not granted explicit verbal memory consent" in result_false
    )
    assert find_caller(email="arthur@example.co.uk") is None


@pytest.mark.asyncio
async def test_save_caller_profile_succeeds_with_consent(temp_db):
    """2. save_caller_profile succeeds after record_memory_consent(True) and row exists."""
    session_state = SessionState(call_id="call-2", memory_consent=None)
    assistant = Assistant(session_state=session_state)

    consent_res = await assistant.record_memory_consent(granted=True)
    assert "granted=true" in consent_res
    assert session_state.memory_consent is True

    save_res = await assistant.save_caller_profile(
        name="Jane Marple",
        email="jane.marple@stmarymead.uk",
        phone="+44 7123 456789",
        language_preference="en-GB",
        facts={
            "interest": "lettings",
            "property_of_interest": "Baker Street",
            "budget_max_gbp": 500000,
        },
    )
    assert "Success: Caller profile saved for Jane Marple." in save_res

    caller = find_caller(email="jane.marple@stmarymead.uk")
    assert caller is not None
    assert caller["name"] == "Jane Marple"
    assert caller["facts"]["budget_max_gbp"] == 500000
    assert caller["facts"]["interest"] == "lettings"


@pytest.mark.asyncio
async def test_subsequent_consent_false_blocks_saves(temp_db):
    """3. A later record_memory_consent(False) blocks further saves."""
    session_state = SessionState(call_id="call-3")
    assistant = Assistant(session_state=session_state)

    await assistant.record_memory_consent(granted=True)
    assert session_state.memory_consent is True

    # Caller changes their mind
    await assistant.record_memory_consent(granted=False)
    assert session_state.memory_consent is False

    save_res = await assistant.save_caller_profile(
        name="Hercule Poirot",
        email="poirot@belgium.be",
    )
    assert "Refusal: Caller has not granted explicit verbal memory consent" in save_res
    assert find_caller(email="poirot@belgium.be") is None


@pytest.mark.asyncio
async def test_lookup_caller_matches_email_case_and_phone_formats(temp_db):
    """4. lookup_caller finds caller by email (case-insensitive) and phone (spaces / +44)."""
    session_state = SessionState(call_id="call-4")
    assistant = Assistant(session_state=session_state)

    await assistant.record_memory_consent(granted=True)
    await assistant.save_caller_profile(
        name="Sherlock Holmes",
        email="sherlock@221b.co.uk",
        phone="+442035766575",
        facts={"interest": "sales", "last_request_type": "sales_lead"},
    )

    # Email case-insensitive lookup
    res_upper_email = await assistant.lookup_caller(email="SHERLOCK@221B.CO.UK")
    assert "Sherlock Holmes" in res_upper_email
    assert "sales" in res_upper_email

    # Phone with local formatting (020 3576 6575 matches stored +442035766575)
    res_spaced_phone = await assistant.lookup_caller(phone="020 3576 6575")
    assert "Sherlock Holmes" in res_spaced_phone

    # In normalised lookup or +44 (0) format
    res_intl_phone = await assistant.lookup_caller(phone="+44 (0)20 3576 6575")
    assert "Sherlock Holmes" in res_intl_phone


@pytest.mark.asyncio
async def test_lookup_caller_unknown_caller(temp_db):
    """5. lookup_caller returns 'No saved profile.' for an unknown caller."""
    session_state = SessionState(call_id="call-5")
    assistant = Assistant(session_state=session_state)

    result = await assistant.lookup_caller(email="stranger@unknown.co.uk")
    assert result == "No saved profile."


@pytest.mark.asyncio
async def test_rejects_disallowed_facts_and_non_integer_budget(temp_db):
    """6. Facts outside allow-list are rejected, and budget_max_gbp rejects non-integers."""
    session_state = SessionState(call_id="call-6")
    assistant = Assistant(session_state=session_state)
    await assistant.record_memory_consent(granted=True)

    # Sensitive fact disallowed in caller profile
    res_disallowed = await assistant.save_caller_profile(
        name="John Watson",
        email="watson@221b.co.uk",
        facts={"annual_income": 80000, "employment_status": "employed"},
    )
    assert "Refusal: Invalid fact keys provided" in res_disallowed

    # Non-integer budget rejected
    res_bad_budget = await assistant.save_caller_profile(
        name="John Watson",
        email="watson@221b.co.uk",
        facts={"budget_max_gbp": "500000"},  # String instead of int
    )
    assert "budget_max_gbp must be a whole integer number" in res_bad_budget

    res_lakh_budget = await assistant.save_caller_profile(
        name="John Watson",
        email="watson@221b.co.uk",
        facts={"budget_max_gbp": 500000.50},  # Float
    )
    assert "budget_max_gbp must be a whole integer number" in res_lakh_budget


@pytest.mark.asyncio
async def test_forget_caller_deletes_profile_and_detaches_logs(temp_db):
    """7. forget_caller deletes profile and sets user_id = NULL on call logs."""
    call_id = start_call_log(temp_db)
    session_state = SessionState(call_id=call_id)
    assistant = Assistant(session_state=session_state)
    await assistant.record_memory_consent(granted=True)

    await assistant.save_caller_profile(
        name="Molly Hooper",
        email="molly@hospital.co.uk",
        phone="07111222333",
    )
    user_id = session_state.user_id
    assert user_id is not None

    # Call log is linked to user_id
    with get_connection(temp_db) as conn:
        row = conn.execute(
            "SELECT user_id FROM call_logs WHERE call_id = ?", (call_id,)
        ).fetchone()
        assert row["user_id"] == user_id

    # Caller requests to be forgotten
    forget_res = await assistant.forget_caller()
    assert (
        "Success: Your profile has been completely deleted and forgotten." in forget_res
    )
    assert session_state.user_id is None

    # Caller profile is deleted
    assert find_caller(user_id=user_id) is None

    # Call log still exists as a business record, but user_id is detached (NULL)
    with get_connection(temp_db) as conn:
        log_row = conn.execute(
            "SELECT user_id FROM call_logs WHERE call_id = ?", (call_id,)
        ).fetchone()
        assert log_row is not None
        assert log_row["user_id"] is None


@pytest.mark.asyncio
async def test_simulated_db_failure_returns_safe_string(monkeypatch):
    """8. A simulated DB failure (e.g. bad path) returns safe string and does not raise."""
    with tempfile.NamedTemporaryFile() as tf:
        bad_path = os.path.join(tf.name, "sub_dir", "db.sqlite")
        monkeypatch.setenv("MCP_DB_PATH", bad_path)

        session_state = SessionState(call_id="call-fail", memory_consent=True)
        assistant = Assistant(session_state=session_state)

        lookup_res = await assistant.lookup_caller(email="test@fail.com")
        assert lookup_res == "Lookup unavailable right now"

        save_res = await assistant.save_caller_profile(
            name="Test Fail",
            email="fail@test.com",
        )
        assert "I couldn't save that just now." in save_res


@pytest.mark.asyncio
async def test_second_save_merges_facts_and_preserves_consent_at(temp_db):
    """9. A second save merges facts and updates last_interaction; consent_at stays same."""
    session_state = SessionState(call_id="call-9")
    assistant = Assistant(session_state=session_state)
    await assistant.record_memory_consent(granted=True)

    await assistant.save_caller_profile(
        name="Mycroft Holmes",
        email="mycroft@diogenes.club",
        phone="02071234567",
        facts={"interest": "sales", "budget_max_gbp": 1200000},
    )
    caller_1 = find_caller(email="mycroft@diogenes.club")
    consent_at_1 = caller_1["consent_at"]

    # Second save with additional fact
    await assistant.save_caller_profile(
        name="Mycroft Holmes",
        email="mycroft@diogenes.club",
        facts={"property_of_interest": "Pall Mall", "preferred_contact": "email"},
    )
    caller_2 = find_caller(email="mycroft@diogenes.club")

    # consent_at is unchanged
    assert caller_2["consent_at"] == consent_at_1
    # facts are merged
    assert caller_2["facts"]["interest"] == "sales"
    assert caller_2["facts"]["budget_max_gbp"] == 1200000
    assert caller_2["facts"]["property_of_interest"] == "Pall Mall"
    assert caller_2["facts"]["preferred_contact"] == "email"


def test_returning_caller_greeting_formatting():
    """Verify returning caller greeting text follows exact rules."""
    # With last request type
    greeting_with_req = get_returning_caller_greeting("Jane", "lettings_enquiry")
    assert "Welcome back, Jane, and thanks for calling MyCitiPad." in greeting_with_req
    assert "Last time you were asking about a lettings enquiry." in greeting_with_req
    assert (
        "Would you like to pick up from there, or is it something new?"
        in greeting_with_req
    )

    # Without last request type
    greeting_no_req = get_returning_caller_greeting("Arthur", None)
    assert "Welcome back, Arthur, and thanks for calling MyCitiPad." in greeting_no_req
    assert (
        "Would you like to pick up from where we left off, or is it something new?"
        in greeting_no_req
    )
    assert "Last time you were asking about" not in greeting_no_req
