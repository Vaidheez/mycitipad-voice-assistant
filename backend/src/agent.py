import asyncio
import contextlib
import json
import logging
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from google.genai import types
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    room_io,
    tokenize,
)
from livekit.agents.voice.events import ConversationItemAddedEvent
from livekit.plugins import deepgram, google, murf, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

try:
    from db import (
        append_transcript,
        find_caller,
        start_call_log,
        update_call_log,
    )
    from memory_tools import MemoryToolsMixin, SessionState
    from outbound_guard import mask_phone
    from prompt import (
        GREETING,
        get_returning_caller_greeting,
        get_system_prompt,
    )
    from tools import DomainToolsMixin
except ImportError:
    from .db import (
        append_transcript,
        find_caller,
        start_call_log,
        update_call_log,
    )
    from .memory_tools import MemoryToolsMixin, SessionState
    from .outbound_guard import mask_phone
    from .prompt import (
        GREETING,
        get_returning_caller_greeting,
        get_system_prompt,
    )
    from .tools import DomainToolsMixin

logger = logging.getLogger("agent")

load_dotenv(".env.local")


class Assistant(MemoryToolsMixin, DomainToolsMixin, Agent):
    """MyCitiPad voice virtual receptionist with persistent memory tools."""

    def __init__(
        self,
        session_state: SessionState | None = None,
        instructions: str | None = None,
        **kwargs,
    ) -> None:
        if instructions is None:
            instructions = get_system_prompt()
        super().__init__(instructions=instructions, **kwargs)
        self.session_state = session_state or SessionState(call_id="")


def log_call_details(
    call_id: str,
    category: str,
    outcome: str,
    details: dict,
) -> None:
    """Extension point for Phase 4 domain tools to store standard call details.

    Args:
        call_id: Unique call session ID
        category: lettings_enquiry | viewing | emergency | maintenance_request | sales_lead | other
        outcome: Business outcome (e.g. details_saved, human_requested, callback_requested)
        details: Standard qualification details gathered for the human team
    """
    update_call_log(
        call_id,
        category=category,
        outcome=outcome,
        details=details,
    )


server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


async def _handle_smoke_mode(ctx: JobContext, meta: dict) -> None:
    """Stage C Smoke Test path: wait for connector participant, say fixed line, hang up."""
    outbound_id = meta.get("outbound_id", "")
    logger.info(
        "Outbound smoke test agent dispatched (outbound_id=%s, room=%s)",
        outbound_id,
        ctx.room.name,
    )

    # 1. Join room
    await ctx.connect()

    # 2. Wait for connector participant to answer
    expected_identity = f"phone-{outbound_id}" if outbound_id else None
    target_kinds = [
        rtc.ParticipantKind.PARTICIPANT_KIND_CONNECTOR,
        rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
        rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
        5,
        7,
    ]
    ring_timeout = float(os.getenv("OUTBOUND_RING_TIMEOUT_S", "75"))
    logger.info(
        "Waiting for connector participant (identity=%s, timeout=%.1fs)...",
        expected_identity,
        ring_timeout,
    )
    wait_start = time.time()
    wait_task = asyncio.create_task(
        ctx.wait_for_participant(
            identity=expected_identity,
            kind=target_kinds,
        )
    )

    stream_warned = False

    async def _log_participants_periodically():
        nonlocal stream_warned
        while not wait_task.done():
            await asyncio.sleep(5.0)
            if wait_task.done():
                break
            elapsed = time.time() - wait_start
            remotes = list(ctx.room.remote_participants.values())
            if not remotes:
                logger.info(
                    "[WAITING FOR CALLER] Elapsed: %.1fs - 0 remote participants in room",
                    elapsed,
                )
            else:
                parts_info = []
                for p in remotes:
                    masked_id = (
                        mask_phone(p.identity) if "+" in p.identity else p.identity
                    )
                    kind_name = (
                        rtc.ParticipantKind.Name(p.kind)
                        if hasattr(rtc.ParticipantKind, "Name")
                        else str(p.kind)
                    )
                    active = p.state == rtc.ParticipantState.PARTICIPANT_STATE_ACTIVE
                    parts_info.append(
                        f"identity='{masked_id}', kind={kind_name}({p.kind}), active={active}"
                    )
                logger.info(
                    "[WAITING FOR CALLER] Elapsed: %.1fs - %d remote participant(s): [%s]",
                    elapsed,
                    len(remotes),
                    "; ".join(parts_info),
                )

            # Check if Twilio status reached answered in DB without connector participant or failed
            if outbound_id:
                try:
                    from db import get_outbound_call

                    row = get_outbound_call(outbound_id)
                    if row and row.get("status") in ("failed", "no_answer"):
                        if not wait_task.done():
                            wait_task.cancel()
                        break
                    if (
                        not stream_warned
                        and elapsed >= 15.0
                        and row
                        and row.get("status") == "answered"
                        and not remotes
                    ):
                        stream_warned = True
                        logger.error(
                            "Twilio status is 'in-progress' but no connector participant is visible after 15 seconds: answered but stream not connected (outbound_id=%s)",
                            outbound_id,
                        )
                except Exception:
                    pass

    log_task = asyncio.create_task(_log_participants_periodically())

    try:
        participant = await asyncio.wait_for(
            asyncio.shield(wait_task), timeout=ring_timeout
        )
        elapsed = time.time() - wait_start
        logger.info(
            "Connector participant answered in %.1fs: identity=%s (kind=%s)",
            elapsed,
            participant.identity,
            participant.kind,
        )
    except Exception as exc:
        elapsed = time.time() - wait_start
        exc_type = type(exc).__name__
        logger.warning(
            "Connector participant did not join in time (elapsed: %.1fs, exception: %s: %s)",
            elapsed,
            exc_type,
            exc or "(timeout)",
        )
        if outbound_id:
            try:
                from db import update_outbound

                update_outbound(
                    outbound_id,
                    status="no_answer",
                    failure_reason=f"Wait timed out after {elapsed:.1f}s ({exc_type})",
                    ended_at=datetime.now(timezone.utc).isoformat(),
                )
            except Exception:
                pass
        await ctx.delete_room()
        return
    finally:
        log_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await log_task
        if not wait_task.done():
            wait_task.cancel()

    # 3. Setup voice session for Murf Falcon TTS playback, explicitly binding to connector participant
    session = AgentSession(
        tts=murf.TTS(
            voice="Hazel",
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True,
        ),
    )
    await session.start(
        agent=Agent(instructions="Smoke test line playback"),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            participant_identity=participant.identity,
            participant_kinds=target_kinds,
        ),
    )

    # 4. Say fixed line
    brand_spoken = "My Citi Pad"
    try:
        from config import BRAND_SPOKEN

        brand_spoken = BRAND_SPOKEN
    except ImportError:
        pass
    smoke_phrase = f"This is a test call from {brand_spoken}. Goodbye."
    logger.info("Speaking smoke test line: '%s'", smoke_phrase)
    handle = await session.say(smoke_phrase, allow_interruptions=False)
    if handle:
        await handle

    # Wait for audio playout to finish
    await asyncio.sleep(2.0)

    # 5. Hang up and record completion
    logger.info("Smoke test line completed. Hanging up.")
    call_sid = meta.get("call_sid")
    if outbound_id:
        try:
            from db import get_outbound_call, update_outbound

            update_outbound(
                outbound_id,
                status="completed",
                ended_at=datetime.now(timezone.utc).isoformat(),
            )
            row = get_outbound_call(outbound_id)
            if row and row.get("twilio_call_sid"):
                call_sid = row["twilio_call_sid"]
        except Exception:
            pass

    try:
        from telephony_twilio import hang_up

        await hang_up(call_sid, ctx.room.name)
    except Exception as exc:
        logger.warning("Error hanging up smoke test call: %s", exc)
        await ctx.delete_room()


