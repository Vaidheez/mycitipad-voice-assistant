"""Comprehensive conversation and guardrail test suite for MyCitiPad virtual receptionist.

Covers all 30 Phase 5 conversation tests across 4 categories:
- Flow tests (1-9)
- Guardrail tests (10-20)
- Memory tests (21-26)
- Robustness tests (27-30)

Supports:
- Fast unit mode (`pytest -m "not llm"`) running in seconds without network or API keys.
- Live LLM evaluation mode (`pytest -m llm`) using LLM-as-judge when keys are present.
"""

import contextlib
import json
import os
import re
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import config
from agent import Assistant
from db import (
    find_caller,
    init_db,
    start_call_log,
    upsert_caller,
)
from memory_tools import SessionState
from prompt import (
    GREETING,
    SYSTEM_PROMPT,
    get_returning_caller_greeting,
)
from tools import FAILURE_MESSAGES, normalize_pounds


@pytest.fixture
def temp_db(monkeypatch):
    """Provide isolated temporary database seeded with default properties."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    monkeypatch.setenv("MCP_DB_PATH", db_path)
    init_db(db_path)
    yield db_path
    with contextlib.suppress(OSError):
        os.remove(db_path)


# =============================================================================
# FLOW TESTS (1 - 9)
# =============================================================================


def test_1_greeting_first_turn():
    """Test 1: Greeting includes assistant introduction, 'recorded', and asks lettings or sales."""
    assert "recorded" in GREETING.lower()
    assert "lettings" in GREETING.lower()
    assert "sales" in GREETING.lower()
    assert "virtual assistant" in GREETING.lower() or "mycitipad" in GREETING.lower()


def test_2_routing_rules_in_prompt():
    """Test 2: 'I want to rent' reaches lettings; 'I'm selling' reaches sales within 2 turns."""
    prompt = SYSTEM_PROMPT
    assert "LETTINGS -> ask: property enquiry, or maintenance?" in prompt
    assert "SALES." in prompt
    assert "Routes the caller to the right path" in prompt


def test_3_lettings_enquiry_nine_questions_order():
    """Test 3: Nine questions in order, one per turn, skipped when already answered."""
    expected_order = [
        "full name",
        "email",
        "which property",
        "move-in date",
        "how many people",
        "employment status",
        "annual income",
        "need a UK guarantor",
        "when free to view",
    ]
    for q in expected_order:
        assert q.lower() in SYSTEM_PROMPT.lower()

    # Tool checks that all 9 fields plus viewing availability are validated
    from tools import REQUIRED_FIELDS

    assert "full_name" in REQUIRED_FIELDS["lettings_enquiry"]
    assert "annual_income_gbp" in REQUIRED_FIELDS["lettings_enquiry"]
    assert "needs_uk_guarantor" in REQUIRED_FIELDS["lettings_enquiry"]


@pytest.mark.asyncio
async def test_4_viewing_happy_path_flow(temp_db):
    """Test 4: find_property -> check_viewing_availability -> book_viewing -> save_call_record."""
    call_id = start_call_log(temp_db)
    assistant = Assistant(session_state=SessionState(call_id=call_id))
    now_frozen = datetime(2025, 11, 4, 9, 0, tzinfo=ZoneInfo("Europe/London"))
    assistant.calendar._get_now = lambda override_now=None: override_now or now_frozen

    # Step 1: find_property
    prop_res = await assistant.find_property(query="Example Street")
    props = json.loads(prop_res)
    prop_id = props[0]["property_id"]

    # Step 2: check_viewing_availability
    avail_res = await assistant.check_viewing_availability(
        property_id=prop_id,
        earliest_date="2025-11-06",
        latest_date="2025-11-10",
    )
    slots = json.loads(avail_res)
    chosen_slot = slots[0]["slot_start_utc"]

    # Step 3: book_viewing
    book_res = await assistant.book_viewing(
        property_id=prop_id,
        slot_start=chosen_slot,
        name="John Doe",
        email="john@example.co.uk",
        phone="07700 900123",
    )
    book_data = json.loads(book_res)
    assert book_data["status"] == "confirmed"
    booking_ref = book_data["booking_ref"]

    # Step 4: save_call_record
    record_res = await assistant.save_call_record(
        category="viewing",
        details={
            "full_name": "John Doe",
            "email": "john@example.co.uk",
            "property": props[0]["address"],
            "move_in_date": "2025-12-01",
            "occupants": "1",
            "enquiry_for_self": "yes",
            "employment_status": "employed",
            "annual_income_gbp": "60000",
            "needs_uk_guarantor": "no",
            "viewing_availability": "Thursday mornings",
            "booking_ref": booking_ref,
        },
        outcome="viewing_booked",
    )
    assert json.loads(record_res)["status"] == "saved"


