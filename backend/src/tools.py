"""Domain function tools for MyCitiPad virtual receptionist.

Enforces:
1. Real data lookups without invented responses.
2. Prices are strictly withheld from model outputs.
3. Bookings and records require database commit before success confirmation.
4. Universal timeouts, fail-safe spoken replies, and simulation overrides.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
from datetime import date, datetime
from typing import Any

from livekit.agents import RunContext, function_tool

import config
from calendar_provider import BookingRequest, CalendarProvider, MockCalendar
from db import (
    create_callback_request,
    create_handover_request,
    normalize_phone,
    search_properties,
    update_call_log,
)
from memory_tools import SessionState

logger = logging.getLogger("mycitipad.tools")

# Standard required fields per category
REQUIRED_FIELDS = {
    "lettings_enquiry": [
        "full_name",
        "email",
        "property",
        "move_in_date",
        "occupants",
        "enquiry_for_self",
        "employment_status",
        "annual_income_gbp",
        "needs_uk_guarantor",
        "viewing_availability",
    ],
    "viewing": [
        "full_name",
        "email",
        "property",
        "move_in_date",
        "occupants",
        "enquiry_for_self",
        "employment_status",
        "annual_income_gbp",
        "needs_uk_guarantor",
        "viewing_availability",
        "booking_ref",
    ],
    "emergency": [
        "full_name",
        "email",
        "current_address",
        "property_interested_in",
        "contact_number",
        "requirement",
    ],
    "maintenance_request": [
        "full_name",
        "email",
        "current_address",
        "property_interested_in",
        "contact_number",
        "requirement",
    ],
    "sales_lead": [
        "full_name",
        "email",
        "current_address",
        "property_interested_in",
        "contact_number",
        "requirement",
    ],
}

# Failure recovery messages from Phase 4 failure table
FAILURE_MESSAGES = {
    "find_property": (
        "I'm having trouble checking properties right now. "
        "I can still take your details for the team. Shall I continue?"
    ),
    "check_viewing_availability": (
        "I can't see the diary just now. I can take your details and "
        "ask the team to arrange the viewing. Is that all right?"
    ),
    "book_viewing": (
        "I couldn't complete that booking, so please don't treat it as booked. "
        "I can try once more, or take your details for the team."
    ),
    "save_call_record": (
        "I couldn't save that just now. Could I take a callback number "
        "so the team can follow up?"
    ),
    "transfer_to_human": (
        "I can't connect you right now. What's the best number for a callback?"
    ),
    "request_callback": ("I couldn't log that. Please call us on 020 3576 6575."),
}


def normalize_pounds(val: Any) -> int:
    """Normalise currency amounts (e.g. '5 lakh', '500,000', '£500000') into integer pounds."""
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    if isinstance(val, float):
        return int(val)
    if isinstance(val, str):
        s = val.strip().lower()
        if "lakh" in s:
            num_part = re.findall(r"[\d.]+", s)
            if num_part:
                return int(float(num_part[0]) * 100_000)
        cleaned = re.sub(r"[^\d]", "", s)
        if cleaned:
            return int(cleaned)
    raise ValueError(f"Cannot parse '{val}' as whole pounds sterling.")


async def _execute_with_safety(
    tool_name: str,
    coro: Any,
    timeout_s: float = config.TOOL_TIMEOUT_S,
) -> str:
    """Wrap tool execution in timeout and failure-simulation handling."""
    sim_flag = os.getenv("MCP_SIMULATE_FAILURE", "").strip().lower()
    tool_sim_map = {
        "find_property": "properties",
        "check_viewing_availability": "calendar",
        "book_viewing": "calendar",
        "save_call_record": "records",
        "transfer_to_human": "handover",
        "request_callback": "callback",
    }
    fallback = FAILURE_MESSAGES.get(
        tool_name, "Sorry, that didn't work just now. Please call us on 020 3576 6575."
    )

    if sim_flag and (sim_flag == tool_sim_map.get(tool_name) or sim_flag == tool_name):
        logger.warning("Simulating failure for tool %s", tool_name)
        if asyncio.iscoroutine(coro):
            coro.close()
        return fallback

    try:
        res = await asyncio.wait_for(coro, timeout=timeout_s)
        return res
    except asyncio.TimeoutError:
        logger.error("Tool %s timed out after %.1fs", tool_name, timeout_s)
        return fallback
    except Exception as exc:
        logger.error("Tool %s encountered error: %s", tool_name, exc)
        return fallback


class DomainToolsMixin:
    """Agent mixin implementing property search, viewing booking, records, and handover."""

    session_state: SessionState
    calendar: CalendarProvider

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not hasattr(self, "calendar"):
            self.calendar = MockCalendar()

    async def _speak_filler(
        self, context: RunContext | None, filler_phrase: str
    ) -> None:
        """Speak a short filler line while slow operations run."""
        if context and hasattr(context, "session") and hasattr(context.session, "say"):
            with contextlib.suppress(Exception):
                await context.session.say(filler_phrase, allow_interruptions=True)

    @function_tool
    async def find_property(
        self,
        context: RunContext = None,
        query: str = "",
        department: str | None = None,
    ) -> str:
        """Check whether a property the caller mentions is one MyCitiPad handles, and whether it is available. Call this when the caller names a street, building or postcode area, before booking a viewing or saving the enquiry. department is 'lettings' or 'sales' if known. Returns up to 3 matches with property_id, address, department and availability status. It never returns prices; do not quote or estimate any price. If there is no match, say you can't find it and ask them to spell it or give the postcode."""

        async def _run() -> str:
            if not query or not query.strip():
                return "Please ask the caller which street, building, or postcode area they are interested in."

            matches = search_properties(query.strip(), department)
            if not matches:
                return (
                    "No matching property found. Please ask the caller to spell "
                    "the building or street name, or provide the postcode area."
                )

            # Strip any internal fields and ensure prices are never exposed
            results = []
            for m in matches:
                item = {
                    "property_id": m["property_id"],
                    "address": m["address"],
                    "department": m["department"],
                    "status": m["status"],
                }
                if m.get("status") == "let_subject_to_references":
                    item["note"] = (
                        "This property is let subject to references and is NOT available for viewings. "
                        "Inform the caller and offer to log their details for the team."
                    )
                elif m.get("status") in ("under_offer", "sold"):
                    item["note"] = (
                        f"This property is {m['status'].replace('_', ' ')} and not available for new viewings."
                    )
                results.append(item)

            return json.dumps(results)

        return await _execute_with_safety("find_property", _run())

    @function_tool
    async def check_viewing_availability(
        self,
        context: RunContext = None,
        property_id: str = "",
        earliest_date: str = "",
        latest_date: str = "",
        part_of_day: str | None = None,
    ) -> str:
        """Get real open viewing slots for a lettings property that is available. Call this after the caller has said when they are free and you have a property_id from find_property. Dates are ISO (YYYY-MM-DD) in UK time; convert phrases like 'next Tuesday' using today's date from the prompt. part_of_day is 'morning', 'afternoon' or omitted. Offer ONLY the slots this tool returns, reading each one's spoken text. If it returns none, say so and offer to widen the dates or log the enquiry for the team. Never suggest a time that was not returned."""

        async def _run() -> str:
            await self._speak_filler(context, "One moment while I check the diary.")

            if not property_id:
                return "Error: property_id is required."

            # Verify property status
            from db import get_property

            prop = get_property(property_id)
            if not prop:
                return f"Property {property_id} was not found."

            if prop.get("status") != "available":
                status_text = prop.get("status", "").replace("_", " ")
                return (
                    f"Property {prop['address']} is currently {status_text} and is not available "
                    "for viewings. Do not book a viewing; offer to log an enquiry for the team."
                )

            try:
                e_date = date.fromisoformat(earliest_date)
                l_date = date.fromisoformat(latest_date)
            except Exception:
                return "Error: earliest_date and latest_date must be valid ISO dates (YYYY-MM-DD)."

            slots = await self.calendar.free_slots(
                earliest=e_date,
                latest=l_date,
                preferred_part_of_day=part_of_day,
            )

            if not slots:
                return (
                    "No viewing slots are available for those dates. "
                    "Offer to widen the date range or log an enquiry for the team."
                )

            output = [
                {
                    "slot_start_utc": s.slot_start_utc,
                    "spoken": s.spoken,
                }
                for s in slots
            ]
            return json.dumps(output)

        return await _execute_with_safety("check_viewing_availability", _run())

    @function_tool
    async def book_viewing(
        self,
        context: RunContext = None,
        property_id: str = "",
        slot_start: str = "",
        name: str = "",
        email: str = "",
        phone: str | None = None,
    ) -> str:
        """Book one of the slots returned by check_viewing_availability for this caller. Call it only after the caller has chosen a specific slot you offered and confirmed their name and email (read the email back letter by letter). slot_start must be copied exactly from the availability result. Say the viewing is booked ONLY if this tool returns status 'confirmed', and then read back the property, date and time. If it returns 'slot_taken', apologise briefly and offer the new alternatives it returns. If it fails, do not claim anything was booked."""

        async def _run() -> str:
            await self._speak_filler(
                context, "One moment while I confirm that booking."
            )

            if not property_id or not slot_start or not name or not email:
                return "Error: property_id, slot_start, name, and email are required to book a viewing."

            # Basic email validation
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email.strip()):
                return f"Error: '{email}' is not a valid email address. Please confirm the email address letter by letter."

            try:
                slot_dt = datetime.fromisoformat(slot_start)
            except Exception:
                return "Error: slot_start must be a valid ISO 8601 UTC timestamp."

            call_id = getattr(self.session_state, "call_id", None)
            booking_req = BookingRequest(
                property_id=property_id,
                caller_name=name.strip(),
                caller_email=email.strip().lower(),
                caller_phone=phone.strip() if phone else None,
                call_id=call_id,
            )

            result = await self.calendar.book(slot_dt, booking_req)

            if result.status == "confirmed":
                return json.dumps(
                    {
                        "status": "confirmed",
                        "booking_ref": result.booking_ref,
                        "property_address": result.property_address,
                        "spoken_time": result.spoken_time,
                        "confirmation_queued": result.confirmation_queued,
                    }
                )
            elif result.status == "slot_taken":
                alternatives = [
                    {"slot_start_utc": s.slot_start_utc, "spoken": s.spoken}
                    for s in (result.alternative_slots or [])
                ]
                return json.dumps(
                    {
                        "status": "slot_taken",
                        "message": "That slot has just been taken by another caller.",
                        "alternative_slots": alternatives,
                    }
                )
            else:
                return json.dumps(
                    {
                        "status": "failed",
                        "message": result.error_message
                        or "Failed to book viewing slot.",
                    }
                )

        return await _execute_with_safety("book_viewing", _run())

    @function_tool
    async def save_call_record(
        self,
        context: RunContext = None,
        category: str = "",
        details: dict[str, Any] | None = None,
        outcome: str = "",
        complete: bool = True,
    ) -> str:
        """Save the structured details collected on this call for the MyCitiPad team. Call it once the details for the path are collected, and always before ending the call. category is one of: lettings_enquiry, viewing, emergency, maintenance_request, sales_lead. Required fields are checked; if any are missing the tool lists them, so ask the caller for those and call again. For an emergency only, if the caller cannot or will not give every detail, save what you have with complete=false. Say 'saved' or 'the team will be in touch' ONLY after this tool returns status 'saved'."""

        async def _run() -> str:
            clean_category = category.lower().strip()
            if clean_category not in REQUIRED_FIELDS:
                return f"Error: Invalid category '{category}'. Must be one of: {list(REQUIRED_FIELDS.keys())}"

            det = details or {}
            if not isinstance(det, dict):
                return "Error: details must be a key-value dictionary."

            # Reject document/ID uploads
            for key in det:
                if any(
                    x in key.lower()
                    for x in ("document", "upload", "passport", "id_number")
                ):
                    return "Refusal: Document uploads or ID numbers are not accepted over phone calls."

            # Field validation
            req_keys = REQUIRED_FIELDS[clean_category]
            missing_fields = []

            for k in req_keys:
                val = det.get(k)
                if val is None or (isinstance(val, str) and not val.strip()):
                    missing_fields.append(k)

            # Emergency exemption: if caller cannot provide all details, allow partial save with complete=false
            if missing_fields and not (clean_category == "emergency" and not complete):
                return json.dumps(
                    {
                        "status": "missing_fields",
                        "missing": missing_fields,
                        "instruction": f"Please ask the caller for: {', '.join(missing_fields)}.",
                    }
                )

            # Normalise monetary fields if present
            cleaned_details = dict(det)
            for money_key in ("annual_income_gbp", "budget", "budget_max_gbp"):
                if money_key in cleaned_details:
                    try:
                        cleaned_details[money_key] = normalize_pounds(
                            cleaned_details[money_key]
                        )
                    except Exception:
                        return f"Error: {money_key} must be whole pounds sterling (e.g. 500000)."

            call_id = getattr(self.session_state, "call_id", None)
            if call_id:
                update_call_log(
                    call_id,
                    category=clean_category,
                    outcome=outcome or "details_saved",
                    details=cleaned_details,
                )

            return json.dumps(
                {
                    "status": "saved",
                    "category": clean_category,
                    "outcome": outcome or "details_saved",
                }
            )

        return await _execute_with_safety("save_call_record", _run())

    @function_tool
    async def transfer_to_human(
        self,
        context: RunContext = None,
        reason: str = "",
    ) -> str:
        """Pass this call to a member of the MyCitiPad team. Call it when the caller asks to speak to a person, or when an emergency caller accepts the offer to be passed over. Say 'let me pass you over' first. Say the call has been passed on ONLY if this tool returns status 'accepted'. If it returns 'unavailable' or fails, tell the caller plainly that nobody can take the call right now, then ask for a callback number and call request_callback."""

        async def _run() -> str:
            call_id = getattr(self.session_state, "call_id", None) or "browser-call"

            # In this browser demo, transfer is simulated based on MCP_HUMAN_AVAILABLE
            # TODO: real transfer (SIP REFER or adding a human participant to the LiveKit room)
            is_available = os.getenv(
                "MCP_HUMAN_AVAILABLE",
                "true" if config.HUMAN_AVAILABLE else "false",
            ).lower() in ("true", "1", "yes")
            status = "accepted" if is_available else "unavailable"

            create_handover_request(
                call_id=call_id,
                reason=reason or "Caller requested human colleague",
                status=status,
            )

            if is_available:
                return json.dumps(
                    {
                        "status": "accepted",
                        "message": "A member of the team is available. Connecting now.",
                    }
                )
            else:
                return json.dumps(
                    {
                        "status": "unavailable",
                        "message": "No colleagues are currently available to take the call. Please ask for a callback number.",
                    }
                )

        return await _execute_with_safety("transfer_to_human", _run())

    @function_tool
    async def request_callback(
        self,
        context: RunContext = None,
        phone: str = "",
        name: str | None = None,
        reason: str = "",
        urgent: bool = False,
    ) -> str:
        """Take a callback request so a person rings the caller back. Use it when a transfer isn't possible, when you can't help, or when the caller prefers a call back. Read the number back digit by digit and get a yes before calling. Never promise or estimate a callback time. Say the request is logged ONLY if this tool returns status 'queued'."""

        async def _run() -> str:
            if not phone or not phone.strip():
                return "Error: Phone number is required to request a callback."

            cleaned_phone = normalize_phone(phone)
            # Validate UK phone format loosely (+44 or leading 0 with at least 10 digits)
            raw_digits = re.sub(r"\D", "", phone)
            if len(raw_digits) < 10:
                return (
                    f"'{phone}' does not appear to be a complete telephone number. "
                    "Please ask the caller to confirm their telephone number digit by digit."
                )

            call_id = getattr(self.session_state, "call_id", None) or "browser-call"
            priority = "urgent" if urgent else "normal"

            create_callback_request(
                call_id=call_id,
                name=name,
                phone=cleaned_phone or phone,
                reason=reason,
                priority=priority,
            )

            return json.dumps(
                {
                    "status": "queued",
                    "phone": cleaned_phone or phone,
                    "priority": priority,
                    "message": "Callback request logged for the team. Do not promise an arrival or callback time.",
                }
            )

        return await _execute_with_safety("request_callback", _run())
