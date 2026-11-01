"""Configuration settings and business rules for MyCitiPad virtual receptionist.

Tunable business parameters marked with (ASSUMPTION) should be verified with MyCitiPad.
"""

import os

TIMEZONE = "Europe/London"

# Permitted viewing days: Mon=0 ... Sat=5  (ASSUMPTION: confirm with MyCitiPad)
VIEWING_DAYS = {0, 1, 2, 3, 4, 5}

# Viewing operating hours in UK local time: 10:00 to 17:00 (ASSUMPTION: confirm with MyCitiPad)
VIEWING_START_HOUR = 10  # first slot at 10:00 (ASSUMPTION)
VIEWING_END_HOUR = 17  # last slot starts at 16:00, concludes by 17:00 (ASSUMPTION)

# Duration and interval of viewings
VIEWING_LENGTH_MIN = 30  # 30-minute viewing length (ASSUMPTION)
SLOT_STEP_MIN = 60  # a new slot every hour on the hour (ASSUMPTION)

# Scheduling window constraints
MIN_NOTICE_HOURS = 2  # minimum notice ahead of current time (ASSUMPTION)
MAX_DAYS_AHEAD = 14  # book up to 14 days in advance (ASSUMPTION)
MAX_SLOTS_OFFERED = 4  # offer at most 4 slots per inquiry

# Execution resilience
TOOL_TIMEOUT_S = 4.0

# Human availability simulation for browser demo (default False)
HUMAN_AVAILABLE = os.getenv("MCP_HUMAN_AVAILABLE", "false").lower() in (
    "true",
    "1",
    "yes",
)

# Simulation flags for failure path testing:
# "", "properties", "calendar", "records", "handover", "callback"
SIMULATE_FAILURE = os.getenv("MCP_SIMULATE_FAILURE", "")

# =============================================================================
# Outbound Telephony Settings (Twilio Connector)
# =============================================================================
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER")
AGENT_NAME = os.getenv("AGENT_NAME", "my-agent")

# Allowlist: If set, ONLY these E.164 numbers can be dialled
OUTBOUND_ALLOWLIST = [
    p.strip() for p in os.getenv("MCP_OUTBOUND_ALLOWLIST", "").split(",") if p.strip()
]

# Allow overriding polite calling hours for testing
OUTBOUND_IGNORE_HOURS = os.getenv("MCP_OUTBOUND_IGNORE_HOURS", "false").lower() in (
    "true",
    "1",
    "yes",
)

# Calling hours: 09:00 - 20:00 UK local time, Mon-Sat (ASSUMPTION: confirm compliance)
OUTBOUND_HOURS_UK = (9, 20)

# Outbound operational constraints
OUTBOUND_MAX_ATTEMPTS = int(os.getenv("OUTBOUND_MAX_ATTEMPTS", "1"))
OUTBOUND_RING_TIMEOUT_S = int(os.getenv("OUTBOUND_RING_TIMEOUT_S", "75"))
OUTBOUND_MAX_CALL_SECONDS = int(os.getenv("OUTBOUND_MAX_CALL_SECONDS", "300"))

# Speech tuning
BRAND_SPOKEN = "My Citi Pad"
LEAVE_VOICEMAIL = True  # (ASSUMPTION)
OFFICE_PHONE_SPOKEN = (
    "oh two oh, three five seven six, six five seven five"  # 020 3576 6575
)