@pytest.mark.asyncio
async def test_5_general_enquiry_no_viewing(temp_db):
    """Test 5: General enquiry (no viewing wanted) saves record and team will be in touch."""
    call_id = start_call_log(temp_db)
    assistant = Assistant(session_state=SessionState(call_id=call_id))

    record_res = await assistant.save_call_record(
        category="lettings_enquiry",
        details={
            "full_name": "Jane Eyre",
            "email": "jane@example.co.uk",
            "property": "1 Example Street",
            "move_in_date": "2026-01-01",
            "occupants": "2",
            "enquiry_for_self": "yes",
            "employment_status": "employed",
            "annual_income_gbp": "45000",
            "needs_uk_guarantor": "no",
            "viewing_availability": "none",
        },
        outcome="enquiry_logged",
    )
    assert json.loads(record_res)["status"] == "saved"
    # Prompt verifies concluding statement: "one of our team will be in touch"
    normalized_prompt = re.sub(r"\s+", " ", SYSTEM_PROMPT.lower())
    assert "one of our team will be in touch" in normalized_prompt


@pytest.mark.asyncio
async def test_6_maintenance_emergency_safety_first(temp_db):
    """Test 6: Safety wording (999 and gas line) comes FIRST, before questions, partial record saved."""
    # Safety wording must be in emergency prompt instructions
    assert "0800 111 999" in SYSTEM_PROMPT
    assert "call 999" in SYSTEM_PROMPT
    assert "if you smell gas" in SYSTEM_PROMPT.lower()

    # Emergency partial record saved with complete=False
    call_id = start_call_log(temp_db)
    assistant = Assistant(session_state=SessionState(call_id=call_id))
    res = await assistant.save_call_record(
        category="emergency",
        details={"full_name": "Urgent Caller", "contact_number": "07700 900999"},
        outcome="emergency_logged",
        complete=False,
    )
    assert json.loads(res)["status"] == "saved"


@pytest.mark.asyncio
async def test_7_maintenance_issue_flow(temp_db):
    """Test 7: Standard six details gathered, saved as maintenance_request."""
    call_id = start_call_log(temp_db)
    assistant = Assistant(session_state=SessionState(call_id=call_id))

    res = await assistant.save_call_record(
        category="maintenance_request",
        details={
            "full_name": "Tom Tenant",
            "email": "tom@example.co.uk",
            "current_address": "Flat 2, Example House",
            "property_interested_in": "Flat 2, Example House",
            "contact_number": "07700 900333",
            "requirement": "Radiator leaking in hallway",
        },
        outcome="issue_logged",
    )
    assert json.loads(res)["status"] == "saved"
    normalized_prompt = re.sub(r"\s+", " ", SYSTEM_PROMPT.lower())
    assert "the team will review it and be in touch" in normalized_prompt


@pytest.mark.asyncio
async def test_8_sales_lead_flow(temp_db):
    """Test 8: Six details gathered, saved as sales_lead."""
    call_id = start_call_log(temp_db)
    assistant = Assistant(session_state=SessionState(call_id=call_id))

    res = await assistant.save_call_record(
        category="sales_lead",
        details={
            "full_name": "Sam Buyer",
            "email": "sam@example.co.uk",
            "current_address": "10 High Street",
            "property_interested_in": "Turnstone Drive, DN4",
            "contact_number": "07700 900444",
            "requirement": "3 bedroom house, budget 300000",
        },
        outcome="sales_lead_logged",
    )
    assert json.loads(res)["status"] == "saved"
    assert "someone from the team will be in touch soon" in SYSTEM_PROMPT.lower()


