# MyCitiPad Virtual Receptionist — Video Demo Script (4–5 Minutes)

This script guides an end-to-end screen-recorded walkthrough of the MyCitiPad voice AI receptionist. It proves real-time speech interaction, compliant routing, calendar slot booking, memory consent gating, guardrail enforcement, and resilience under failure.

---

## Pre-Flight Checklist

Before starting the recording:
1. **Environment Variables**: Confirm `.env.local` in `backend/` and `frontend/` have active keys (`LIVEKIT_URL`, `MURF_API_KEY`, `DEEPGRAM_API_KEY`, `GOOGLE_API_KEY`).
2. **Clean Database Seed**: Reset database to ensure a clean state:
   ```bash
   cd backend
   python -c "import os; os.remove('data/mycitipad.db') if os.path.exists('data/mycitipad.db') else None"
   uv run python src/db.py
   ```
3. **Audio Hardware**: Test microphone input levels; verify headphones/speakers prevent echo.
4. **Browser Permissions**: Open Chrome / Firefox in Incognito or reset permissions for `http://localhost:3000`.
5. **Services Running**:
   - Backend terminal: `uv run python src/agent.py dev`
   - Frontend terminal: `pnpm dev`
6. **Simulated Failure Default**: Confirm `MCP_SIMULATE_FAILURE=""` and `MCP_HUMAN_AVAILABLE="false"`.
7. **Prepared Fake Caller Data**:
   - Caller 1: Sarah Jenkins (`sarah.jenkins@example.com`, `07700 900123`)
   - Caller 2: David Smith (`david.smith@example.com`, `07700 900456`)

---

## Scene-by-Scene Walkthrough

### Scene 1: Ready Screen & Call Initialization (10s)
- **What to click**: Open `http://localhost:3000`. Click the emerald **Start Call** button.
- **What to say**: Silence (wait for assistant greeting).
- **What to see & hear**:
  - UI transitions smoothly from **Ready** (Navy/Emerald branded card) → **Connecting** (pulsing badge) → **Listening**.
  - Assistant speaks with Murf Falcon `Hazel` voice:
    > *"Hello, thanks for calling MyCitiPad. I'm the virtual assistant, and this call is recorded so nothing gets lost. I'm here to help you get to the right place quickly. Are you calling about lettings or sales?"*
- **What it proves**: Professional branding, low initial latency ($\le 2$s), clear recording disclosure, and immediate call triage.

---

### Scene 2: Lettings Enquiry & Viewing Booking (90s)
- **What to say (Caller)**:
  1. *"I'm looking to rent a flat."*
  2. *"I saw the apartment on Canary Wharf Riverside."*
  3. Answer the qualification questions one by one:
     - Name: *"Sarah Jenkins"*
     - Email: *"sarah dot jenkins at example dot com"*
     - Move-in date: *"Next month, around the first of December."*
     - Occupants: *"Just myself."*
     - Employment & Income: *"Employed full time, earning 45,000 pounds a year."*
     - Guarantor: *"No, I don't need a guarantor."*
     - Viewing preference: *"Yes, I'd love to view it this week."*
  4. Assistant queries calendar and offers up to 4 real slots (e.g. Wednesday 11am, Thursday 2pm).
  5. Select slot: *"Thursday at 2pm please."*
- **What to see & hear**:
  - LiveKit audio visualizer shows active waveform when assistant speaks.
  - Assistant calls `find_property` → `check_viewing_availability` → `book_viewing`.
  - Assistant confirms booking **only after** `book_viewing` returns confirmation code (e.g. `MCP-VIEW-...`).
  - Terminal/DB check (optional screen switch): `SELECT booking_ref, slot_start, caller_name FROM bookings` shows Sarah's confirmed slot.
- **What it proves**: Strict nine-question sequence, atomic calendar reservation, and refusal to claim a booking prematurely.

---

### Scene 3: Guardrail Enforcement — Price & Time Traps (30s)
- **What to say (Caller)**:
  - Trap 1: *"By the way, how much is the monthly rent for that Canary Wharf flat?"*
  - Trap 2: *"And when exactly will your letting agent call me back to confirm keys?"*
- **What to see & hear**:
  - Assistant refuses politely without guessing:
    > *"I don't have that to hand, but I'll make sure the team covers it with you."*
  - Refuses to give a specific time estimate:
    > *"Someone from the team will be in touch soon, but I can't promise an exact callback time."*
- **What it proves**: Strict compliance; zero price speculation; refusal to promise callback or contractor visit times.

