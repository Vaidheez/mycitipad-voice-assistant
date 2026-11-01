# MyCitiPad Voice Receptionist — Audio & Latency Quality Checklist

This checklist is used for manual end-to-end audio evaluation of the virtual receptionist on a live microphone call.

| Item | Criteria | Target | Verification Method | Status | Notes |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **Greeting Latency** | First greeting starts shortly after clicking **Start call** | $\le$ 2.0s | Press Start Call; measure time until greeting audio begins | [ ] Pass | Room connection + prewarmed Silero VAD + Murf Falcon TTFB |
| **Turn Turnaround** | Replies start promptly after caller finishes utterance | $\sim$ 1.0s | Speak caller turn; measure silence until agent speaks | [ ] Pass | Deepgram Nova-3 endpointing + Gemini + Falcon streaming |
| **Currency Reading** | Monetary figures spoken in natural British English | "five hundred thousand pounds" | Say "my budget is five hundred thousand pounds" or "5 lakh" | [ ] Pass | Never "5 lakh", never dollars, never raw numbers |
| **Postcode Phonetics** | Postcodes articulated clearly with separated characters | "S E sixteen" | Enquire about "Fisher Close, SE16" | [ ] Pass | Postcode letters and numbers enunciated distinctly |
| **Phone Number Pacing** | Phone numbers read back digit by digit | "zero, seven, seven, zero, zero..." | Provide mobile callback number | [ ] Pass | Steady cadence with slight pauses between digit groups |
| **Email Readback** | Emails confirmed letter by letter | "j-o-h-n at example dot com" | Provide email for viewing confirmation | [ ] Pass | Spells out letters with explicit "at" and "dot" |
| **Dates & Times** | Viewing times read in natural UK conversational style | "Thursday the sixth of November at eleven in the morning" | Book a viewing slot | [ ] Pass | Generated via `format_spoken_slot` helper in London time |
| **Rental Terms** | Monthly rent expressed without abbreviations | "per calendar month" | Enquire about rental terms | [ ] Pass | Never reads "pcm" as an acronym |
| **Barge-in / Interruption**| Agent immediately cuts audio when caller speaks | $\le$ 300ms | Interrupt agent mid-sentence ("Wait, actually...") | [ ] Pass | LiveKit server-side track interruption + VAD detection |
| **Voice Consistency** | Clear, warm British English accent | UK Hazel (`en-UK-hazel`) | Listen across multiple turns | [ ] Pass | Consistent Murf Falcon model, pitch, and timbre |
| **Conversational Cadence**| Short spoken sentences, exactly one question per turn | $\le$ 2 sentences / turn | Follow lettings qualification path | [ ] Pass | Avoids compound questions and run-on sentences |
| **Emergency Calmness** | Emergency warnings spoken clearly and unhurried | Immediate safety | Say "there is a gas leak in my flat" | [ ] Pass | 999 and gas line numbers recited with clear diction |

---

## Verifiable Code Assertions (Pre-Call Verification)

* **TTS Engine Config:** `murf.TTS(voice="en-UK-hazel", model="falcon")` configured in `agent.py`.
* **Prompt Spoken Directives:** Explicit rules in `backend/src/prompt.py` (`STYLE` block) forcing digit-by-digit phone numbers, letter-by-letter email confirmations with "at" and "dot", spaced postcodes, and "per calendar month" expansions.
* **Deterministic Spoken Slot Formatter:** `calendar_provider.py` enforces UK ordinals ("third", "twenty-fourth") and daylight-saving aware clock times ("at ten in the morning", "at two in the afternoon").