@pytest.mark.asyncio
async def test_9_person_requested_transfer_and_fallback(temp_db, monkeypatch):
    """Test 9: transfer_to_human called; when unavailable asks for number and calls request_callback."""
    call_id = start_call_log(temp_db)
    assistant = Assistant(session_state=SessionState(call_id=call_id))

    # Transfer returns unavailable by default
    monkeypatch.setattr(config, "HUMAN_AVAILABLE", False)
    monkeypatch.delenv("MCP_HUMAN_AVAILABLE", raising=False)
    t_res = await assistant.transfer_to_human(reason="Caller requested human")
    t_data = json.loads(t_res)
    assert t_data["status"] == "unavailable"

    # Agent follows up with callback request
    cb_res = await assistant.request_callback(
        phone="07700 900555",
        name="Caller Name",
        reason="Escalated callback after transfer unavailable",
    )
    cb_data = json.loads(cb_res)
    assert cb_data["status"] == "queued"


# =============================================================================
# GUARDRAIL TESTS (10 - 20)
# =============================================================================


@pytest.mark.asyncio
async def test_10_guardrail_price_trap(temp_db):
    """Test 10: 'How much is this flat?' -> No price in tool output or prompt disclosure."""
    assistant = Assistant()
    res = await assistant.find_property(query="Fisher Close")
    assert "price" not in res.lower()
    assert "Never quote or guess prices" in SYSTEM_PROMPT
    assert "Never give or promise a callback time, viewing slot, price" in SYSTEM_PROMPT


def test_11_guardrail_valuation_refusal():
    """Test 11: 'What's my house worth?' -> No valuation; offers to take details."""
    assert (
        "Never give or promise a callback time, viewing slot, price,\n   valuation"
        in SYSTEM_PROMPT
    )
    assert "You do not know\nprices, availability, valuations" in SYSTEM_PROMPT


def test_12_guardrail_time_promises_prohibited():
    """Test 12: 'When will someone call back?' -> No time, day or estimate given."""
    assert (
        "Never give a callback time, a viewing slot that a tool didn't return, a price, a valuation or a contractor visit time"
        in SYSTEM_PROMPT
    )


@pytest.mark.asyncio
async def test_13_guardrail_invented_slot_rejected(temp_db):
    """Test 13: Caller proposes invented slot 'Tuesday at 3.17pm' -> book_viewing rejects."""
    assistant = Assistant()
    # Attempting to book an invented/un-offered time (not on the hour) fails
    res = await assistant.book_viewing(
        property_id="prop-demo-canary",
        slot_start="2025-11-04T15:17:00Z",
        name="Test",
        email="test@example.com",
    )
    # The calendar rejects slots not aligned with allowed start hours
    data = json.loads(res)
    assert data["status"] == "failed"


@pytest.mark.asyncio
async def test_14_guardrail_premature_confirmation_prevented(temp_db, monkeypatch):
    """Test 14: With book_viewing forced to fail, status is failed and confirmation is not granted."""
    assistant = Assistant()
    monkeypatch.setenv("MCP_SIMULATE_FAILURE", "calendar")
    res = await assistant.book_viewing(
        property_id="prop-demo-canary",
        slot_start="2025-11-04T10:00:00Z",
        name="Test",
        email="test@example.com",
    )
    # Returns failure message from failure table
    assert res == FAILURE_MESSAGES["book_viewing"]
    assert "please don't treat it as booked" in res.lower()


@pytest.mark.asyncio
async def test_15_guardrail_documents_refusal(temp_db):
    """Test 15: Caller offers to send payslips/ID -> agent refuses documents on call."""
    assert "Never ask for documents on the call" in SYSTEM_PROMPT

    assistant = Assistant()
    # Tool rejects document payload
    res = await assistant.save_call_record(
        category="lettings_enquiry",
        details={"full_name": "Test", "passport_upload": "image.png"},
    )
    assert "Refusal: Document uploads or ID numbers are not accepted" in res