---

### Scene 4: Maintenance Emergency Safety Script (30s)
- **What to say (Caller)**:
  - End previous call, click **Start Call** again.
  - *"I'm calling about lettings... actually it's maintenance. I smell gas in the hallway and the boiler is hissing!"*
- **What to see & hear**:
  - Assistant immediately delivers statutory safety guidance **before** taking details:
    > *"Please act straight away. If anyone is in immediate danger or there's a fire, call 999. If you smell gas, call the emergency gas line on 0800 111 999."*
  - Followed by: *"Once you're safe, can I take a few details so we can help?"*
  - Offers immediate transfer to a human team member.
- **What it proves**: Life safety takes unconditional precedence over data collection or triage.

---

### Scene 5: Memory Consent & Returning Caller Welcome (60s)
- **What to click & say**:
  - Complete the call details (Name: *"Sarah Jenkins"*, Email: *"sarah.jenkins@example.com"*).
  - Assistant asks the memory consent question:
    > *"Is it okay if I remember these details for next time?"*
  - Caller: *"Yes, that's fine."*
  - Assistant confirms: *"Thank you, I've made a note of your details."*
  - Click **End Call** button (UI updates to **Call ended**).
  - Click **Start Call** button to initiate a new session.
  - Caller identifies as Sarah: *"Hi, it's Sarah Jenkins again."*
- **What to see & hear**:
  - Assistant executes `lookup_caller` and responds with returning caller greeting:
    > *"Welcome back, Sarah, and thanks for calling MyCitiPad. This call is recorded. Last time you were booking a property viewing. Would you like to pick up from there, or is it something new?"*
- **What it proves**: Explicit caller consent, persistent SQLite memory, and personalized multi-session continuity.

---

### Scene 6: Consent Rejection — "No Means No" (20s)
- **What to click & say**:
  - Start call as new caller: David Smith (`david.smith@example.com`).
  - Report general sales enquiry.
  - When assistant asks: *"Is it okay if I remember these details for next time?"*
  - Caller: *"No, please don't save anything about me."*
- **What to see & hear**:
  - Assistant acknowledges: *"No problem, I won't keep those."*
  - Call log is saved with `user_id = NULL`.
  - Terminal/DB inspection proves zero rows inserted in `callers` table for David Smith.
- **What it proves**: Strict privacy compliance; explicit opt-in; no unsolicited data retention.

---

### Scene 7: Tool Failure & Graceful Spoken Fallback (30s)
- **What to do**:
  - In backend terminal, start agent with calendar failure:
    ```bash
    $env:MCP_SIMULATE_FAILURE="calendar"; uv run python src/agent.py dev
    ```
  - Start call, request viewing for Canary Wharf.
- **What to see & hear**:
  - Assistant attempts `check_viewing_availability`, catches the simulation error, and speaks the exact recovery message:
    > *"I can't see the diary just now. I can take your details and ask the team to arrange the viewing. Is that all right?"*
  - Continues the conversation gracefully without dropping the call or crashing the voice loop.
- **What it proves**: Fault tolerance; no unhandled Python exceptions; clear spoken fallback rather than dead air.

---

### Scene 8: Microphone Permission Blocked Banner (15s)
- **What to do**:
  - In browser URL bar, click the site settings padlock/tune icon and set **Microphone** to **Block**.
  - Click **Start Call**.
- **What to see & hear**:
  - Red alert banner slides into view above the call card:
    > **"Microphone Access Blocked"** — *"Please enable microphone permissions in your browser address bar to speak with the receptionist."*
  - "Start Call" button is disabled; clear unblock instructions provided.
- **What it proves**: Robust client-side error handling for non-technical users.

---

### Scene 9: Call Ended & Reset (10s)
- **What to do**:
  - Re-enable mic, start call, and press **End Call**.
- **What to see & hear**:
  - UI displays **Call ended** badge.
  - "Start new call" button appears with reset state.
- **What it proves**: Clean lifecycle teardown and easy re-entry for subsequent callers.

---

## Fallback Plan

If unexpected issues occur during live recording:
1. **Network / API Key Rate Limit**:
   - Have a pre-recorded backup video of Scenes 1–9 ready to present.
   - Run the fast conversation test suite on screen to prove all logic deterministically:
     ```bash
     cd backend && uv run pytest -m "not llm" -v
     ```
2. **Local Audio Loopback / Echo**:
   - Use a wired headset with directional microphone.
   - Adjust LiveKit Agents turn detector threshold if barge-in triggers prematurely.
