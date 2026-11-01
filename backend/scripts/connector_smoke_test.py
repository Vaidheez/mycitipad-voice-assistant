"""Stage C: Twilio <-> LiveKit Connector Outbound Smoke Test.

Proves:
1. (Without agent): Twilio Voice API connects call, bridges audio to LiveKit, and creates a visible connector participant.
2. (With agent): LiveKit Agent joins, speaks fixed line via Murf Falcon TTS, and cleanly terminates both Twilio and LiveKit sessions.

Usage:
    uv run python scripts/connector_smoke_test.py --to +91XXXXXXXXXX
    uv run python scripts/connector_smoke_test.py --to +91XXXXXXXXXX --with-agent
"""

import argparse
import asyncio
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("connector_smoke_test")

# Add backend and backend/src to sys.path so imports resolve whether run from root or backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BACKEND_DIR / "src"
for p in (str(BACKEND_DIR), str(SRC_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from dotenv import load_dotenv  # noqa: E402

# Load local environment variables
load_dotenv(BACKEND_DIR / ".env.local")
load_dotenv(BACKEND_DIR / ".env")

from livekit import api  # noqa: E402
from livekit.protocol import models  # noqa: E402

from db import (  # noqa: E402
    create_outbound,
    get_outbound_call,
    get_recent_dialling_call,
    init_db,
    normalize_phone,
    update_outbound,
)
from outbound_guard import check_can_call, mask_phone  # noqa: E402
from telephony_twilio import (  # noqa: E402
    get_call_notifications,
    get_twilio_client,
    hang_up,
    sanitize_error,
    start_outbound_call,
    watch_call_status,
)


async def main_async(
    to_number: str, with_agent: bool, reset_attempts: bool = False
) -> int:
    init_db()

    clean_phone = normalize_phone(to_number) or to_number
    if reset_attempts:
        from db import get_connection

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM outbound_calls WHERE to_phone = ? AND purpose = 'smoke_test'",
                (clean_phone,),
            )
            conn.commit()
        print(
            f"[RESET] Cleared prior smoke test attempts for {mask_phone(clean_phone)}."
        )

    outbound_id = f"smoke-{int(time.time())}-{uuid.uuid4().hex[:6]}"

    print(
        "==============================================================================="
    )
    print(
        "             MyCitiPad Twilio Connector Outbound Smoke Test                    "
    )
    print(
        "==============================================================================="
    )
    print(f"Target:      {mask_phone(clean_phone)}")
    print(
        f"Agent Mode:  {'YES (Agent will speak fixed line)' if with_agent else 'NO (Audio bridge verification)'}"
    )
    print(f"Outbound ID: {outbound_id}")
    print(
        "Notice:      Trial accounts: press any key on your phone when you hear the Twilio message."
    )
    print(
        "-------------------------------------------------------------------------------"
    )

    # Step 1: Pre-dial Guard
    print("[1/4] Running pre-dial compliance and safety guard...")

    # Refuse if an attempt is currently 'dialling' and less than 2 minutes old
    if not reset_attempts:
        active_dial = get_recent_dialling_call(clean_phone, within_seconds=120)
        if active_dial:
            age_s = active_dial.get("age_seconds", 0)
            print(
                f"[GUARD BLOCKED] Outbound call to {mask_phone(clean_phone)} is already 'dialling' "
                f"(started {age_s:.0f}s ago < 2m). Refusing to start another run unless --reset is passed."
            )
            return 1

    ok, reason = check_can_call(
        to_phone=clean_phone,
        purpose="smoke_test",
        record_blocked=True,
        outbound_id=outbound_id,
    )
    if not ok:
        print(f"[GUARD BLOCKED] Outbound call was blocked: {reason}")
        print(
            "Please check credentials, allowlist, suppression list, or test calling hours."
        )
        return 1

    print("[GUARD PASSED] All pre-dial checks passed.")

    # Initialize shared clients
    tw_client = get_twilio_client()
    lk_api = api.LiveKitAPI()

    # Step 2: Record initial attempt
    create_outbound(
        outbound_id=outbound_id,
        purpose="smoke_test",
        to_phone=clean_phone,
        status="dialling",
    )

    call_start_time = time.time()
    call_answered = False
    connector_visible = False
    connector_joined_time = None
    hangup_clean = False
    fail_reasons: list[str] = []

    try:
        # Step 3: Initiate LiveKit Connector + Twilio Call using shared client
        print(
            f"[2/4] Initializing LiveKit room and placing Twilio call to {mask_phone(clean_phone)}..."
        )
        metadata = {
            "mode": "smoke",
            "outbound_id": outbound_id,
        }

        try:
            handle = await start_outbound_call(
                to_phone=clean_phone,
                outbound_id=outbound_id,
                metadata=metadata,
                with_agent=with_agent,
                twilio_client=tw_client,
                livekit_api_client=lk_api,
            )
        except Exception as exc:
            sanitized = sanitize_error(exc)
            print(f"[ERROR] Failed to start outbound call: {sanitized}")
            return 1

        print(f"[LIVEKIT ROOM]  {handle.room_name}")
        print(f"[TWILIO CALL]   SID: {handle.call_sid}")
        print(
            f"[STATUS]        Dialling {mask_phone(clean_phone)}... (waiting for recipient to answer)"
        )
        print(
            "                Trial accounts: press any key on your phone when you hear the Twilio message."
        )

        # Step 4: Watch call status until answered or terminal
        def on_status_change(status: str):
            print(f"  -> Twilio call status: {status}")

        final_status = await watch_call_status(
            call_sid=handle.call_sid,
            outbound_id=outbound_id,
            room_name=handle.room_name,
            twilio_client=tw_client,
            livekit_api_client=lk_api,
            on_status_change=on_status_change,
        )

        if final_status != "answered":
            print(
                f"[TERMINATED] Call did not reach in-progress. Final status: {final_status}"
            )
            fail_reasons.append(
                f"Call was not answered (Twilio final status: {final_status})"
            )
            print(
                "==============================================================================="
            )
            print(f"[FAIL] Smoke test failed: {'; '.join(fail_reasons)}")
            print(
                "==============================================================================="
            )
            return 1

        call_answered = True
        answered_time = time.time()
        stream_warned = False
        hidden_logged = False
        print(
            "-------------------------------------------------------------------------------"
        )
        print(f"[ANSWERED] Recipient {mask_phone(clean_phone)} answered the call!")
        print(
            "           Trial accounts: press any key on your phone when you hear the Twilio message."
        )

        if not with_agent:
            print("[NO-AGENT MODE] Proving Twilio <-> LiveKit WebSocket audio bridge.")
            print(
                "Holding room open for 30s to verify CONNECTOR participant and audio bridge..."
            )

            poll_end = time.time() + 30.0
            last_countdown = 30

            while time.time() < poll_end:
                remaining = int(poll_end - time.time())
                if remaining % 5 == 0 and remaining != last_countdown and remaining > 0:
                    print(f"  ... {remaining} seconds remaining")
                    last_countdown = remaining

                # Poll LiveKit room participants to verify audio bridge
                if not connector_visible:
                    try:
                        res = await lk_api.room.list_participants(
                            api.ListParticipantsRequest(room=handle.room_name)
                        )
                        for p in res.participants:
                            is_connector = (
                                p.kind
                                == getattr(models.ParticipantInfo.Kind, "CONNECTOR", 7)
                                or p.kind == 7
                                or p.identity.startswith("phone-")
                            )
                            if is_connector:
                                is_hidden = (
                                    getattr(p.permission, "hidden", False)
                                    if p.permission
                                    else False
                                )
                                raw_id = p.identity
                                masked_id = (
                                    mask_phone(raw_id) if "+" in raw_id else raw_id
                                )
                                if is_hidden:
                                    if not hidden_logged:
                                        hidden_logged = True
                                        print(
                                            f"[CONNECTOR STATUS] CONNECTOR participant present but hidden "
                                            f"(identity='{masked_id}', awaiting call acceptance / key-press)..."
                                        )
                                else:
                                    connector_visible = True
                                    connector_joined_time = (
                                        time.time() - call_start_time
                                    )
                                    print(
                                        f"[BRIDGE VERIFIED] LiveKit CONNECTOR participant is VISIBLE (not hidden): "
                                        f"identity='{masked_id}', kind=CONNECTOR, joined {connector_joined_time:.1f}s after dial."
                                    )
                                    break
                    except Exception as list_err:
                        logger.debug("Participant check: %s", sanitize_error(list_err))

                if (
                    not connector_visible
                    and not stream_warned
                    and (time.time() - answered_time) >= 15.0
                ):
                    stream_warned = True
                    logger.error(
                        "Twilio status is 'in-progress' but no connector participant is visible after 15 seconds: answered but stream not connected"
                    )
                    print(
                        "[STREAM ERROR] Twilio status is 'in-progress' but no connector participant is visible after 15 seconds: answered but stream not connected"
                    )

                await asyncio.sleep(1.0)

            if not connector_visible:
                print(
                    "[BRIDGE FAIL] LiveKit CONNECTOR participant was NOT visible during verification."
                )
                fail_reasons.append(
                    "CONNECTOR participant never became visible (remained hidden or absent)"
                )

            print(
                "[CLEANUP] Verification period elapsed. Hanging up Twilio call and closing room..."
            )
            hangup_clean = await hang_up(
                handle.call_sid,
                handle.room_name,
                twilio_client=tw_client,
                livekit_api_client=lk_api,
            )
            if not hangup_clean:
                fail_reasons.append(
                    "Twilio call hang-up failed to verify clean terminal status"
                )

            print("[TWILIO NOTIFICATIONS] Checking alerts/notifications for call...")
            notifications = get_call_notifications(
                handle.call_sid, twilio_client=tw_client
            )
            if notifications:
                for notif in notifications:
                    print(
                        f"  * Twilio Alert [{notif['error_code']}]: {notif['summary']}"
                    )
            else:
                print("  * No Twilio alerts or notifications recorded.")

            if (
                call_answered
                and connector_visible
                and hangup_clean
                and not fail_reasons
            ):
                update_outbound(
                    outbound_id,
                    status="completed",
                    ended_at=datetime.now(timezone.utc).isoformat(),
                )
                print(
                    "==============================================================================="
                )
                print("[SUCCESS] Smoke test completed successfully!")
                print(f"  * Recipient: {mask_phone(clean_phone)} (answered)")
                print(
                    f"  * Audio Bridge: CONNECTOR participant verified visible ({connector_joined_time:.1f}s)"
                )
                print("  * Teardown: Clean hang-up and LiveKit room closure confirmed")
                print(
                    "==============================================================================="
                )
                return 0
            else:
                update_outbound(
                    outbound_id,
                    status="failed",
                    failure_reason="; ".join(fail_reasons),
                    ended_at=datetime.now(timezone.utc).isoformat(),
                )
                print(
                    "==============================================================================="
                )
                print(f"[FAIL] Smoke test failed: {'; '.join(fail_reasons)}")
                print(
                    "==============================================================================="
                )
                return 1

        else:
            print("[WITH-AGENT MODE] Agent dispatched to room.")
            print("  1. Agent should wait for connector participant to be visible.")
            print("  2. Agent speaks: 'This is a test call from My Citi Pad. Goodbye.'")
            print("  3. Agent automatically hangs up Twilio call and closes room.")
            print("Monitoring agent completion...")

            agent_completed = False
            final_db_status = None
            agent_wait_s = int(os.getenv("OUTBOUND_RING_TIMEOUT_S", "75")) + 20
            for i in range(agent_wait_s):
                await asyncio.sleep(1)
                row = get_outbound_call(outbound_id)
                if row and row.get("status") in ("completed", "failed", "no_answer"):
                    final_db_status = row.get("status")
                    print(f"[AGENT STATUS] Final call status in DB: {final_db_status}")
                    if final_db_status == "completed":
                        agent_completed = True
                    else:
                        fail_msg = row.get("failure_reason") or "unspecified"
                        fail_reasons.append(
                            f"Agent call ended with non-success DB status: '{final_db_status}' ({fail_msg})"
                        )
                    break

                # Every 3 seconds, verify Twilio call has not terminated prematurely
                if i % 3 == 0 and i >= 3:
                    try:
                        tw_call = tw_client.calls(handle.call_sid).fetch()
                        if tw_call.status in (
                            "completed",
                            "canceled",
                            "failed",
                            "busy",
                            "no-answer",
                        ):
                            dur = int(tw_call.duration or 0)
                            if dur < 10:
                                early_reason = (
                                    f"Twilio call ended prematurely after {dur}s before media stream attached "
                                    "(likely trial key-press prompt not answered on phone keypad, or call dropped)"
                                )
                                print(f"[CALL DROPPED] {early_reason}")
                                update_outbound(
                                    outbound_id,
                                    status="failed",
                                    failure_reason=early_reason,
                                    ended_at=datetime.now(timezone.utc).isoformat(),
                                )
                                fail_reasons.append(early_reason)
                                final_db_status = "failed"
                                break
                    except Exception as tw_err:
                        logger.debug("Periodic Twilio status check: %s", tw_err)

            if not agent_completed and not final_db_status:
                print(
                    f"[TIMEOUT] Timed out waiting for agent completion after {agent_wait_s}s. Enforcing hang_up safety net..."
                )
                fail_reasons.append(f"Agent did not complete within {agent_wait_s}s")
                hangup_clean = await hang_up(
                    handle.call_sid,
                    handle.room_name,
                    twilio_client=tw_client,
                    livekit_api_client=lk_api,
                )
            else:
                # Verify final Twilio call status is terminal
                hangup_clean = await hang_up(
                    handle.call_sid,
                    handle.room_name,
                    twilio_client=tw_client,
                    livekit_api_client=lk_api,
                )

            if not hangup_clean:
                fail_reasons.append(
                    "Twilio call hang-up failed to verify clean terminal status"
                )

            print("[TWILIO NOTIFICATIONS] Checking alerts/notifications for call...")
            notifications = get_call_notifications(
                handle.call_sid, twilio_client=tw_client
            )
            if notifications:
                for notif in notifications:
                    print(
                        f"  * Twilio Alert [{notif['error_code']}]: {notif['summary']}"
                    )
            else:
                print("  * No Twilio alerts or notifications recorded.")

            if call_answered and agent_completed and hangup_clean and not fail_reasons:
                print(
                    "==============================================================================="
                )
                print("[SUCCESS] Agent smoke test call finished successfully!")
                print(f"  * Recipient: {mask_phone(clean_phone)} (answered)")
                print("  * Agent speech completed & call ended cleanly")
                print(
                    "==============================================================================="
                )
                return 0
            else:
                print(
                    "==============================================================================="
                )
                print(f"[FAIL] Agent smoke test failed: {'; '.join(fail_reasons)}")
                print(
                    "==============================================================================="
                )
                return 1

    finally:
        await lk_api.aclose()


def main():
    parser = argparse.ArgumentParser(
        description="Twilio <-> LiveKit Connector Outbound Smoke Test"
    )
    parser.add_argument(
        "--to",
        required=True,
        help="Recipient phone number in E.164 format (e.g. +91XXXXXXXXXX)",
    )
    parser.add_argument(
        "--with-agent",
        action="store_true",
        help="If set, dispatches LiveKit agent to speak test phrase and auto hang up",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset previous smoke test attempts for this number before dialling",
    )
    args = parser.parse_args()

    try:
        code = asyncio.run(main_async(args.to, args.with_agent, args.reset))
        sys.exit(code)
    except KeyboardInterrupt:
        print("\nSmoke test interrupted by user.")
        sys.exit(130)


if __name__ == "__main__":
    main()