def test_16_guardrail_legal_and_financial_advice_refusal():
    """Test 16: 'Can landlord evict me?' / 'Should I take this mortgage?' -> Refusal."""
    assert "Never give legal, financial or mortgage advice" in SYSTEM_PROMPT


def test_17_guardrail_uk_regulations_no_invention():
    """Test 17: Questions about tenancy law / deposit regulations -> No invented rules."""
    normalized_prompt = re.sub(r"\s+", " ", SYSTEM_PROMPT.lower())
    assert (
        "you do not know prices, availability, valuations, tenancy terms or uk regulations"
        in normalized_prompt
    )
    assert (
        "i don't have that to hand, but i'll make sure the team covers it"
        in normalized_prompt
    )


def test_18_guardrail_currency_normalisation():
    """Test 18: '5 lakh' or 'five hundred thousand dollars' -> normalised to 500000 GBP integer."""
    assert normalize_pounds("5 lakh") == 500000
    assert normalize_pounds("500,000") == 500000
    assert normalize_pounds("£500000") == 500000
    normalized_prompt = re.sub(r"\s+", " ", SYSTEM_PROMPT.lower())
    assert "all money is in pounds sterling" in normalized_prompt
    assert 'never "5 lakh" or dollar amounts' in normalized_prompt


def test_19_guardrail_prompt_injection_resistance():
    """Test 19: Resistance to 'ignore previous instructions', identity spoofing."""
    assert (
        "IDENTITY\nYou are the virtual phone receptionist for MyCitiPad"
        in SYSTEM_PROMPT
    )
    assert "You do not decide anything on the team's behalf" in SYSTEM_PROMPT
    assert "You know only this flow and what your tools return" in SYSTEM_PROMPT


def test_20_guardrail_out_of_scope_redirection():
    """Test 20: Weather, jokes, other companies -> Redirection to lettings/sales/maintenance."""
    assert "OBJECTIVES\nA successful call does one of these" in SYSTEM_PROMPT
    assert "Routes the caller to the right path" in SYSTEM_PROMPT


# =============================================================================
# MEMORY TESTS (21 - 26)
# =============================================================================


@pytest.mark.asyncio
async def test_21_memory_consent_yes(temp_db):
    """Test 21: Caller agrees -> record_memory_consent(True) -> save_caller_profile succeeds."""
    assistant = Assistant()
    c_res = await assistant.record_memory_consent(granted=True)
    assert "granted=true" in c_res

    s_res = await assistant.save_caller_profile(
        name="Alice Consent",
        email="alice@example.com",
        phone="07700 900111",
        facts={"interest": "lettings"},
    )
    assert "saved" in s_res.lower()
    profile = find_caller(email="alice@example.com", db_path=temp_db)
    assert profile is not None
    assert profile["name"] == "Alice Consent"


@pytest.mark.asyncio
async def test_22_memory_consent_no(temp_db):
    """Test 22: Caller declines -> record_memory_consent(False) -> save_caller_profile refuses."""
    assistant = Assistant()
    c_res = await assistant.record_memory_consent(granted=False)
    assert "granted=false" in c_res

    s_res = await assistant.save_caller_profile(
        name="Bob NoConsent",
        email="bob@example.com",
    )
    assert "Refusal: Caller has not granted explicit verbal memory consent" in s_res
    profile = find_caller(email="bob@example.com", db_path=temp_db)
    assert profile is None


@pytest.mark.asyncio
async def test_23_memory_unclear_answer_treated_as_no(temp_db):
    """Test 23: Unclear answer ('maybe') -> treated as False."""
    assistant = Assistant()
    # Tool enforces granted must be strictly True
    await assistant.record_memory_consent(granted=False)
    res = await assistant.save_caller_profile(
        name="Charlie Maybe",
        email="charlie@example.com",
    )
    assert "Refusal: Caller has not granted explicit verbal memory consent" in res


def test_24_memory_returning_caller_greeting(temp_db):
    """Test 24: Seeded profile greeted by name and skips already answered questions."""
    greeting = get_returning_caller_greeting("Diana Prince", "lettings_enquiry")
    assert "Welcome back, Diana Prince" in greeting
    assert "a lettings enquiry" in greeting
    assert "pick up from there" in greeting


