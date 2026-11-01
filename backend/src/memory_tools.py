"""Persistent memory tools with explicit caller consent for MyCitiPad.

Hard rule: save_caller_profile refuses to write if memory_consent is not True.
Transient or sensitive qualification data (employment, income, guarantor, address)
is strictly prohibited from caller profiles and belongs only in call logs.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any

from livekit.agents import RunContext, function_tool

try:
    from db import delete_caller, find_caller, update_call_log, upsert_caller
except ImportError:
    from .db import delete_caller, find_caller, update_call_log, upsert_caller

logger = logging.getLogger("mycitipad.memory_tools")

# Allowed fact keys for persistent profiles
ALLOWED_FACT_KEYS = {
    "interest",
    "property_of_interest",
    "budget_max_gbp",
    "preferred_contact",
    "last_request_type",
}

# PRIVACY & DATA MINIMISATION NOTICE:
# In accordance with UK data protection and agency policy, the caller profile stores
# only long-term continuity facts with explicit consent (interest, property of interest,
# maximum budget in whole pounds, preferred contact method, and last request type).
# Sensitive qualification attributes (employment status, annual income, guarantor requirements,
# current address, or document uploads) are strictly excluded from persistent caller profiles
# and must only be stored in per-call business logs (`call_logs.details`) for staff review.


@dataclass
class SessionState:
    """Per-call session state for memory and caller identity tracking."""

    call_id: str
    user_id: str | None = None
    memory_consent: bool | None = None


class MemoryToolsMixin:
    """Agent mixin implementing caller lookup, consent recording, profile persistence, and deletion."""

    session_state: SessionState

    @function_tool
    async def lookup_caller(
        self,
        context: RunContext = None,
        email: str | None = None,
        phone: str | None = None,
    ) -> str:
        """Look up whether this caller has spoken to MyCitiPad before and gave permission to be remembered. Call this once, as soon as the caller has given an email address or phone number and you do not already know who they are. Do not call it before you have one of those. Returns the caller's name, language preference and saved facts, or says no profile exists. Never read the stored details back unless the caller has been identified."""
        try:
            if not email and not phone:
                return "Please provide an email address or phone number to look up a profile."

            profile = find_caller(email=email, phone=phone)
            if not profile:
                return "No saved profile."

            self.session_state.user_id = profile["user_id"]
            name = profile["name"]
            lang = profile.get("language_preference", "en-GB")
            facts = profile.get("facts", {})

            return (
                f"Returning caller recognized: name={name}, language={lang}, "
                f"facts={json.dumps(facts)}"
            )
        except Exception as exc:
            logger.error("lookup_caller error: %s", exc)
            return "Lookup unavailable right now"

    @function_tool
    async def record_memory_consent(
        self,
        context: RunContext = None,
        granted: bool = False,
    ) -> str:
        """Record the caller's answer to the question 'Is it okay if I remember these details for next time?'. Call this only immediately after you asked that exact question and the caller answered. granted is true only for a clear yes. A no, silence, or anything unclear is false."""
        try:
            self.session_state.memory_consent = bool(granted)
            if self.session_state.memory_consent:
                return "Consent recorded: granted=true."
            else:
                return "Consent recorded: granted=false."
        except Exception as exc:
            logger.error("record_memory_consent error: %s", exc)
            return "Failed to record consent status."

    @function_tool
    async def save_caller_profile(
        self,
        context: RunContext = None,
        name: str = "",
        email: str | None = None,
        phone: str | None = None,
        language_preference: str | None = "en-GB",
        facts: dict[str, Any] | None = None,
    ) -> str:
        """Save or update the caller's profile for future calls. Only call this after record_memory_consent returned granted=true in this same call. If consent was not given, this tool will refuse. Only the allowed fact keys are accepted (interest, property_of_interest, budget_max_gbp, preferred_contact, last_request_type). Never include employment, income, address or documents. Say the profile is saved only after this tool reports success."""
        # Hard code-level consent enforcement
        if self.session_state.memory_consent is not True:
            logger.warning("save_caller_profile refused: explicit consent not granted.")
            return (
                "Refusal: Caller has not granted explicit verbal memory consent. "
                "Profile not saved."
            )

        if not name or not name.strip():
            return "Refusal: Full name is required to save a caller profile."

        if not email and not phone:
            return "Refusal: At least one contact detail (email or phone) is required."

        facts_dict = facts or {}
        if not isinstance(facts_dict, dict):
            return "Refusal: Facts must be a key-value dictionary."

        # Validate allow-list
        invalid_keys = set(facts_dict.keys()) - ALLOWED_FACT_KEYS
        if invalid_keys:
            return (
                f"Refusal: Invalid fact keys provided: {list(invalid_keys)}. "
                "Only allowed keys are: interest, property_of_interest, "
                "budget_max_gbp, preferred_contact, last_request_type."
            )

        # Validate budget is an integer if provided
        if "budget_max_gbp" in facts_dict:
            budget = facts_dict["budget_max_gbp"]
            if not isinstance(budget, int) or isinstance(budget, bool):
                return (
                    "Refusal: budget_max_gbp must be a whole integer number "
                    "in pounds sterling (e.g. 500000)."
                )

        try:
            user_id = upsert_caller(
                name=name.strip(),
                email=email,
                phone=phone,
                language_preference=language_preference or "en-GB",
                facts=facts_dict,
                user_id=self.session_state.user_id,
            )
            self.session_state.user_id = user_id
            update_call_log(self.session_state.call_id, user_id=user_id)
            return f"Success: Caller profile saved for {name.strip()}."
        except Exception as exc:
            logger.error("save_caller_profile error: %s", exc)
            return "I couldn't save that just now."

    @function_tool
    async def forget_caller(
        self,
        context: RunContext = None,
    ) -> str:
        """Delete the saved profile of the person on this call. Call it when they ask to be forgotten or to have their details removed. Confirm to them only after the tool reports success."""
        try:
            if not self.session_state.user_id:
                return "No identified caller profile exists to delete on this call."

            deleted = delete_caller(self.session_state.user_id)
            if deleted:
                self.session_state.user_id = None
                self.session_state.memory_consent = False
                return (
                    "Success: Your profile has been completely deleted and forgotten."
                )
            else:
                return "I couldn't find a profile to delete."
        except Exception as exc:
            logger.error("forget_caller error: %s", exc)
            return "I couldn't remove your details just now."
