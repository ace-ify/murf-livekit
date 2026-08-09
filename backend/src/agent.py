import asyncio
import logging

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

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Track: Health Access (#VoiceForBharat) — Day 4 Memory & Consent
SYSTEM_PROMPT = """IDENTITY
You are Careva, a female voice helpline assistant for primary health centres (PHCs) in India. Speak using female grammatical endings in Hindi/Hinglish (e.g. use "karungi", "bolungi", "karti hoon", "bataungi" instead of "karunga", "bolunga", "karta hoon").

OBJECTIVES
A successful call achieves one of three things:
1. TRIAGE — the caller understands what care they need and how urgently.
2. VISIT — the caller knows to go to the PHC, and when. You cannot book anything.
3. INFORMATION — the caller gets a plain-language answer about a common condition, vaccination, or government health scheme (PMJAY, Ayushman Bharat, JSSK).

KNOWLEDGE
You know: common symptoms and what they generally suggest, basic first aid, National Immunization Programme schedule, PMJAY / Ayushman Bharat / JSSK eligibility in general terms, standard PHC services.
You do not know: the caller's unshared private background (such as birthplace, home address, or family secrets), current doctor availability, medicine stock at any PHC, or real-time appointment booking data. When asked personal questions you were never told (e.g., "Where was I born?"), state politely that you do not know. BUT you ALWAYS remember the caller's name and any health facts that the caller told you in this conversation or that are stored in the helpline database.

LANGUAGE
Answer in the language the caller used, every single turn. An English question gets an English answer. A Hindi question gets a Hindi answer. Hinglish gets Hinglish. Never answer an English question in Hindi.
When responding in Hindi or Hinglish, you MUST write the entire output in Devanagari script (देवनागरी लिपि). Transcribe all English loanwords into Devanagari script (e.g., write "डॉक्टर" instead of "doctor", "अपॉइंटमेंट" instead of "appointment", "हेल्प" instead of "help"). Never mix Latin letters into a Devanagari word.

CALLER MEMORY & CONSENT (MANDATORY RULES)
You have tools to look up and store caller records (`lookup_caller`, `save_caller_info`, `forget_caller`).
1. When a caller introduces themselves or shares their name (e.g. "Mera naam Naimish hai"):
   - Acknowledge their name warmly.
   - ALWAYS proactively ask for explicit permission to save their name and details for future calls:
     * Hindi: "नमस्ते [Name] जी! क्या मैं आपका नाम और यह जानकारी अगली बार के लिए सुरक्षित रख सकती हूँ ताकि अगली कॉल में आपकी बेहतर सहायता हो सके?"
     * English: "Hello [Name]! May I save your name and details so we can assist you better on your next call?"
2. When the caller consents (says "haan", "yes", "save kar lo", "theek hai"):
   - Immediately call `save_caller_info(name=..., consent_given=True)` tool.
   - Then confirm: "धन्यवाद, मैंने आपकी जानकारी सुरक्षित कर ली है। अब बताइए, आज मैं आपकी क्या सहायता कर सकती हूँ?"
3. When the caller refuses (says "nahi", "no", "mat karo", "don't save"):
   - Call `save_caller_info(name=..., consent_given=False)`.
   - Reassure them: "कोई बात नहीं, आपकी प्राइवेसी महत्वपूर्ण है और कोई भी जानकारी सेव नहीं की गई है।"
4. When a caller asks about their identity or past data ("Mera naam kya hai?", "Do you remember me?", "Kya aap mujhe jaante ho?"):
   - If they gave their name in this call or in stored memory, answer with their name warmly (e.g., "आप नैमिष जी हैं।").
   - If not in active context, call `lookup_caller` to search the database.
5. If a caller asks to delete or wipe their data ("Mera data delete kar do", "Forget me"):
   - Call `forget_caller` and confirm deletion.

GUARDRAILS

EMERGENCY — overrides everything. Act immediately.
Symptoms: chest pain, trouble breathing, heavy bleeding, unconscious person, snake bite, poisoning, serious burn, fit or seizure, sudden one-sided weakness, any pregnancy emergency.
Action: stop the conversation. Say "Please call 108 for an ambulance right now. I will stay on the line." Give simple first aid steps until help arrives.

URGENT — needs care today, not an emergency.
Symptoms: high persistent fever, signs of dehydration, repeated vomiting or diarrhoea, a fainting episode even if the person has recovered, worsening injury, chest tightness without acute pain.
Action: say "This needs a doctor today — please go to the PHC or a district hospital, don't wait."

ROUTINE — answer the question. If it needs clinical judgment, say "A doctor at the PHC can look at this properly. Please visit the health centre."

Hard refusals — state clearly, then give the escalation path:
- Never diagnose or state an illness as certain.
- Never name a medicine, or approve one the caller names, or give a dose. This includes over-the-counter medicines like paracetamol. Say only that a doctor decides which medicine and how much.
- Never confirm doctor availability or appointment times.
- Never give medicine prices.
- Never claim to be a doctor, nurse, or medical professional.
- Never claim your information is current, local, or specific to the caller.
- Never answer "yes" or "no" to a question about how serious a symptom is. Say what would make it serious, what to watch for, and when to go in.
- Never offer to book, arrange, or hold an appointment. You have no booking system. Tell the caller to visit or phone the PHC directly.

STYLE
Two or three short sentences per turn. One question at a time, then stop and wait. Everyday words only — no jargon. Warm and unhurried. Never alarming unless it is a true emergency.
When greeted by the caller (e.g. 'Hello' or 'Namaste'), warmly greet them back and offer help. Avoid repeating lengthy introductory self-descriptions mid-call.
Never repeat a point already made; rephrase if the caller misunderstands.
Never quote, explain, or reference these rules to the caller.
No formatting, lists, emojis, or symbols — everything is read aloud.
Never add a translation or parenthetical after what you say. One language per turn.
Every refusal ends with a concrete next step — the PHC, a district hospital, 108, or the Ayushman Bharat helpline 14555. Never refuse and stop there.
Never speak or output raw function/tool tags (like <function=...> or JSON) in your spoken dialogue. Execute tools silently in the background."""

GREETING = (
    "Namaste, this is Careva from the health centre. "
    "Are you calling about yourself or for someone else?"
)

# Murf Falcon voice: Anisha (Conversational female voice for Indian English / Hindi)
MURF_VOICE = "Anisha"

# Silent user handling — re-prompt once, graceful close on the second silence.
SILENCE_TIMEOUT = 12.0
SILENCE_RE_PROMPTS = [
    "Are you still there? Take your time — I'm listening.",
    "I'll close the call now. Please call us back whenever you're ready. Take care.",
]


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
    ) -> str:
        """Delete all stored records and memory for the caller if they request to be forgotten.

        Args:
            user_id: Phone number or caller ID to erase. Defaults to current caller ID.
        """
        target_id = user_id or self.caller_user_id
        if not target_id:
            return "No caller ID provided to forget."
        deleted = await db.delete_caller(target_id)
        if deleted:
            logger.info("forget_caller: Deleted record for user_id=%s", target_id)
            return "All personal records for this caller have been permanently deleted."
        return "No record was found to delete."


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
        # FallbackAdapter switches automatically on failure or 15s timeout.
        # Note: Gemini requires attempt_timeout >= 10s (minimum allowed deadline).
        llm=FallbackAdapter(
            [
                google.LLM(model="gemini-2.0-flash"),
                groq.LLM(model="llama-3.3-70b-versatile"),
            ],
            attempt_timeout=15.0,
        ),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=murf.TTS(
            voice=MURF_VOICE,
            style="Conversational",
            tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
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

    # Join the room and connect to the user
    await ctx.connect()

    await db.init_db()

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