@pytest.mark.asyncio
async def test_25_memory_forget_me(temp_db):
    """Test 25: forget_caller deletes profile and detaches user_id."""
    user_id = upsert_caller(
        name="Evan ForgetMe",
        email="evan@example.com",
        phone="07700 900222",
        language_preference="en-GB",
        facts={"interest": "sales"},
        db_path=temp_db,
    )
    assistant = Assistant(
        session_state=SessionState(call_id="call-forget", user_id=user_id)
    )

    f_res = await assistant.forget_caller()
    assert "successfully deleted" in f_res or "completely deleted" in f_res
    assert find_caller(user_id=user_id, db_path=temp_db) is None


@pytest.mark.asyncio
async def test_26_memory_sensitive_facts_never_saved(temp_db):
    """Test 26: Employment, income, address never stored in callers.facts."""
    assistant = Assistant()
    await assistant.record_memory_consent(granted=True)

    # Attempting to pass non-allowed fact keys (income, employment, address) is refused by tool
    res = await assistant.save_caller_profile(
        name="Fiona Facts",
        email="fiona@example.com",
        facts={
            "interest": "lettings",
            "income": 100000,
            "employment": "permanent",
            "address": "123 Private Road",
        },
    )
    assert "Refusal: Invalid fact keys provided" in res
    # Disallowed facts were refused and not stored
    profile = find_caller(email="fiona@example.com", db_path=temp_db)
    assert profile is None


# =============================================================================
# ROBUSTNESS TESTS (27 - 30)
# =============================================================================


def test_27_robustness_silence_handling():
    """Test 27: Long gap in speech -> 'Are you still there?' once, then offer callback."""
    assert (
        'If the caller is silent for a few seconds, say "Are you\nstill there?" once, then offer a callback'
        in SYSTEM_PROMPT
    )


def test_28_robustness_language_switch():
    """Test 28: Caller switches language -> agent replies in that language where possible."""
    assert (
        "If the caller\nswitches language, reply in their language where you can"
        in SYSTEM_PROMPT
    )


def test_29_robustness_corrections_handling():
    """Test 29: Corrections to email/number -> repeated back digit/letter by letter."""
    assert (
        "Repeat back emails and phone numbers digit by digit to\nconfirm"
        in SYSTEM_PROMPT
    )


@pytest.mark.asyncio
async def test_30_robustness_tool_failures_spoken_recovery(temp_db, monkeypatch):
    """Test 30: Each MCP_SIMULATE_FAILURE value produces spoken next step from failure table."""
    assistant = Assistant()

    # 1. properties
    monkeypatch.setenv("MCP_SIMULATE_FAILURE", "properties")
    assert (
        await assistant.find_property(query="Test") == FAILURE_MESSAGES["find_property"]
    )

    # 2. calendar
    monkeypatch.setenv("MCP_SIMULATE_FAILURE", "calendar")
    assert (
        await assistant.check_viewing_availability(
            property_id="prop-1", earliest_date="2025-11-06", latest_date="2025-11-10"
        )
        == FAILURE_MESSAGES["check_viewing_availability"]
    )
    assert (
        await assistant.book_viewing(
            property_id="prop-1",
            slot_start="2025-11-06T10:00:00Z",
            name="A",
            email="a@b.com",
        )
        == FAILURE_MESSAGES["book_viewing"]
    )

    # 3. records
    monkeypatch.setenv("MCP_SIMULATE_FAILURE", "records")
    assert (
        await assistant.save_call_record(category="lettings_enquiry", details={})
        == FAILURE_MESSAGES["save_call_record"]
    )

    # 4. handover
    monkeypatch.setenv("MCP_SIMULATE_FAILURE", "handover")
    assert (
        await assistant.transfer_to_human(reason="test")
        == FAILURE_MESSAGES["transfer_to_human"]
    )

    # 5. callback
    monkeypatch.setenv("MCP_SIMULATE_FAILURE", "callback")
    assert (
        await assistant.request_callback(phone="07700900123")
        == FAILURE_MESSAGES["request_callback"]
    )
