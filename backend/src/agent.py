import asyncio
import json
import logging
import os
import re

from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    metrics,
    room_io,
    tokenize,
)
from livekit.agents.llm import FallbackAdapter
from livekit.plugins import (
    deepgram,
    groq,
    murf,
    noise_cancellation,
    openai,
    silero,
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel

import db
import facilities
import rag

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Track: Health Access (#VoiceForBharat) — Day 5 Real Tools & Facility Locator
SYSTEM_PROMPT = """You are Careva, a female voice assistant for Health Centres in India. Speak in natural conversational Hindi/Hinglish (Devanagari script) or English based on the caller's language.

ROLE & OBJECTIVES:
1. Triage symptoms and guide callers when to visit the PHC or call 108 in emergencies.
2. Nearest Health Centre & Hospital Lookup: When a caller asks where to go, where the nearest PHC/hospital/CHC is, or asks about OPD timings, call `find_nearest_health_facility`. If their district/pincode is already known from memory, use it automatically.
3. Health Schemes & Vaccines: Answer questions on PHC care, vaccines, and schemes (PM-JAY, JSSK) using `search_health_guidelines`.
4. Generic Medicines & Jan Aushadhi Savings (MCP Tool): When a caller asks about cheap generic medicines, discounts, or drug prices (e.g. Dolo, BP, Sugar/Diabetes, Acidity, Antibiotics), call `lookup_generic_medicine`.
5. Memory & Consent: When a new caller shares their name/district, ask for permission to remember them for future calls. Call `save_caller_info` ONLY AFTER the caller explicitly agrees (says yes/haan). Never call `save_caller_info` while still asking for consent.
6. If a caller asks to forget them or delete their data, call `forget_caller` and confirm deletion.
7. Outbound calls: if you called them and they say stop, "abhi busy hoon", or ask not to be called again, call `end_call` immediately — do not argue or re-pitch.

GUARDRAILS & STYLE:
- Emergencies (chest pain, breathing trouble, severe bleeding, unconsciousness): Tell them to call 108 immediately.
- Never prescribe specific medicines, doses, or give final medical diagnoses.
- Keep responses short: 1-2 simple sentences per turn. Stop and listen.
- Never output raw function tags or JSON code in your dialogue."""

GREETING = (
    "Namaste, this is Careva from the health centre. "
    "Are you calling about yourself or for someone else?"
)


def _outbound_greeting(name: str, reason: str) -> str:
    """Who is calling, why, and how to stop it — in the first two sentences."""
    who = (
        f"Namaste{' ' + name + ' ji' if name else ''}, this is Careva, the automated "
        "health assistant from your local health centre."
    )
    why = (
        f" I'm calling about {reason}."
        if reason
        else " I'm calling for a quick health follow-up."
    )
    return (
        who
        + why
        + " If this is not a good time, just say stop and I will end the call and not call you again."
    )


# Murf Falcon voice: Anisha (Conversational female voice for Indian English / Hindi)
MURF_VOICE = "Anisha"

# Silent user handling
SILENCE_TIMEOUT = 12.0
SILENCE_RE_PROMPTS = [
    "Are you still there? Take your time — I'm listening.",
    "I'll close the call now. Please call us back whenever you're ready. Take care.",
]

FUNCTION_TAG_REGEX = re.compile(
    r"<function=[^>]*>.*?</function>", re.DOTALL | re.IGNORECASE
)
RAW_TAG_REGEX = re.compile(r"</?function[^>]*>", re.IGNORECASE)


class CleanSentenceTokenizer(tokenize.basic.SentenceTokenizer):
    """Safety tokenizer that cleans any leaked function/XML tags before audio synthesis."""

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        cleaned = FUNCTION_TAG_REGEX.sub("", text)
        cleaned = RAW_TAG_REGEX.sub("", cleaned).strip()
        if not cleaned:
            return []
        return super().tokenize(cleaned, language=language)


def _clean_user_id(val: str) -> str:
    s = (val or "").strip()
    if (
        not s
        or s.lower() in ("default", "none", "null", "undefined")
        or "caller" in s.lower()
    ):
        return ""
    return s


class Assistant(Agent):
    def __init__(self, caller_user_id: str = "") -> None:
        super().__init__(instructions=SYSTEM_PROMPT)
        self.caller_user_id = caller_user_id
        self.caller_facts: dict = {}
        self.job_ctx: JobContext | None = None

    @function_tool
    async def lookup_caller(
        self,
        context: RunContext,
        user_id: str = "",
        name: str = "",
    ) -> str:
        """Look up a caller's previous records, name, language preference, and health access facts.

        Use this tool when a caller mentions their name, phone number, or asks if you remember them.

        Args:
            user_id: The caller's phone number or identifier. If not provided, defaults to current session caller ID.
            name: The caller's name to search for if user_id is not known.
        """
        clean_id = _clean_user_id(user_id)
        target_id = clean_id or self.caller_user_id
        caller = None
        if target_id:
            caller = await db.get_caller(target_id)
        if not caller and name and name.lower() not in ("null", "none"):
            caller = await db.get_caller_by_name(name.strip())

        if caller:
            self.caller_facts = caller.get("facts") or {}
            logger.info(
                "lookup_caller found record for %s (%s)",
                caller.get("name"),
                caller.get("user_id"),
            )
            return db.format_caller_for_agent(caller)

        logger.info(
            "lookup_caller: No previous record found for id='%s', name='%s'",
            target_id,
            name,
        )
        return "No previous record found for this caller. Treat them as a new caller."

    @function_tool
    async def save_caller_info(
        self,
        context: RunContext,
        name: str,
        consent_given: bool,
        user_id: str = "",
        language_preference: str = "hi",
        district: str = "",
        age_band: str = "",
        ongoing_conditions: str = "",
        last_triage_outcome: str = "",
    ) -> str:
        """Save or update caller information in the helpline database.

        CRITICAL HEALTH ACCESS RULE:
        You MUST ask for explicit permission before calling this tool.
        If the caller refuses or says no, consent_given MUST be False.

        Args:
            name: Caller's name.
            consent_given: MUST be True only if the caller explicitly agreed to have their info saved.
            user_id: Phone number or caller ID. If empty, defaults to current caller.
            language_preference: Preferred language ("hi", "en", or "hinglish").
            district: Caller's home district or town (e.g., "Varanasi", "Pune", "Patna").
            age_band: Age range (e.g., "30-40", "elderly", "child 5-10"). Do NOT store sensitive medical history.
            ongoing_conditions: Known general conditions mentioned (e.g., "hypertension, diabetes").
            last_triage_outcome: Short summary of triage advice given (e.g., "Advised routine PHC visit for fever").
        """
        if not consent_given:
            logger.info("save_caller_info: Consent was denied. No data saved.")
            return (
                "Consent was NOT given. No caller information was saved. "
                "Reassure the caller that their privacy is respected."
            )

        clean_id = _clean_user_id(user_id)
        target_id = (
            clean_id
            or self.caller_user_id
            or f"caller_{name.lower().strip().replace(' ', '_')}"
        )
        facts = {}
        if district and district.lower() not in ("null", "none"):
            facts["district"] = district.strip()
        if age_band and age_band.lower() not in ("null", "none"):
            facts["age_band"] = age_band.strip()
        if ongoing_conditions and ongoing_conditions.lower() not in ("null", "none"):
            facts["ongoing_conditions"] = [
                c.strip()
                for c in ongoing_conditions.split(",")
                if c.strip() and c.strip().lower() not in ("null", "none")
            ]
        if last_triage_outcome and last_triage_outcome.lower() not in ("null", "none"):
            facts["last_triage_outcome"] = last_triage_outcome.strip()

        self.caller_facts.update(facts)

        saved = await db.save_caller(
            user_id=target_id,
            name=name,
            language_preference=language_preference,
            facts=facts,
            consent_given=True,
        )
        if saved:
            logger.info(
                "save_caller_info: Successfully saved data for %s (%s)",
                name,
                target_id,
            )
            return f"Successfully saved details for {name} with explicit consent."
        return "Failed to save caller information due to an internal error."

    @function_tool
    async def forget_caller(
        self,
        context: RunContext,
        user_id: str = "",
        name: str = "",
    ) -> str:
        """Delete all stored records and memory for the caller if they request to be forgotten.

        Args:
            user_id: Phone number or caller ID to erase. Defaults to current caller ID.
            name: Caller's name to erase if user_id is not known.
        """
        clean_id = _clean_user_id(user_id)
        target_id = clean_id or self.caller_user_id
        target_name = (
            name.strip() if name and name.lower() not in ("null", "none") else ""
        )
        deleted = await db.delete_caller(user_id=target_id, name=target_name)
        if not deleted and target_name:
            deleted = await db.delete_caller(name=target_name)
        if not deleted and self.caller_user_id:
            deleted = await db.delete_caller(user_id=self.caller_user_id)

        if deleted:
            self.caller_user_id = ""
            self.caller_facts.clear()
            logger.info(
                "forget_caller: Deleted record for user_id=%s, name=%s",
                target_id,
                target_name,
            )
            return "All personal records for this caller have been permanently deleted from the database."
        return "No record was found to delete."

    @function_tool
    async def end_call(self, context: RunContext, reason: str = "") -> str:
        """End the call politely. Call this when the caller says stop, asks not to be
        called again, says it's a bad time, or the conversation is finished.

        Args:
            reason: Short note on why the call is ending (e.g. "caller asked to stop").
        """
        logger.info("end_call: %s", reason or "conversation complete")
        await context.session.say(
            "Theek hai, main call rakh rahi hoon. Aapka din shubh ho."
        ).wait_for_playout()
        context.session.shutdown()
        return "Call ended."

    @function_tool
    async def find_nearest_health_facility(
        self,
        context: RunContext,
        location_or_pincode: str = "",
        facility_type: str = "any",
    ) -> str:
        """Find the nearest Primary Health Centre (PHC), Community Health Centre (CHC), or Hospital.

        Use this tool when a caller asks:
        - "Mera paas ka PHC/hospital kahan hai?" or "Where is the nearest health centre?"
        - "OPD kitne baje tak khula hai?" or "What are the hospital timings?"
        - "Emergency mein kahan jayein?" or "Which facility is open 24x7?"
        - "Where can I get free blood tests, fever tests, or delivery care nearby?"

        Args:
            location_or_pincode: District name, city name, area, or 6-digit Indian PIN code. If empty, automatically checks caller memory.
            facility_type: Filter by "PHC", "CHC", "District Hospital", or "any".
        """
        loc = (location_or_pincode or "").strip()
        if not loc:
            # Tool chaining: auto-resolve from Day 4 memory
            loc = (
                self.caller_facts.get("district")
                or self.caller_facts.get("location")
                or ""
            )

        logger.info(
            "find_nearest_health_facility live query loc='%s', type='%s'",
            loc,
            facility_type,
        )
        res = await facilities.find_health_facilities_async(
            loc, facility_type=facility_type
        )

        # Advanced Day 5 Extra: Push results to UI via LiveKit Data Channel
        if (
            self.job_ctx
            and self.job_ctx.room
            and self.job_ctx.room.local_participant
            and res.get("primary_facility")
        ):
            try:
                payload = json.dumps(
                    {
                        "type": "facility_card",
                        "facility": res["primary_facility"],
                        "all_facilities": res.get("facilities", []),
                        "timestamp": res.get("verified_timestamp", "Live Public API"),
                    }
                ).encode("utf-8")
                await self.job_ctx.room.local_participant.publish_data(
                    payload,
                    topic="facility_card",
                )
            except Exception as e:
                logger.warning("Failed to publish facility_card to room: %s", e)

        return res["spoken_summary"]

    @function_tool
    async def search_health_guidelines(
        self,
        context: RunContext,
        query: str,
    ) -> str:
        """Search official government health scheme documents, benefits, eligibility, vaccination schedules, and PHC services.

        Use this tool when a caller asks about:
        - Ayushman Bharat / PM-JAY coverage (up to 5 Lakhs), eligibility, 14555 helpline.
        - Janani Shishu Suraksha Karyakram (JSSK) free delivery, newborn care, free hospital transport.
        - Universal Immunization Programme (UIP) / vaccine schedule for infants and children (birth, 6/10/14 weeks, 9 months).
        - Primary Health Centre (PHC) standard services, OPD hours, free diagnostics and medicines.

        Args:
            query: The specific health scheme, vaccination, or PHC service question to look up.
        """
        logger.info("search_health_guidelines query='%s'", query)
        results = rag.search_health_rag(query, top_k=2)
        return results

    @function_tool
    async def lookup_generic_medicine(
        self,
        context: RunContext,
        medicine_or_condition: str,
    ) -> str:
        """Lookup PMBJP Jan Aushadhi generic medicine rates, active salts, and up to 80% cost savings compared to branded market medicines.

        Use this tool when a caller asks:
        - "Dolo 650 ya Paracetamol ka sasta generic rate kya hai?"
        - "Sugar / Diabetes / BP ki dawaiyan sasti kahan milengi?"
        - "Jan Aushadhi kendra par kitna discount milta hai?"
        - "What is the generic alternative for Pan 40 / Augmentin / Telma?"

        Args:
            medicine_or_condition: Medicine brand name, generic salt name, or medical condition (e.g. "Dolo", "Sugar", "BP", "Telmisartan", "Acidity").
        """
        import health_mcp_server

        logger.info(
            "lookup_generic_medicine query: '%s'",
            medicine_or_condition,
        )
        return await health_mcp_server.lookup_generic_medicine(medicine_or_condition)

    @function_tool
    async def get_district_health_advisory(
        self,
        context: RunContext,
        district_or_city: str = "",
    ) -> str:
        """Query real-time Open-Meteo Air Quality & Respiratory Health API for any Indian district.

        Use this tool when a caller asks:
        - "Varanasi / Pune / Lucknow mein aaj hawa (AQI) aur mausam kaisa hai?"
        - "Asthma ya sans ke mareezon ke liye aaj koi health precaution ya alert hai?"

        Args:
            district_or_city: District or city name in India. If empty, uses caller memory district.
        """
        import health_mcp_server

        loc = (district_or_city or "").strip()
        if not loc:
            loc = self.caller_facts.get("district") or "India"

        logger.info("get_district_health_advisory query: '%s'", loc)
        return await health_mcp_server.get_district_health_advisory(loc)


server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="my-agent")
async def my_agent(ctx: JobContext):
    # Logging setup
    # Add any other context you want in all log entries here
    ctx.log_context_fields = {
        "room": ctx.room.name,
    }

    # Set up a voice AI pipeline using Murf Falcon, Gemini, Deepgram, and the LiveKit turn detector
    session = AgentSession(
        # Speech-to-text (STT) is your agent's ears, turning the user's speech into text that the LLM can understand
        # See all available models at https://docs.livekit.io/agents/models/stt/
        # language="multi" handles Hindi/English code-switching in one sentence,
        # which is how callers actually talk. Without it Deepgram defaults to
        # en-US and mangles Indian-accented speech ("WhiteHat", "harder writing").
        # If callers turn out to be English-only, "en-IN" is more accurate.
        stt=deepgram.STT(model="nova-3", language="multi"),
        # A Large Language Model (LLM) is your agent's brain, processing user input and generating a response
        # See all available models at https://docs.livekit.io/agents/models/llm/
        # Groq llama-3.3-70b primary (fastest TTFT). Fallback is llama-3.1-70b
        # on NVIDIA NIM — same family, so voice and Hinglish quality don't
        # change when Groq rate-limits.
        # ponytail: OpenAI-compatible endpoint, so the openai plugin covers it;
        # no separate nvidia plugin needed.
        # NIM's meta/llama-3.3-70b-instruct is listed but never responds
        # (measured: read timeout at 45s+), which is why the fallback used to be
        # dead weight. 3.1-70b measures 0.8s TTFT with working tool calls.
        llm=FallbackAdapter(
            [
                groq.LLM(model="llama-3.3-70b-versatile"),
                openai.LLM(
                    model="meta/llama-3.1-70b-instruct",
                    base_url="https://integrate.api.nvidia.com/v1",
                    api_key=os.getenv("NVIDIA_API_KEY"),
                    temperature=0.2,
                ),
            ],
            attempt_timeout=15.0,
        ),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=murf.TTS(
            voice=MURF_VOICE,
            style="Conversational",
            tokenizer=CleanSentenceTokenizer(min_sentence_len=4),
            text_pacing=True,
        ),
        # VAD and turn detection are used to determine when the user is speaking and when the agent should respond
        # See more at https://docs.livekit.io/agents/build/turns
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation=True,
        # Fires "user_state_changed" -> "away" once user AND agent are both
        # silent for this long. Drives the silent-caller handling below.
        user_away_timeout=SILENCE_TIMEOUT,
    )

    # ── Silent caller handling ───────────────────────────────────────────────
    # Follow LiveKit's documented cancellable-task pattern: start one task on
    # "away", re-prompt once, wait for a response, then close. Voice activity
    # cancels via user_state_changed. Typed chat uses the RoomIO text callback
    # below because text input does not change the audio user state.
    inactivity_task: asyncio.Task[None] | None = None

    def _cancel_inactivity() -> None:
        nonlocal inactivity_task

        if inactivity_task is not None and not inactivity_task.done():
            inactivity_task.cancel()
        inactivity_task = None

    async def _handle_inactivity() -> None:
        logger.info("SILENCE re-prompt after %.0fs idle", SILENCE_TIMEOUT)
        try:
            await session.say(
                SILENCE_RE_PROMPTS[0], allow_interruptions=True
            ).wait_for_playout()
            await asyncio.sleep(SILENCE_TIMEOUT)

            logger.info("SILENCE no response after re-prompt, closing call")
            await session.say(
                SILENCE_RE_PROMPTS[1], allow_interruptions=True
            ).wait_for_playout()
            session.shutdown()
        except asyncio.CancelledError:
            logger.info("SILENCE caller returned, cancelling close")
            raise

    @session.on("user_state_changed")
    def _on_user_state_changed(ev) -> None:
        nonlocal inactivity_task

        if ev.new_state == "away":
            if inactivity_task is None or inactivity_task.done():
                inactivity_task = asyncio.create_task(_handle_inactivity())
            return

        _cancel_inactivity()

    async def _on_text_input(sess, ev) -> None:
        # LiveKit's default callback is interrupt() + generate_reply(). Add only
        # the missing inactivity cancellation before preserving that behavior.
        _cancel_inactivity()
        await sess.interrupt()
        sess.generate_reply(user_input=ev.text)

    # ── Latency logging (Day 1 optional extra) ───────────────────────────────
    # Components arrive as separate metrics events, so accumulate then log.
    # eou is None whenever LiveKit judges VAD unreliable, so it prints "n/a"
    # rather than 0 — a 0 would read as "no delay" when it means "unknown".
    turn: dict = {}

    @session.on("metrics_collected")
    def _on_metrics(ev) -> None:
        m = ev.metrics
        if isinstance(m, metrics.EOUMetrics):
            turn["eou"] = m.end_of_utterance_delay or None
        elif isinstance(m, metrics.LLMMetrics):
            turn["llm"] = m.ttft
        elif isinstance(m, metrics.TTSMetrics):
            eou = turn.get("eou")
            llm = turn.get("llm", 0.0)
            logger.info(
                "LATENCY reply=%.0fms (llm_ttft=%.0f + tts_ttfb=%.0f), turn_detect=%s",
                (llm + m.ttfb) * 1000,
                llm * 1000,
                m.ttfb * 1000,
                f"{eou * 1000:.0f}ms" if eou else "n/a",
            )
            turn.clear()

    assistant = Assistant()  # caller_user_id set after ctx.connect()
    assistant.job_ctx = ctx

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=assistant,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            # A helpline "close call" should end the room for the caller too,
            # not merely disconnect the agent participant.
            delete_room_on_close=True,
            text_input=room_io.TextInputOptions(text_input_cb=_on_text_input),
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

    # Join the room and connect to the user, initializing DB concurrently
    db_init_task = asyncio.create_task(db.init_db())
    await ctx.connect()
    await db_init_task

    # ── Day 6: outbound call ────────────────────────────────────────────────
    # Dispatch metadata (see src/outbound.py) means "call this number", so dial
    # out before greeting. wait_until_answered lets LiveKit surface the real SIP
    # outcome — no answer / busy / declined — as a TwirpError instead of us
    # timing anything ourselves.
    dial_info = {}
    if ctx.job.metadata:
        try:
            dial_info = json.loads(ctx.job.metadata)
        except json.JSONDecodeError:
            logger.warning("ignoring non-JSON job metadata: %r", ctx.job.metadata)

    phone = dial_info.get("phone", "")
    if phone:
        logger.info("OUTBOUND dialing %s (reason=%r)", phone, dial_info.get("reason"))
        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=os.environ["SIP_OUTBOUND_TRUNK_ID"],
                    sip_call_to=phone,
                    participant_identity=phone,
                    participant_name=dial_info.get("name") or "Caller",
                    wait_until_answered=True,
                    krisp_enabled=True,
                )
            )
            logger.info("OUTBOUND answered by %s", phone)
        except api.TwirpError as e:
            # ponytail: no retry loop — re-run src/outbound.py to redial.
            logger.warning(
                "OUTBOUND not connected to %s: %s (sip_status=%s)",
                phone,
                e.message,
                e.metadata.get("sip_status"),
            )
            ctx.shutdown(reason="outbound call not answered")
            return

    # Determine caller ID from connected room participant (must be after ctx.connect())
    caller_id = ""
    for p in ctx.room.remote_participants.values():
        if p.identity:
            caller_id = p.identity
            break

    if not caller_id:
        try:
            remote_p = await ctx.wait_for_participant(timeout=2.0)
            if remote_p and remote_p.identity:
                caller_id = remote_p.identity
        except Exception:
            pass

    assistant.caller_user_id = caller_id
    caller_record = await db.get_caller(caller_id) if caller_id else None
    if caller_record:
        assistant.caller_facts = caller_record.get("facts") or {}

    # Step 4: Greet returning callers by name and context, or default greeting for new callers
    if phone:
        greeting = _outbound_greeting(
            dial_info.get("name") or (caller_record or {}).get("name", ""),
            dial_info.get("reason", ""),
        )
    elif caller_record:
        name = caller_record.get("name", "")
        lang = caller_record.get("language_preference", "hi")
        facts = caller_record.get("facts", {})
        last_outcome = facts.get("last_triage_outcome", "") or (
            ", ".join(facts.get("ongoing_conditions", []))
            if facts.get("ongoing_conditions")
            else ""
        )

        if lang == "en":
            if last_outcome:
                greeting = f"Namaste {name}, welcome back to the health centre. Last time we spoke about {last_outcome}. How are you feeling today?"
            else:
                greeting = f"Namaste {name}, welcome back to the health centre. How can I assist you today?"
        else:
            if last_outcome:
                greeting = f"नमस्ते {name} जी, स्वास्थ्य केंद्र में आपका फिर से स्वागत है। पिछली बार हमने {last_outcome} के बारे में बात की थी। आज आप कैसा महसूस कर रहे हैं?"
            else:
                greeting = f"नमस्ते {name} जी, स्वास्थ्य केंद्र में आपका फिर से स्वागत है। आज मैं आपकी क्या सहायता कर सकती हूँ?"
    else:
        greeting = GREETING

    # Greet only after the caller is actually subscribed, so the opening line
    # isn't played to an empty room.
    await session.say(greeting, allow_interruptions=True)


if __name__ == "__main__":
    cli.run_app(server)
