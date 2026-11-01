"""Twilio Voice API and LiveKit Twilio Connector integration for MyCitiPad.

Handles outbound call lifecycle:
1. LiveKit ConnectTwilioCall (direction OUTBOUND) creates room & connector participant.
2. Twilio REST call creation points to LiveKit connector URL.
3. Status watcher monitors ringing/answered states and cleans up on terminal states.
4. Clean teardown terminates Twilio call and deletes LiveKit room.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
from collections import namedtuple
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from livekit import api
from twilio.rest import Client as TwilioClient

try:
    from config import (
        AGENT_NAME,
        OUTBOUND_RING_TIMEOUT_S,
        TWILIO_ACCOUNT_SID,
        TWILIO_AUTH_TOKEN,
        TWILIO_FROM_NUMBER,
    )
    from db import normalize_phone, update_outbound
    from outbound_guard import mask_phone
except ImportError:
    from .config import (
        AGENT_NAME,
        OUTBOUND_RING_TIMEOUT_S,
        TWILIO_ACCOUNT_SID,
        TWILIO_AUTH_TOKEN,
        TWILIO_FROM_NUMBER,
    )
    from .db import normalize_phone, update_outbound
    from .outbound_guard import mask_phone

logger = logging.getLogger("mycitipad.telephony_twilio")

CallHandle = namedtuple("CallHandle", ["room_name", "call_sid"])
TERMINAL_STATUSES = {"completed", "busy", "no-answer", "failed", "canceled"}

_shared_twilio_client: TwilioClient | None = None


def sanitize_error(exc: Any) -> str:
    """Sanitize error messages to ensure no tokens, account SIDs, or full phones are leaked.

    For Twilio errors, outputs only the Twilio error code and a one-line summary.
    For LiveKit errors, labels them accurately as LiveKit errors without raw dumps.
    """
    mod = getattr(type(exc), "__module__", "").lower()
    name = type(exc).__name__.lower()

    # 1. LiveKit error
    if "livekit" in mod or "twirp" in mod:
        code = getattr(exc, "code", None) or getattr(exc, "status", None) or "unknown"
        msg = getattr(exc, "message", None) or getattr(exc, "msg", None) or str(exc)
        clean_msg = re.sub(r"https?://[^\s]+", "", str(msg))
        clean_msg = re.sub(r"\s+", " ", clean_msg).strip()
        return f"LiveKit error {code}: {clean_msg}"

    # 2. TwilioRestException or object with Twilio attributes
    if "twilio" in mod or "twilio" in name or hasattr(exc, "code"):
        code = getattr(exc, "code", None)
        status_code = getattr(exc, "status", None)
        msg = getattr(exc, "msg", None) or getattr(exc, "message", None)

        err_code = code or status_code or "unknown"
        clean_msg = str(msg or "").strip()
        # Remove raw URL paths like /2010-04-01/Accounts/.../Calls/...
        clean_msg = re.sub(r"/2010-04-01/Accounts/[^\s]+", "", clean_msg)
        clean_msg = re.sub(r"/Accounts/[^\s]+", "", clean_msg)
        clean_msg = re.sub(r"https?://[^\s]+", "", clean_msg)
        clean_msg = re.sub(r"AC[a-fA-F0-9]{32}", "AC[REDACTED_SID]", clean_msg)
        clean_msg = re.sub(r"\b[a-fA-F0-9]{32}\b", "[REDACTED_TOKEN]", clean_msg)
        clean_msg = re.sub(r"(\+[1-9]\d{5,10})(\d{4})", r"***\2", clean_msg)
        clean_msg = re.sub(r"\s+", " ", clean_msg).strip()
        if not clean_msg:
            clean_msg = "Request failed"
        return f"Twilio error {err_code}: {clean_msg}"

    text = str(exc).strip()

    # 3. String representation fallback for Twilio formatted errors
    if (
        "Twilio returned the following information:" in text
        or "https://www.twilio.com/docs/errors/" in text
        or "HTTP Error Your request was:" in text
    ):
        code_match = re.search(r"errors/(\d+)", text) or re.search(
            r"\bcode\s*[:=]\s*(\d+)", text, re.I
        )
        err_code = code_match.group(1) if code_match else "unknown"

        info_match = re.search(
            r"Twilio returned the following information:\s*\n+([^\n]+)", text
        )
        if info_match:
            clean_msg = info_match.group(1).strip()
        else:
            line_match = re.search(r"(Unable to [^\n]+)", text)
            clean_msg = line_match.group(1).strip() if line_match else "Request failed"

        clean_msg = re.sub(r"/2010-04-01/Accounts/[^\s]+", "", clean_msg)
        clean_msg = re.sub(r"/Accounts/[^\s]+", "", clean_msg)
        clean_msg = re.sub(r"https?://[^\s]+", "", clean_msg)
        clean_msg = re.sub(r"AC[a-fA-F0-9]{32}", "AC[REDACTED_SID]", clean_msg)
        clean_msg = re.sub(r"\b[a-fA-F0-9]{32}\b", "[REDACTED_TOKEN]", clean_msg)
        clean_msg = re.sub(r"(\+[1-9]\d{5,10})(\d{4})", r"***\2", clean_msg)
        clean_msg = re.sub(r"\s+", " ", clean_msg).strip()
        return f"Twilio error {err_code}: {clean_msg}"

    # 4. General non-Twilio error: strip credentials, phones, and newlines
    text = re.sub(r"AC[a-fA-F0-9]{32}", "AC[REDACTED_SID]", text)
    text = re.sub(r"\b[a-fA-F0-9]{32}\b", "[REDACTED_TOKEN]", text)
    text = re.sub(r"(\+[1-9]\d{5,10})(\d{4})", r"***\2", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def get_twilio_client() -> TwilioClient:
    """Instantiate or return the shared Twilio REST API client using environment credentials."""
    global _shared_twilio_client
    sid = os.getenv("TWILIO_ACCOUNT_SID") or TWILIO_ACCOUNT_SID
    token = os.getenv("TWILIO_AUTH_TOKEN") or TWILIO_AUTH_TOKEN
    if not sid or not token:
        raise ValueError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN must be configured")
    if (
        _shared_twilio_client is None
        or getattr(_shared_twilio_client, "account_sid", None) != sid
    ):
        _shared_twilio_client = TwilioClient(sid, token)
    return _shared_twilio_client


async def start_outbound_call(
    to_phone: str,
    outbound_id: str,
    metadata: dict[str, Any] | None = None,
    with_agent: bool = True,
    livekit_api_client: api.LiveKitAPI | None = None,
    twilio_client: TwilioClient | None = None,
    db_path: str | None = None,
) -> CallHandle:
    """Initiate an outbound call via LiveKit Twilio Connector and Twilio Voice API.

    Args:
        to_phone: E.164 recipient phone number
        outbound_id: Unique outbound identifier
        metadata: Dispatch metadata dict passed to LiveKit agent
        with_agent: If True, dispatches AGENT_NAME agent to the room
        livekit_api_client: Optional LiveKitAPI client for dependency injection / testing
        twilio_client: Optional Twilio Client for dependency injection / testing
        db_path: Optional SQLite database path

    Returns:
        CallHandle(room_name, call_sid)
    """
    clean_to = normalize_phone(to_phone) or to_phone
    from_number = os.getenv("TWILIO_FROM_NUMBER") or TWILIO_FROM_NUMBER
    agent_name = os.getenv("AGENT_NAME") or AGENT_NAME

    room_name = f"outbound-{outbound_id}"
    participant_identity = f"phone-{outbound_id}"

    lk_api = livekit_api_client or api.LiveKitAPI()
    tw_client = twilio_client or get_twilio_client()

    meta_payload = metadata or {}
    meta_payload.setdefault("outbound_id", outbound_id)
    meta_payload.setdefault("to_phone", clean_to)

    agents = []
    if with_agent:
        agents.append(
            api.RoomAgentDispatch(
                agent_name=agent_name,
                metadata=json.dumps(meta_payload),
            )
        )

    logger.info(
        "Requesting LiveKit Twilio Connector for room=%s, recipient=%s (with_agent=%s)",
        room_name,
        mask_phone(clean_to),
        with_agent,
    )

    try:
        # Step 1: ConnectTwilioCall to LiveKit
        connect_req = api.ConnectTwilioCallRequest(
            twilio_call_direction=api.ConnectTwilioCallRequest.TWILIO_CALL_DIRECTION_OUTBOUND,
            room_name=room_name,
            participant_identity=participant_identity,
            participant_name="MyCitiPad caller",
            agents=agents,
        )
        res = await lk_api.connector.connect_twilio_call(connect_req)
        connect_url = res.connect_url

        if not connect_url:
            raise RuntimeError("LiveKit connector did not return a connect_url")

        # Convert wss:// into https:// for Twilio Media Streams webhook URL
        tw_stream_url = connect_url.replace("wss://", "https://")
        parsed_stream = urlparse(tw_stream_url)
        is_https = tw_stream_url.startswith("https://")
        logger.info(
            "LiveKit connector webhook URL: %s://%s/... (starts with https://: %s)",
            parsed_stream.scheme,
            parsed_stream.netloc,
            is_https,
        )
        print(
            f"[TWILIO WEBHOOK] Target URL: {parsed_stream.scheme}://{parsed_stream.netloc}/... (starts with https://: {is_https})"
        )
        print("[TWILIO CALL PARAMS] Parameters passed: ['to', 'from_', 'url']")

    except Exception as exc:
        sanitized = sanitize_error(exc)
        logger.error("LiveKit connect_twilio_call failed: %s", sanitized)
        update_outbound(
            outbound_id,
            status="failed",
            failure_reason=sanitized,
            ended_at=datetime.now(timezone.utc).isoformat(),
            db_path=db_path,
        )
        if livekit_api_client is None:
            await lk_api.aclose()
        raise RuntimeError(f"LiveKit connector failed: {sanitized}") from exc

    # Step 2: Immediately call Twilio REST API to place the call
    try:
        logger.info(
            "Placing Twilio call to=%s from=%s",
            mask_phone(clean_to),
            mask_phone(from_number),
        )
        call = tw_client.calls.create(
            to=clean_to,
            from_=from_number,
            url=tw_stream_url,
        )
        call_sid = call.sid
        logger.info("Twilio call initiated: sid=%s", call_sid)

        update_outbound(
            outbound_id,
            twilio_call_sid=call_sid,
            status="dialling",
            db_path=db_path,
        )
        return CallHandle(room_name=room_name, call_sid=call_sid)

    except Exception as exc:
        sanitized = sanitize_error(exc)
        logger.error("Twilio call creation failed: %s", sanitized)

        # Clean up the LiveKit room since Twilio rejected the call
        try:
            await lk_api.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except Exception as del_err:
            logger.warning("Error deleting room after Twilio rejection: %s", del_err)

        update_outbound(
            outbound_id,
            status="failed",
            failure_reason=sanitized,
            ended_at=datetime.now(timezone.utc).isoformat(),
            db_path=db_path,
        )
        if livekit_api_client is None:
            await lk_api.aclose()
        raise RuntimeError(f"Twilio rejected call: {sanitized}") from exc

    finally:
        if livekit_api_client is None:
            await lk_api.aclose()


async def watch_call_status(
    call_sid: str,
    outbound_id: str,
    room_name: str,
    twilio_client: TwilioClient | None = None,
    livekit_api_client: api.LiveKitAPI | None = None,
    db_path: str | None = None,
    poll_interval: float = 2.0,
    max_wait: float | None = None,
    on_status_change: Any | None = None,
) -> str:
    """Poll Twilio call status until answered, rejected, or completed.

    Maps Twilio status:
    - in-progress -> answered
    - busy -> rejected
    - no-answer -> no_answer
    - failed / canceled -> failed
    - completed (before in-progress) -> no_answer
    - timeout while queued/ringing -> no_answer (cancels Twilio call)

    Deletes the room if final status is not 'answered'.
    """
    tw_client = twilio_client or get_twilio_client()
    lk_api = livekit_api_client or api.LiveKitAPI()
    timeout_s = max_wait if max_wait is not None else (OUTBOUND_RING_TIMEOUT_S + 10)

    start_time = asyncio.get_event_loop().time()
    last_reported_status = None
    ever_in_progress = False

    try:
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            try:
                call = tw_client.calls(call_sid).fetch()
                raw_status = call.status.lower()
            except Exception as fetch_err:
                sanitized = sanitize_error(fetch_err)
                logger.warning(
                    "Failed to fetch Twilio status for %s: %s", call_sid, sanitized
                )
                raw_status = "unknown"

            if raw_status != last_reported_status:
                logger.info(
                    "Call %s status changed to: %s (elapsed=%.1fs)",
                    call_sid,
                    raw_status,
                    elapsed,
                )
                last_reported_status = raw_status
                if on_status_change:
                    with contextlib.suppress(Exception):
                        on_status_change(raw_status)

            # 1. Answered: in-progress
            if raw_status == "in-progress":
                ever_in_progress = True
                update_outbound(
                    outbound_id,
                    status="answered",
                    db_path=db_path,
                )
                logger.info("Call %s answered and in-progress.", call_sid)
                return "answered"

            # 2. Busy / Rejected
            if raw_status == "busy":
                update_outbound(
                    outbound_id,
                    status="rejected",
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    db_path=db_path,
                )
                await _safe_delete_room(lk_api, room_name)
                return "rejected"

            # 3. No answer
            if raw_status == "no-answer":
                update_outbound(
                    outbound_id,
                    status="no_answer",
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    db_path=db_path,
                )
                await _safe_delete_room(lk_api, room_name)
                return "no_answer"

            # 4. Failed or Canceled
            if raw_status in ("failed", "canceled"):
                update_outbound(
                    outbound_id,
                    status="failed",
                    failure_reason=f"Twilio status: {raw_status}",
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    db_path=db_path,
                )
                await _safe_delete_room(lk_api, room_name)
                return "failed"

            # 5. Completed before in-progress -> no_answer
            if raw_status == "completed":
                final_st = "completed" if ever_in_progress else "no_answer"
                update_outbound(
                    outbound_id,
                    status=final_st,
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    db_path=db_path,
                )
                if not ever_in_progress:
                    await _safe_delete_room(lk_api, room_name)
                return final_st

            # 6. Ringing timeout check
            if elapsed >= timeout_s:
                logger.warning(
                    "Ringing timeout (%.1fs) exceeded for call %s", elapsed, call_sid
                )
                try:
                    tw_client.calls(call_sid).update(status="canceled")
                except Exception as cancel_err:
                    logger.warning(
                        "Error canceling timed out call %s: %s", call_sid, cancel_err
                    )

                update_outbound(
                    outbound_id,
                    status="no_answer",
                    failure_reason="Ringing timeout exceeded",
                    ended_at=datetime.now(timezone.utc).isoformat(),
                    db_path=db_path,
                )
                await _safe_delete_room(lk_api, room_name)
                return "no_answer"

            await asyncio.sleep(poll_interval)

    finally:
        if livekit_api_client is None:
            await lk_api.aclose()


async def hang_up(
    call_sid: str | None,
    room_name: str | None,
    twilio_client: TwilioClient | None = None,
    livekit_api_client: api.LiveKitAPI | None = None,
    verify_timeout: float = 5.0,
) -> bool:
    """Terminate the Twilio call and delete the LiveKit room cleanly.

    Order of operations:
    1. Fetch current Twilio call status.
       - If already terminal ('completed', 'busy', 'no-answer', 'failed', 'canceled'),
         skip the update and log "already ended".
       - If active, update status="completed".
    2. Only after handling Twilio call, delete the LiveKit room.
    3. Re-fetch Twilio call and verify it reaches a terminal status within verify_timeout (5s).
       Print final status and duration.
       If not terminal within verify_timeout, print FAIL.

    Swallows all exceptions; this function never raises.
    Returns True if hang up reached clean terminal state, False otherwise.
    """
    sid = os.getenv("TWILIO_ACCOUNT_SID") or TWILIO_ACCOUNT_SID
    tw_client = twilio_client or (get_twilio_client() if sid else None)
    lk_api = livekit_api_client or api.LiveKitAPI()
    is_clean = True

    # 1. Inspect and terminate Twilio call first
    if call_sid and tw_client:
        try:
            logger.info(
                "Fetching Twilio call status for %s before hang-up...", call_sid
            )
            call = tw_client.calls(call_sid).fetch()
            curr_status = (
                call.status.lower()
                if call and getattr(call, "status", None)
                else "unknown"
            )

            if curr_status in TERMINAL_STATUSES:
                logger.info(
                    "Twilio call %s already ended with status '%s'; skipping update.",
                    call_sid,
                    curr_status,
                )
            else:
                logger.info(
                    "Ending Twilio call %s via REST API (current status: %s)...",
                    call_sid,
                    curr_status,
                )
                tw_client.calls(call_sid).update(status="completed")
                logger.info("Twilio call %s update requested.", call_sid)
        except Exception as exc:
            sanitized = sanitize_error(exc)
            logger.warning(
                "Error inspecting or ending Twilio call %s: %s", call_sid, sanitized
            )
            is_clean = False

    # 2. Only after handling Twilio call, delete the LiveKit room
    if room_name:
        await _safe_delete_room(lk_api, room_name)

    # 3. Fetch call again and verify final terminal status and duration
    if call_sid and tw_client:
        start_time = asyncio.get_event_loop().time()
        final_call = None
        reached_terminal = False

        while (asyncio.get_event_loop().time() - start_time) <= verify_timeout:
            try:
                final_call = tw_client.calls(call_sid).fetch()
                st = (
                    final_call.status.lower()
                    if final_call and getattr(final_call, "status", None)
                    else "unknown"
                )
                if st in TERMINAL_STATUSES:
                    reached_terminal = True
                    break
            except Exception as exc:
                sanitized = sanitize_error(exc)
                logger.warning(
                    "Error verifying final status of Twilio call %s: %s",
                    call_sid,
                    sanitized,
                )
                break
            await asyncio.sleep(0.5)

        if final_call and reached_terminal:
            duration = getattr(final_call, "duration", None)
            dur_str = f"{duration}s" if duration is not None else "n/a"
            try:
                final_dur_s = int(duration or 0)
            except (ValueError, TypeError):
                final_dur_s = 0

            st = final_call.status
            logger.info(
                "Twilio call %s finalized: status=%s, duration=%s",
                call_sid,
                st,
                dur_str,
            )
            print(
                f"[HANGUP CLEAN] Twilio call {call_sid} finalized with status '{st}' (duration: {dur_str})."
            )
            if final_dur_s < 10:
                print(
                    "[WARNING] Call ended early: likely trial key-press prompt not answered, or the media stream failed to connect."
                )
            is_clean = True
        else:
            final_st = (
                getattr(final_call, "status", "unknown") if final_call else "unknown"
            )
            logger.error(
                "Twilio call %s did not reach terminal status within %.1fs (final status: %s)",
                call_sid,
                verify_timeout,
                final_st,
            )
            print(
                f"[HANGUP FAIL] Twilio call {call_sid} did not reach terminal status within {verify_timeout:.1f}s (current status: {final_st})."
            )
            is_clean = False

    if livekit_api_client is None:
        with contextlib.suppress(Exception):
            await lk_api.aclose()

    return is_clean


async def _safe_delete_room(lk_api: api.LiveKitAPI, room_name: str) -> None:
    """Delete a LiveKit room safely without raising exceptions."""
    try:
        logger.info("Deleting LiveKit room %s...", room_name)
        await lk_api.room.delete_room(api.DeleteRoomRequest(room=room_name))
        logger.info("LiveKit room %s deleted successfully.", room_name)
    except Exception as exc:
        err_str = str(exc).lower()
        err_code = str(getattr(exc, "code", "")).lower()
        # Treat missing room as already closed rather than an error
        if (
            "not_found" in err_code
            or "does not exist" in err_str
            or "not found" in err_str
        ):
            logger.info("LiveKit room %s is already closed.", room_name)
            return

        sanitized = sanitize_error(exc)
        logger.warning("Could not delete LiveKit room %s: %s", room_name, sanitized)


def get_call_notifications(
    call_sid: str, twilio_client: TwilioClient | None = None
) -> list[dict[str, str]]:
    """Fetch Twilio alerts/notifications for a call SID with clean one-line summaries."""
    if not call_sid:
        return []
    tw_client = twilio_client or (
        get_twilio_client()
        if (os.getenv("TWILIO_ACCOUNT_SID") or TWILIO_ACCOUNT_SID)
        else None
    )
    if not tw_client:
        return []
    try:
        notifications = tw_client.calls(call_sid).notifications.list(limit=10)
        items = []
        for n in notifications:
            err_code = str(getattr(n, "error_code", "unknown") or "unknown")
            msg = getattr(n, "message_text", "") or ""
            clean_msg = sanitize_error(msg)
            clean_msg = re.sub(r"https?://[^\s]+", "", clean_msg)
            clean_msg = re.sub(r"\s+", " ", clean_msg).strip()
            items.append(
                {
                    "error_code": err_code,
                    "summary": clean_msg or "No error summary provided",
                }
            )
        return items
    except Exception as exc:
        logger.debug(
            "Failed fetching notifications for %s: %s",
            call_sid,
            sanitize_error(exc),
        )
        return []
