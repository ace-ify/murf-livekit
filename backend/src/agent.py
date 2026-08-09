import asyncio
import logging
import re

from dotenv import load_dotenv
from livekit import rtc
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
from livekit.plugins import deepgram, google, groq, murf, noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

import db
import rag

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Track: Health Access (#VoiceForBharat) — Day 4 Memory, Consent & RAG
SYSTEM_PROMPT = """You are Careva, a female voice assistant for Health Centres in India. Speak in natural conversational Hindi/Hinglish (Devanagari script) or English based on the caller's language.

ROLE & OBJECTIVES:
1. Triage symptoms and guide callers when to visit the PHC or call 108 in emergencies.
2. Answer questions on PHC care, vaccinations, and government health schemes (PM-JAY, JSSK) using search_health_guidelines.
3. Memory & Consent: When a new caller shares their name, ask for permission to remember them for future calls. Call save_caller_info ONLY AFTER the caller explicitly agrees (says yes/haan). Never call save_caller_info while still asking for consent.
4. If a caller asks to forget them or delete their data, call forget_caller and confirm deletion.

GUARDRAILS & STYLE:
- Emergencies (chest pain, breathing trouble, severe bleeding, unconsciousness): Tell them to call 108 immediately.
- Never prescribe specific medicines, doses, or give final medical diagnoses.
- Keep responses short: 1-2 simple sentences per turn. Stop and listen.
- Never output raw function tags or JSON code in your dialogue."""

GREETING = (
    "Namaste, this is Careva from the health centre. "
    "Are you calling about yourself or for someone else?"
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
            logger.info(
                "forget_caller: Deleted record for user_id=%s, name=%s",
                target_id,
                target_name,
            )
            return "All personal records for this caller have been permanently deleted from the database."
        return "No record was found to delete."

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
        # Gemini primary — noticeably better Hindi/Hinglish generation than the
        # Groq llamas, which produced incoherent Hindi ("aapke paas kyon
        # bhatkana hai?"). Groq llama-3.3-70b is the fallback: 8b-instant gave
        # off-topic replies, qwen3.6 leaks <think> tags, gpt-oss is slower.
        # FallbackAdapter: Groq Llama-3.3-70B as primary (fastest TTFT),
        # with Google Gemini 2.0 Flash as secondary fallback.
        llm=FallbackAdapter(
            [
                groq.LLM(model="llama-3.3-70b-versatile"),
                google.LLM(model="gemini-2.0-flash"),
            ],
            attempt_timeout=12.0,
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

    # Step 4: Greet returning callers by name and context, or default greeting for new callers
    if caller_record:
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