@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext):
    # Check for outbound mode in job dispatch metadata
    metadata_str = getattr(ctx.job, "metadata", "") or "{}"
    try:
        job_meta = json.loads(metadata_str)
    except Exception:
        job_meta = {}

    mode = job_meta.get("mode")
    if mode == "smoke":
        await _handle_smoke_mode(ctx, job_meta)
        return

    # Logging setup
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # Start business call log on session start
    call_id = start_call_log()
    session_state = SessionState(call_id=call_id)
    assistant = Assistant(session_state=session_state)

    # Set up a voice AI pipeline using Murf Falcon, Gemini, Deepgram, and the LiveKit turn detector
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="multi"),
        llm=google.LLM(
            model=gemini_model,
            http_options=types.HttpOptions(timeout=15000),
        ),
        tts=murf.TTS(
            voice="Hazel",
            style="Conversation",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
            text_pacing=True,
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    # Transcript recording: append user & agent speech turns to call log
    @session.on("conversation_item_added")
    def on_conversation_item(event: ConversationItemAddedEvent):
        try:
            if hasattr(event, "item") and hasattr(event.item, "role"):
                role = "caller" if event.item.role == "user" else "agent"
                text = getattr(event.item, "text_content", "") or ""
                if text.strip():
                    append_transcript(call_id, role, text)
        except Exception as exc:
            logger.error("Failed to append transcript turn: %s", exc)

    # Close handler: finalize call log with ended_at timestamp
    @session.on("close")
    def on_session_close(*_args):
        try:
            update_call_log(
                call_id,
                ended_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            logger.error("Failed to finalize call log on session close: %s", exc)

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=assistant,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind
                    == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )

    # Join the room and connect to the user
    await ctx.connect()

    # Wait for the participant to read metadata/attributes
    participant = await ctx.wait_for_participant()

    # Look up returning caller by browser-generated or SIP caller ID
    caller_id = None
    if participant.attributes:
        caller_id = participant.attributes.get(
            "mcp_caller_id"
        ) or participant.attributes.get("sip.phoneNumber")
    if not caller_id and participant.metadata:
        try:
            meta = json.loads(participant.metadata)
            caller_id = meta.get("mcp_caller_id")
        except Exception:
            pass

    greeting = GREETING
    if caller_id:
        try:
            profile = find_caller(user_id=caller_id, phone=caller_id)
            if profile:
                session_state.user_id = profile["user_id"]
                name = profile["name"]
                last_request = profile.get("facts", {}).get("last_request_type")
                greeting = get_returning_caller_greeting(name, last_request)
                logger.info(
                    "Recognized returning caller %s (id=%s)", name, profile["user_id"]
                )
        except Exception as exc:
            logger.error("Error looking up returning caller: %s", exc)
            greeting = GREETING

    # Deliver initial spoken greeting (personalized for returning callers, or standard first-turn)
    await session.say(greeting, allow_interruptions=True)


if __name__ == "__main__":
    cli.run_app(server)
