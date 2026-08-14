"""Agent handoff orchestration for multi-agent voice workflows.

Handles seamless in-session handoffs between the primary triage agent (Assistant / Careva)
and specialized domain agents (ClinicSpecialist) while preserving conversation context,
caller memory, telemetry, and room state.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livekit.agents import Agent, RunContext

logger = logging.getLogger("handoff")

# ponytail: in-session agent handoff using session.update_agent, preserving
# audio stream and room connection without requiring separate room creation or
# participant migration.


async def _speak(
    session, text: str, wait: bool = False, allow_interruptions: bool = True
) -> None:
    """Helper to speak through AgentSession safely in real runs and test mocks."""
    try:
        handle = session.say(text, allow_interruptions=allow_interruptions)
        if (
            wait
            and hasattr(handle, "wait_for_playout")
            and callable(handle.wait_for_playout)
        ):
            res = handle.wait_for_playout()
            if inspect.isawaitable(res):
                await res
        elif inspect.isawaitable(handle):
            await handle
    except Exception as e:
        logger.debug("Speech playback helper notice: %s", e)


async def transfer_to_specialist(
    current_agent: Agent,
    context: RunContext,
    reason: str,
    specialist_type: str = "clinic",
) -> str:
    """Transfer caller from current agent to a domain specialist."""
    # Emergency guard: never hand off during an emergency
    if getattr(current_agent, "emergency_flag", False):
        logger.warning(
            "transfer_to_specialist REFUSED: emergency in progress (reason=%r)", reason
        )
        return (
            "REFUSED — you cannot transfer this call during a medical emergency. "
            "Tell the caller to call 108 immediately, then ask permission to send a summary "
            "to a human health worker and create an emergency escalation."
        )

    tts_locale = getattr(current_agent, "tts_locale", "en-IN")
    caller_facts = getattr(current_agent, "caller_facts", {}) or {}
    caller_name = caller_facts.get("name", "")

    # Step 1: Announce handoff to caller
    announcement = (
        "I'll connect you to our clinic specialist Samar who can help with that."
        if tts_locale.startswith("en")
        else "Main aapko apne clinic specialist Samar se connect kar rahi hoon jo isme madad karenge."
    )
    logger.info("Announcing handoff: %s", announcement)
    await _speak(context.session, announcement, wait=True)

    # Step 2: Instantiate ClinicSpecialist with inherited context
    from clinic_specialist import ClinicSpecialist

    specialist = ClinicSpecialist(
        caller_user_id=getattr(current_agent, "caller_user_id", ""),
        caller_facts=caller_facts,
        job_ctx=getattr(current_agent, "job_ctx", None),
        tts_locale=tts_locale,
        emergency_flag=getattr(current_agent, "emergency_flag", False),
        redflag_flag=getattr(current_agent, "redflag_flag", False),
        call_id=getattr(current_agent, "call_id", 0),
        call_start_dt=getattr(current_agent, "call_start_dt", None),
        user_turns=getattr(current_agent, "user_turns", 0),
        agent_turns=getattr(current_agent, "agent_turns", 0),
        escalation_created_flag=getattr(
            current_agent, "escalation_created_flag", False
        ),
    )

    # Step 3: Publish handoff event to room data channel if connected
    job_ctx = getattr(current_agent, "job_ctx", None)
    if job_ctx and job_ctx.room and job_ctx.room.local_participant:
        try:
            payload = json.dumps(
                {
                    "type": "agent_handoff",
                    "from_agent": "main",
                    "to_agent": "clinic_specialist",
                    "reason": reason,
                    "caller_user_id": specialist.caller_user_id,
                }
            ).encode("utf-8")
            await job_ctx.room.local_participant.publish_data(
                payload, topic="agent_handoff"
            )
        except Exception as e:
            logger.warning("Failed to publish agent_handoff event: %s", e)

    # Step 4: Update session agent and switch TTS voice to male (Samar)
    context.session.update_agent(specialist)
    try:
        context.session.tts.update_options(voice="Samar")
        logger.info("TTS voice updated to Samar (male) for specialist")
    except Exception as e:
        logger.warning("Failed to update TTS voice to Samar: %s", e)
    logger.info("Agent handoff completed -> ClinicSpecialist active")

    # Step 5: Specialist speaks its initial context-aware greeting
    if tts_locale.startswith("en"):
        specialist_greeting = (
            f"Namaste{' ' + caller_name + ' ji' if caller_name else ''}, I'm Samar, the clinic specialist. "
            f"I understand you need help with {reason}. Let me assist you."
        )
    else:
        specialist_greeting = (
            f"नमस्ते{' ' + caller_name + ' जी' if caller_name else ''}, मैं समर हूँ, क्लिनिक स्पेशलिस्ट। "
            f"मुझे पता चला कि आपको {reason} के बारे में जानकारी चाहिए। मैं आपकी सहायता करता हूँ।"
        )

    await _speak(context.session, specialist_greeting, allow_interruptions=True)
    return f"Successfully transferred to Clinic Specialist for: {reason}"


async def transfer_to_main(
    specialist_agent: Agent,
    context: RunContext,
    reason: str = "Facility questions complete",
) -> str:
    """Transfer caller back to main Careva agent."""
    tts_locale = getattr(specialist_agent, "tts_locale", "en-IN")
    caller_facts = getattr(specialist_agent, "caller_facts", {}) or {}

    # Step 1: Announce return handoff
    announcement = (
        "Let me transfer you back to Careva for further assistance."
        if tts_locale.startswith("en")
        else "Main aapko wapas Careva se connect kar rahi hoon jo aage madad karengi."
    )
    logger.info("Announcing return handoff: %s", announcement)
    await _speak(context.session, announcement, wait=True)

    # Step 2: Instantiate Assistant with updated context
    import agent

    main_agent = agent.Assistant(
        caller_user_id=getattr(specialist_agent, "caller_user_id", "")
    )
    main_agent.caller_facts = caller_facts
    main_agent.job_ctx = getattr(specialist_agent, "job_ctx", None)
    main_agent.tts_locale = tts_locale
    main_agent.emergency_flag = getattr(specialist_agent, "emergency_flag", False)
    main_agent.redflag_flag = getattr(specialist_agent, "redflag_flag", False)
    main_agent.call_id = getattr(specialist_agent, "call_id", 0)
    main_agent.call_start_dt = getattr(specialist_agent, "call_start_dt", None)
    main_agent.user_turns = getattr(specialist_agent, "user_turns", 0)
    main_agent.agent_turns = getattr(specialist_agent, "agent_turns", 0)
    main_agent.escalation_created_flag = getattr(
        specialist_agent, "escalation_created_flag", False
    )

    # Step 3: Publish handoff event
    job_ctx = getattr(specialist_agent, "job_ctx", None)
    if job_ctx and job_ctx.room and job_ctx.room.local_participant:
        try:
            payload = json.dumps(
                {
                    "type": "agent_handoff",
                    "from_agent": "clinic_specialist",
                    "to_agent": "main",
                    "reason": reason,
                    "caller_user_id": main_agent.caller_user_id,
                }
            ).encode("utf-8")
            await job_ctx.room.local_participant.publish_data(
                payload, topic="agent_handoff"
            )
        except Exception as e:
            logger.warning("Failed to publish return agent_handoff event: %s", e)

    # Step 4: Update session agent and restore TTS voice to female (Anisha)
    context.session.update_agent(main_agent)
    try:
        context.session.tts.update_options(voice="Anisha")
        logger.info("TTS voice restored to Anisha (female) for main agent")
    except Exception as e:
        logger.warning("Failed to restore TTS voice to Anisha: %s", e)
    logger.info("Return handoff completed -> Assistant (Careva) active")

    # Step 5: Careva speaks resumption greeting
    resumption = (
        "I'm back with you. How else can I help you today?"
        if tts_locale.startswith("en")
        else "मैं वापस आपके साथ हूँ। आज मैं आपकी और क्या सहायता कर सकती हूँ?"
    )
    await _speak(context.session, resumption, allow_interruptions=True)
    return f"Successfully returned to main Careva assistant (reason: {reason})"
