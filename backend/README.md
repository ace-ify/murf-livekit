# Backend — Voice Agent with Murf Falcon TTS

The Python backend for the Voice Agent Starter. It runs a real-time voice AI pipeline using [LiveKit Agents](https://docs.livekit.io/agents), connecting Murf Falcon TTS, Deepgram STT, and Google Gemini into a single conversational agent.

## How It Works

```
User speaks → [Deepgram STT] → text → [Gemini LLM] → response → [Murf Falcon TTS] → audio → User hears
```

LiveKit handles the real-time audio transport. The agent connects to LiveKit as a participant, listens for user speech, and responds with synthesized audio.

## Setup

### 1. Install dependencies

```bash
cd backend
uv sync
```

### 2. Configure environment

```bash
cp .env.example .env.local
```

Fill in your keys in `.env.local`:

| Variable | Where to get it |
|----------|-----------------|
| `LIVEKIT_URL` | [LiveKit Cloud](https://cloud.livekit.io/) → Settings |
| `LIVEKIT_API_KEY` | [LiveKit Cloud](https://cloud.livekit.io/) → Settings |
| `LIVEKIT_API_SECRET` | [LiveKit Cloud](https://cloud.livekit.io/) → Settings |
| `MURF_API_KEY` | [murf.ai/api/dashboard](https://murf.ai/api/dashboard) |
| `DEEPGRAM_API_KEY` | [deepgram.com](https://console.deepgram.com/) |
| `GOOGLE_API_KEY` | [aistudio.google.com](https://aistudio.google.com/apikey) |

For LiveKit Cloud users, you can auto-populate LiveKit credentials:

```bash
lk cloud auth
lk app env -w -d .env.local
```

### 3. Download models

```bash
uv run python src/agent.py download-files
```

This downloads Silero VAD and the LiveKit turn detector models.

### 4. Run the agent

```bash
# Development mode (auto-reload)
uv run python src/agent.py dev

# Or test directly in your terminal (no frontend needed)
uv run python src/agent.py console

# Production
uv run python src/agent.py start
```

## Configuration

All configuration lives in [`src/agent.py`](src/agent.py).

### System prompt

The `SYSTEM_PROMPT` constant at the top of `agent.py` controls what your agent does. Change it to build any voice-powered use case.

#### Example prompts

**Customer Support (default):**

```
You are a friendly and efficient customer support agent for a tech company. Help users with account issues, billing questions, and product troubleshooting. Be concise, empathetic, and solution-oriented. If you don't know something, say so honestly and offer to escalate.
```

**Language Tutor:**

```
You are a patient and encouraging language tutor helping the user practice conversational Spanish. Speak primarily in Spanish but switch to English to explain grammar or vocabulary when needed. Correct mistakes gently and suggest better phrasing. Keep conversations natural and fun.
```

**AI Receptionist:**

```
You are a professional receptionist for a medical clinic. Help callers schedule appointments, answer questions about office hours and services, and take messages for doctors. Be warm but efficient. Ask for the caller's name and reason for calling upfront.
```

**Interview Coach:**

```
You are an experienced interview coach. Conduct mock interviews with the user for software engineering roles. Ask one behavioral or technical question at a time, let the user answer fully, then give specific feedback on their response — what was strong, what could improve, and a suggested reframe. Keep the tone encouraging but honest.
```

**Sales Assistant:**

```
You are a knowledgeable sales assistant for an electronics store. Help customers find the right product by asking about their needs, budget, and preferences. Compare options clearly, highlight trade-offs, and make a recommendation. Never be pushy — focus on helping the customer make the best decision for them.
```

**Fitness Coach:**

```
You are an upbeat personal fitness coach. Help users plan workouts, suggest exercises for specific muscle groups, and answer questions about form and technique. Ask about their fitness level and any injuries before recommending exercises. Keep instructions clear and motivating.
```

**Storyteller / Bedtime Narrator:**

```
You are a creative storyteller who tells original bedtime stories for children aged 4–8. Ask the child (or parent) for a character name, a favorite animal, and a setting, then weave a short, calming story. Use vivid but simple language. End each story on a peaceful, sleepy note.
```

**Meeting Summarizer:**

```
You are a meeting assistant. The user will describe what happened in a meeting or read you their notes. Summarize the key decisions, action items (with owners if mentioned), and any open questions. Be concise and structured. Ask clarifying questions if something is ambiguous.
```

**Trivia Game Host:**

```
You are an enthusiastic trivia game host. Ask the user one trivia question at a time from a mix of categories — science, history, pop culture, geography, and sports. Wait for their answer, tell them if they're right or wrong, give a brief fun fact, then move to the next question. Keep score and announce it every 5 questions.
```

**Mental Health Check-in Companion:**

```
You are a gentle, non-clinical wellness companion. Help users talk through their day, reflect on how they're feeling, and practice simple grounding exercises like deep breathing or gratitude lists. You are not a therapist — if the user expresses serious distress or mentions self-harm, gently encourage them to reach out to a professional or crisis helpline.
```

### Voice

Set the `voice` argument in the `murf.TTS(...)` call:

```python
tts=murf.TTS(
    voice="en-US-matthew",    # Change this
    style="Conversation",
    tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
    text_pacing=True
)
```

Some voice options:

| Voice ID | Description |
|----------|-------------|
| `en-US-matthew` | US English, male (default) |
| `en-US-natalie` | US English, female |
| `en-UK-ruby` | UK English, female |
| `en-US-miles` | US English, male |

Browse all 150+ voices: [Murf Voice Library](https://murf.ai/api/docs/voices-styles/voice-library).

### STT (Speech-to-Text)

Default is Deepgram Nova-3. Change in the `AgentSession(stt=...)` call:

```python
stt=deepgram.STT(model="nova-3")
```

### LLM

Default is Google Gemini. To switch:

- **Gemini (default):** Set `GOOGLE_API_KEY` in `.env.local`
- **OpenAI:** Set `OPENAI_API_KEY`, install `livekit-agents[openai]`, and change the `llm=` argument

## Day 5: Tools & Data Sources (Live vs Local)

Careva (Health Access Voice Agent) integrates real-time public APIs with robust local fallback caches:

| Tool / Service | Source Type | Endpoint / Source | Description |
| :--- | :--- | :--- | :--- |
| **Health Facility Locator** (`find_nearest_health_facility`) | **Live Public API** | OpenStreetMap Nominatim + India Post (`api.postalpincode.in`) | Real-time geocoding & POI search for hospitals, PHCs, CHCs, and coordinates across India. |
| **Emergency & OPD Fallback** (`facilities.py`) | **Local Cache** | Verified Indian Public Health Registry (`FALLBACK_FACILITIES`) | Curated OPD timings, 24x7 emergency status, and free tests used if external APIs time out. |
| **Generic Drug Identifier (MCP)** (`lookup_generic_medicine`) | **Live Public API** | NLM RxNorm REST API (`rxnav.nlm.nih.gov`) | Dynamic active chemical generic salts and PMBJP Jan Aushadhi 60-80% discount savings guidance. |
| **Environmental Health Advisory (MCP)** (`get_district_health_advisory`) | **Live Public API** | Open-Meteo Air Quality API (`air-quality-api.open-meteo.com`) | Real-time PM2.5, PM10, and US AQI index with clinical precautions for asthma/elderly callers. |
| **Health Schemes RAG** (`search_health_guidelines`) | **Local RAG** | `data/knowledge/*.md` | Ayushman Bharat (PM-JAY), JSSK maternal care, and Universal Immunization guidelines. |

## Day 6: Outbound Calls

Careva can call the patient instead of waiting to be called — medication/vaccination
reminders and post-triage follow-ups.

One-time telephony setup (Twilio Elastic SIP Trunking → LiveKit):

1. Twilio Console → Elastic SIP Trunking → create a trunk, add a credential list, buy a number.
2. Create the LiveKit outbound trunk (numbers/address from Twilio):

   ```bash
   lk sip outbound create --name careva-outbound \
     --address <your-trunk>.pstn.twilio.com --number +1XXXXXXXXXX \
     --auth-user "$SIP_AUTH_USERNAME" --auth-pass "$SIP_AUTH_PASSWORD"
   ```

3. Put the printed `SIPTrunkID` in `.env.local` as `SIP_OUTBOUND_TRUNK_ID`.

Place a call (agent must be running — `uv run src/agent.py dev`):

```bash
uv run src/outbound.py +919876543210 --name "Ramesh" --reason "your BP medicine reminder"
```

`src/outbound.py` only creates an explicit agent dispatch with the phone number in job
metadata; the agent dials via `ctx.api.sip.create_sip_participant(..., wait_until_answered=True)`,
so no-answer/busy/declined surface as a `TwirpError` and the job shuts down instead of
talking to a dead line. The opening line (`_outbound_greeting`) states who is calling, why,
and that saying "stop" ends the call — `end_call` handles that immediately.

## Day 7: Knowing When to Ask for Human Help

Careva stops and creates a request for a human in exactly two situations:

1. **Red flag / clinical emergency** — chest pain, breathing trouble, severe or continuing
   bleeding, fainting, fits, pregnancy complication, sick newborn, poisoning, suicidal talk,
   or a symptom getting worse despite advice.
2. **A decision it must not make** — a diagnosis, starting/stopping a medicine, a dose, or
   permission to skip treatment or hospital care.

Everything else (facility lookup, OPD timings, schemes, medicine prices, a mild complaint it
already advised on) stays with the agent. `tests/test_agent.py` asserts both paths.

**Permission first.** The agent asks *"Kya main iska ek chhota summary ek human health worker
ko bhej sakti hoon?"* and only then calls `create_escalation`. A refusal calls the same tool
with `consent_given=False`, which writes nothing at all.

**What the human gets** — six fields, never the transcript: who, what happened, what the
agent already checked, urgency (`low|medium|high|emergency`), the caller's language, and
their preferred follow-up method.

**PII scrub.** `db.scrub_pii` strips phone numbers, Aadhaar, long digit runs, and any short
number sitting next to `otp`/`pin`/`upi`/`account`/`card` before the row is written — one
choke point, so what is stored and what the webhook sends cannot diverge. Clinical numbers
survive on purpose: `108`, `1075`, `14555`, `BP 140/90`, `45-55`, `pin code 221002`. A bare
`pin 221002` is scrubbed — losing a pincode is cheaper than leaking a PIN.

**Reference number.** `ESC-0007`, derived from the row id (`AUTOINCREMENT`, so a number is
never reused). The agent speaks it digit by digit and says a worker will review it during
working hours — it never promises an immediate callback. `check_escalation_status` reads it
back on a later call, scoped to the owning caller.

**Dedupe.** A partial unique index (`caller_user_id, dedupe_key WHERE status='open'`) makes
the same complaint from the same caller update the existing case instead of opening a second
one — decided atomically inside SQLite, so two concurrent sessions cannot both insert.
Resolving a case frees the slot.

**Where requests land.** `data/helpline.db` is the source of truth; the human queue is the
Next.js page at `/admin`. Set `ESCALATION_WEBHOOK_URL` for an extra Discord ping (optional,
best-effort — a dead webhook never fails the tool or loses the row).

Working the queue:

```bash
uv run src/escalations.py list
uv run src/escalations.py list --status resolved
uv run src/escalations.py resolve ESC-0001 --note "ANM visited, referred to CHC"

# Day 6 callback: tell the caller it's resolved (needs SIP + the agent running)
uv run src/escalations.py resolve ESC-0001 --note "done" --call
```

`/admin` writes status straight into SQLite and prints the `--call` command per row. There is
deliberately no HTTP server and no poller on the Python side.

> **Security:** `/admin` and its GET are unauthenticated for local dev and they list health
> complaints. Status *writes* need `ADMIN_TOKEN` and fail closed when it is unset. Put real
> auth in front of that page before deploying it anywhere public.

## Testing

The project includes an eval suite based on the LiveKit Agents [testing framework](https://docs.livekit.io/agents/build/testing/):

```bash
uv run pytest
```

Tests are in [`tests/test_agent.py`](tests/test_agent.py) and use LLM-as-judge evaluations to verify the agent behaves correctly (friendly greetings, grounding, refusing harmful requests).

To run tests in CI, you'll need to add `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` as repository secrets.

## Deployment

### Railway

[![Deploy on Railway](https://railway.com/button.svg)](https://railway.com/deploy/tIVCF1?referralCode=cNjn2P&utm_medium=integration&utm_source=template&utm_campaign=generic)

Set these environment variables in Railway:
- `MURF_API_KEY`
- `DEEPGRAM_API_KEY`
- `GOOGLE_API_KEY`
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`

### Docker

A production-ready [Dockerfile](Dockerfile) is included:

```bash
docker build -t murf-voice-agent .
docker run --env-file .env.local murf-voice-agent
```

## Project Structure

```
backend/
├── src/
│   └── agent.py          # Agent entrypoint — pipeline, prompt, config
│   └── db.py             # SQLite: caller memory + Day 7 escalations, PII scrub
│   └── outbound.py       # Day 6 — dispatch an outbound call
│   └── escalations.py    # Day 7 — list/resolve escalations, callback the caller
├── tests/
│   └── test_agent.py         # LLM-judged eval suite
│   └── test_escalations.py   # Day 7 — refs, dedupe, scrub, status transitions
├── .env.example           # Environment variable template
├── pyproject.toml         # Python dependencies (uv)
├── Dockerfile             # Production container
└── railway.toml           # Railway deploy config
```

## Links

- [Murf Falcon TTS Docs](https://murf.ai/api/docs/text-to-speech/streaming)
- [Murf Voice Library](https://murf.ai/api/docs/voices-styles/voice-library)
- [LiveKit Agents Docs](https://docs.livekit.io/agents)
- [Deepgram Nova-3 Docs](https://developers.deepgram.com)

## License

MIT — see [LICENSE](LICENSE).
