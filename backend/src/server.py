"""FastAPI backend server for MyCitiPad Voice Receptionist.

Provides:
- Token minting endpoint (POST /api/token) for LiveKit WebRTC client connection
- Dashboard data endpoint (GET /api/dashboard) and seed endpoint (POST /api/dashboard/seed)
- Properties catalog endpoint (GET /api/properties)
- Health check and system status (GET /health, GET /)
- Automatic background lifecycle management for LiveKit Agent worker (optional)
"""

import json
import logging
import os
import random
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.protobuf import json_format
from livekit import api
from livekit.protocol import agent_dispatch, room

# Ensure local environment variables are loaded
load_dotenv(".env.local")
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mycitipad.fastapi")

# Configuration
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
AGENT_NAME = os.getenv("AGENT_NAME", "my-agent")
START_AGENT_WORKER = os.getenv("START_AGENT_WORKER", "true").lower() in (
    "true",
    "1",
    "yes",
)

try:
    from db import get_connection, seed_properties
except ImportError:
    from .db import get_connection, seed_properties


agent_process: subprocess.Popen | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan manager for background services."""
    global agent_process

    # Seed properties on startup if empty
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM properties")
        if cur.fetchone()[0] == 0:
            logger.info("Initializing database properties seed...")
            seed_properties(conn)
            conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("Database startup check: %s", exc)

    # Optionally spawn LiveKit Agent worker process
    if START_AGENT_WORKER:
        try:
            logger.info(
                "Launching LiveKit Agent worker subprocess (src/agent.py dev)..."
            )
            repo_root = Path(__file__).resolve().parent.parent
            agent_process = subprocess.Popen(
                [sys.executable, "src/agent.py", "dev"],
                cwd=str(repo_root),
            )
            logger.info("Agent worker running with PID: %s", agent_process.pid)
        except Exception as exc:
            logger.error("Failed to spawn agent worker process: %s", exc)

    yield

    # Teardown
    if agent_process:
        logger.info(
            "Terminating LiveKit Agent worker process tree (PID %s)...",
            agent_process.pid,
        )
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(agent_process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                agent_process.terminate()
                agent_process.wait(timeout=5)
        except Exception as exc:
            logger.warning("Error terminating agent process: %s", exc)
        logger.info("Agent worker process stopped.")


app = FastAPI(
    title="MyCitiPad Voice Receptionist API",
    description="FastAPI backend providing LiveKit token generation, SQLite persistence, and operational metrics.",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for Next.js frontend (localhost:3000 and any origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "MyCitiPad Voice Receptionist FastAPI Backend",
        "version": "1.0.0",
        "livekit_url": LIVEKIT_URL,
        "agent_name": AGENT_NAME,
        "worker_running": agent_process is not None and agent_process.poll() is None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.api_route("/api/token", methods=["GET", "POST"])
async def create_token(request: Request):
    """Mint a LiveKit WebRTC token for the frontend voice client."""
    if not LIVEKIT_URL or not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LiveKit credentials (LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET) not fully configured in backend .env.local",
        )

    caller_id = request.headers.get("x-mcp-caller-id")
    body: dict[str, Any] = {}
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}

    if not caller_id:
        caller_id = (
            body.get("participantAttributes", {}).get("mcp_caller_id")
            or body.get("participant_attributes", {}).get("mcp_caller_id")
            or body.get("mcp_caller_id")
        )

    # Generate unique room and participant identities
    random_id = random.randint(1000, 9999)
    room_name = f"voice_assistant_room_{random_id}"
    participant_name = "user"
    participant_identity = (
        str(caller_id) if caller_id else f"voice_assistant_user_{random_id}"
    )

    try:
        # Build Room Configuration with Agent Dispatch
        room_config = room.RoomConfiguration()
        if body and "room_config" in body:
            try:
                json_format.ParseDict(
                    body["room_config"], room_config, ignore_unknown_fields=True
                )
            except Exception as e:
                logger.warning("Could not parse client room_config: %s", e)

        if not room_config.agents:
            agent_dispatch_name = (
                body.get("agent_name")
                or body.get("agentName")
                or AGENT_NAME
                or "my-agent"
            )
            room_config.agents.append(
                agent_dispatch.RoomAgentDispatch(agent_name=agent_dispatch_name)
            )

        # Create LiveKit access token with video/audio grants and agent dispatch
        token = (
            api.AccessToken(api_key=LIVEKIT_API_KEY, api_secret=LIVEKIT_API_SECRET)
            .with_identity(participant_identity)
            .with_name(participant_name)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .with_room_config(room_config)
        )

        # Attach caller attributes if available
        if caller_id:
            token.with_attributes({"mcp_caller_id": str(caller_id)})
            token.with_metadata(json.dumps({"mcp_caller_id": str(caller_id)}))

        jwt_token = token.to_jwt()

        return JSONResponse(
            content={
                "serverUrl": LIVEKIT_URL,
                "roomName": room_name,
                "participantName": participant_name,
                "participantToken": jwt_token,
            },
            headers={"Cache-Control": "no-store"},
        )
    except Exception as exc:
        logger.error("Failed to generate token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token generation failed: {exc}",
        ) from exc


@app.get("/api/dashboard")
def get_dashboard_data():
    """Retrieve operational metrics, bookings, call logs, and caller memory from SQLite."""
    try:
        conn = get_connection()
        conn.row_factory = None  # Use dicts

        cur = conn.cursor()

        # 1. KPI Counts
        cur.execute("SELECT COUNT(*) FROM call_logs")
        total_calls = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM bookings")
        total_bookings = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM callback_requests WHERE status = 'pending'")
        pending_callbacks = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM call_logs WHERE category = 'emergency'")
        emergencies = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM call_logs WHERE category = 'maintenance_request'"
        )
        maintenance_requests = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM call_logs WHERE category = 'sales_lead'")
        sales_leads = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM callers")
        caller_profiles = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM properties")
        properties_count = cur.fetchone()[0]

        # 2. Bookings
        cur.execute(
            """SELECT b.booking_ref, b.property_id, b.slot_start, b.slot_end,
                      b.caller_name, b.caller_email, b.caller_phone, b.status, b.created_at,
                      p.address as property_address, p.price_gbp, p.department
               FROM bookings b
               LEFT JOIN properties p ON b.property_id = p.property_id
               ORDER BY b.created_at DESC"""
        )
        booking_cols = [
            "booking_ref",
            "property_id",
            "slot_start",
            "slot_end",
            "caller_name",
            "caller_email",
            "caller_phone",
            "status",
            "created_at",
            "property_address",
            "price_gbp",
            "department",
        ]
        bookings = [dict(zip(booking_cols, row)) for row in cur.fetchall()]

        # 3. Call Logs
        cur.execute(
            """SELECT call_id, user_id, started_at, ended_at, category, outcome, details, transcript
               FROM call_logs
               ORDER BY started_at DESC
               LIMIT 100"""
        )
        log_cols = [
            "call_id",
            "user_id",
            "started_at",
            "ended_at",
            "category",
            "outcome",
            "details",
            "transcript",
        ]
        raw_logs = [dict(zip(log_cols, row)) for row in cur.fetchall()]
        call_logs = []
        for log in raw_logs:
            parsed_details = None
            if log["details"]:
                try:
                    parsed_details = json.loads(log["details"])
                except Exception:
                    parsed_details = log["details"]
            call_logs.append(
                {
                    **log,
                    "category": log["category"] or "general_enquiry",
                    "outcome": log["outcome"] or "in_progress",
                    "summary": (
                        parsed_details.get("requirement")
                        or parsed_details.get("property")
                        or "Voice interaction completed"
                    )
                    if isinstance(parsed_details, dict)
                    else "Voice interaction completed",
                    "details": parsed_details,
                }
            )

        # 4. Callbacks
        cur.execute(
            """SELECT callback_id, call_id, name, phone, reason, priority, status, created_at
               FROM callback_requests
               ORDER BY created_at DESC"""
        )
        cb_cols = [
            "callback_id",
            "call_id",
            "name",
            "phone",
            "reason",
            "priority",
            "status",
            "created_at",
        ]
        callbacks = [dict(zip(cb_cols, row)) for row in cur.fetchall()]

        # 5. Caller Memory Profiles
        cur.execute(
            """SELECT user_id, name, email, phone, language_preference, facts, consent_at, last_interaction
               FROM callers
               ORDER BY last_interaction DESC"""
        )
        caller_cols = [
            "user_id",
            "name",
            "email",
            "phone",
            "language_preference",
            "facts",
            "consent_at",
            "last_interaction",
        ]
        raw_callers = [dict(zip(caller_cols, row)) for row in cur.fetchall()]
        callers = []
        for c in raw_callers:
            parsed_facts = []
            if c["facts"]:
                try:
                    parsed_facts = json.loads(c["facts"])
                    if isinstance(parsed_facts, dict):
                        parsed_facts = [
                            f"{k}: {v}" for k, v in parsed_facts.items() if v
                        ]
                except Exception:
                    parsed_facts = [str(c["facts"])]
            callers.append({**c, "facts": parsed_facts})

        # 6. Properties
        cur.execute(
            """SELECT property_id, address, postcode_area, department, status, bedrooms, price_gbp, source_url
               FROM properties
               ORDER BY department ASC, price_gbp ASC"""
        )
        prop_cols = [
            "property_id",
            "address",
            "postcode_area",
            "department",
            "status",
            "bedrooms",
            "price_gbp",
            "source_url",
        ]
        properties = [dict(zip(prop_cols, row)) for row in cur.fetchall()]

        conn.close()

        return {
            "summary": {
                "totalCalls": total_calls,
                "totalBookings": total_bookings,
                "pendingCallbacks": pending_callbacks,
                "emergencies": emergencies,
                "maintenanceRequests": maintenance_requests,
                "salesLeads": sales_leads,
                "callerProfiles": caller_profiles,
                "propertiesCount": properties_count,
            },
            "bookings": bookings,
            "callLogs": call_logs,
            "callbacks": callbacks,
            "callers": callers,
            "properties": properties,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error("Error reading dashboard data: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to query SQLite: {exc}",
        ) from exc


@app.post("/api/dashboard/seed")
def seed_demo_data():
    """Seed sample bookings, emergencies, and calls for demonstration."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        now = datetime.now(timezone.utc)
        iso_now = now.isoformat()

        # Seed sample booking
        booking_ref = f"MCP-VIEW-{random.randint(100000, 999999)}"
        slot_time = f"{now.strftime('%Y-%m-%d')} 14:00"

        cur.execute(
            """INSERT OR IGNORE INTO bookings
               (booking_ref, property_id, slot_start, slot_end, caller_name, caller_email, caller_phone, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed', ?)""",
            (
                booking_ref,
                "prop-canary-01",
                slot_time,
                slot_time,
                "Sarah Jenkins",
                "sarah.jenkins@example.com",
                "07700 900123",
                iso_now,
            ),
        )

        # Seed sample emergency
        cur.execute(
            """INSERT OR IGNORE INTO call_logs
               (call_id, started_at, ended_at, category, outcome, details, transcript)
               VALUES (?, ?, ?, 'emergency', 'safety_disclosed_transferred', ?, ?)""",
            (
                f"call-emergency-{random.randint(1000, 9999)}",
                iso_now,
                iso_now,
                json.dumps(
                    {
                        "full_name": "Marcus Vance",
                        "contact_number": "07700 900789",
                        "current_address": "Flat 4B, Shoreditch High Street",
                        "requirement": "Gas smell in hallway. Emergency gas line 0800 111 999 given.",
                    }
                ),
                "agent: Please act straight away. If you smell gas call 0800 111 999.",
            ),
        )

        # Seed callback
        cur.execute(
            """INSERT OR IGNORE INTO callback_requests
               (callback_id, call_id, name, phone, reason, priority, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'high', 'pending', ?)""",
            (
                f"cb-{random.randint(1000, 9999)}",
                "call-emergency",
                "Marcus Vance",
                "07700 900789",
                "Gas emergency safety follow-up",
                iso_now,
            ),
        )

        # Seed caller memory
        cur.execute(
            """INSERT OR IGNORE INTO callers
               (user_id, name, email, phone, language_preference, facts, consent_at, last_interaction)
               VALUES (?, ?, ?, ?, 'en-GB', ?, ?, ?)""",
            (
                f"usr-{random.randint(1000, 9999)}",
                "Sarah Jenkins",
                "sarah.jenkins@example.com",
                "07700 900123",
                json.dumps(
                    [
                        "Preferred 2-bed in Canary Wharf",
                        "Available for viewings on Thursday afternoons",
                    ]
                ),
                iso_now,
                iso_now,
            ),
        )

        conn.commit()
        conn.close()
        return {"success": True, "message": "Demo data populated successfully"}
    except Exception as exc:
        logger.error("Failed to seed demo data: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Seed failed: {exc}",
        ) from exc


@app.get("/api/properties")
def get_properties():
    """Retrieve all available property catalog listings."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """SELECT property_id, address, postcode_area, department, status, bedrooms, price_gbp, source_url
               FROM properties
               ORDER BY department ASC, price_gbp ASC"""
        )
        cols = [
            "property_id",
            "address",
            "postcode_area",
            "department",
            "status",
            "bedrooms",
            "price_gbp",
            "source_url",
        ]
        items = [dict(zip(cols, row)) for row in cur.fetchall()]
        conn.close()
        return items
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch properties: {exc}",
        ) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
