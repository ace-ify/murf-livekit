import logging

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    inference,
    metrics,
    tokenize,
    room_io,
)
from livekit.plugins import murf, silero, google, groq, deepgram, noise_cancellation
from livekit.agents.llm import FallbackAdapter
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Track: Health Access (#VoiceForBharat)
SYSTEM_PROMPT = """You are Asha, a health access helpline assistant for a rural primary health centre in India.

You help with three things: understanding a health problem well enough to route it to the right care, visits to the health centre, and plain answers about common conditions, vaccination and government health schemes.

EMERGENCY RULE, overrides everything below. On chest pain, trouble breathing, heavy bleeding, an unconscious person, snake bite, poisoning, serious burn, a fit or seizure, sudden weakness on one side of the body, or any pregnancy problem: stop the normal conversation, tell them to call 108 for an ambulance now, and stay on the line with simple first aid steps until help arrives.

You are not a doctor. Never diagnose, never state an illness as certain, never name a prescription medicine or a dose. Say what the symptoms could point to in general terms, then who to see and how soon.

LANGUAGE. Decide from the caller's first words: Hindi, English, or Hinglish. Stay in that language for the whole call. Only switch if the caller switches first. Never change language mid-answer.

SPEAKING. Two or three short sentences, never more. One question at a time, then stop and wait. Everyday words only. Warm and unhurried, never alarming.

NEVER repeat a point you have already made. If the caller misunderstands, say it a different way or move on, do not restate the same sentence.

These rules describe how YOU speak. Never quote them, explain them, or ask the caller to change how they talk.

No formatting, lists, emojis or symbols. Everything you say is read aloud.

Open with: "Namaste, this is Asha from the health centre. Are you calling about yourself or for someone else?\""""

# Murf Falcon voice. Murf accepts either the prefixed id ("en-IN-anisha") or the
# bare actor name ("anisha"). Swap to Samar or Pooja, or a hi-IN locale, here —
# it's the only knob needed to change the voice.
MURF_VOICE = "en-IN-anisha"
MURF_LOCALE = "en-IN"


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)

    # To add tools, use the @function_tool decorator.
    # Here's an example that adds a simple weather tool.
    # You also have to add `from livekit.agents import function_tool, RunContext` to the top of this file
    # @function_tool
    # async def lookup_weather(self, context: RunContext, location: str):
    #     """Use this tool to look up current weather information in the given location.
    #
    #     If the location is not supported by the weather service, the tool will indicate this. You must tell the user the location's weather is unavailable.
    #
    #     Args:
    #         location: The location to look up weather information for (e.g. city name)
    #     """
    #
    #     logger.info(f"Looking up weather for {location}")
    #
    #     return "sunny with a temperature of 70 degrees."


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
        # Groq (llama-3.1-8b-instant) as primary — lowest latency, no demand spikes.
        # Gemini falls in automatically if Groq fails or times out (attempt_timeout=5s).
        llm=FallbackAdapter([
            groq.LLM(model="llama-3.1-8b-instant"),
            google.LLM(model="gemini-2.0-flash-lite-001"),
        ]),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=murf.TTS(
                voice=MURF_VOICE,
                locale=MURF_LOCALE,
                style="Conversation",
                tokenizer=tokenize.basic.SentenceTokenizer(min_sentence_len=2),
                text_pacing=True
            ),
        # VAD and turn detection are used to determine when the user is speaking and when the agent should respond
        # See more at https://docs.livekit.io/agents/build/turns
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation=True,
    )

    # Day 1 optional extra: baseline response latency, logged once per turn.
    # The components arrive as separate metrics events, so accumulate then log.
    #
    # eou (turn-detection delay) is reported as None whenever LiveKit judges VAD
    # unreliable — see audio_recognition.py: "better than providing likely wrong
    # values". So it is printed as "n/a", never as 0, and never silently summed
    # into the total: a 0 there would read as "no delay" when it means "unknown".
    #
    # ponytail: plain dict, not a per-speech_id map — one caller talks at a time.
    # If you ever run concurrent speech, key this by m.speech_id.
    turn = {}

    @session.on("metrics_collected")
    def _on_metrics(ev):
        m = ev.metrics
        if isinstance(m, metrics.EOUMetrics):
            # 0.0 is the library's "couldn't measure" sentinel, so treat it as missing.
            turn["eou"] = m.end_of_utterance_delay or None
        elif isinstance(m, metrics.LLMMetrics):
            turn["llm"] = m.ttft
        elif isinstance(m, metrics.TTSMetrics):
            eou, llm = turn.get("eou"), turn.get("llm", 0.0)
            logger.info(
                "LATENCY reply=%.0fms (llm_ttft=%.0f + tts_ttfb=%.0f), turn_detect=%s",
                (llm + m.ttfb) * 1000,
                llm * 1000,
                m.ttfb * 1000,
                f"{eou * 1000:.0f}ms" if eou else "n/a",
            )
            turn.clear()

    # To use a realtime model instead of a voice pipeline, use the following session setup instead.
    # (Note: This is for the OpenAI Realtime API. For other providers, see https://docs.livekit.io/agents/models/realtime/))
    # 1. Install livekit-agents[openai]
    # 2. Set OPENAI_API_KEY in .env.local
    # 3. Add `from livekit.plugins import openai` to the top of this file
    # 4. Use the following session setup instead of the version above
    # session = AgentSession(
    #     llm=openai.realtime.RealtimeModel(voice="marin")
    # )

    # # Add a virtual avatar to the session, if desired
    # # For other providers, see https://docs.livekit.io/agents/models/avatar/
    # avatar = hedra.AvatarSession(
    #   avatar_id="...",  # See https://docs.livekit.io/agents/models/avatar/plugins/hedra
    # )
    # # Start the avatar and wait for it to join
    # await avatar.start(session, room=ctx.room)

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=Assistant(),
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


if __name__ == "__main__":
    cli.run_app(server)
