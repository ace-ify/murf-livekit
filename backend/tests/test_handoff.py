from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from livekit.agents import AgentSession, inference, llm

from agent import Assistant
from clinic_specialist import ClinicSpecialist


def _llm() -> llm.LLM:
    return inference.LLM(model="openai/gpt-4.1-mini")


# ─── Unit Tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handoff_refuses_during_emergency():
    """Emergency guard: Handoff tool must fail-safe refuse if an emergency is active."""
    assistant = Assistant(caller_user_id="+919876543210")
    assistant.emergency_flag = True

    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.say = MagicMock(return_value=AsyncMock(wait_for_playout=AsyncMock()))

    res = await assistant.transfer_to_clinic_specialist(
        ctx, reason="Check doctor availability for severe chest pain"
    )

    assert "REFUSED" in res
    assert "108" in res
    assert not ctx.session.update_agent.called


@pytest.mark.asyncio
async def test_handoff_preserves_caller_state():
    """Context preservation: Caller details, facts, and locale transfer to ClinicSpecialist."""
    assistant = Assistant(caller_user_id="user_priya_123")
    assistant.caller_facts = {"name": "Priya", "district": "Varanasi"}
    assistant.tts_locale = "en-IN"
    assistant.call_id = 42

    say_mock = MagicMock(return_value=AsyncMock(wait_for_playout=AsyncMock()))
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.say = say_mock
    ctx.session.update_agent = MagicMock()

    res = await assistant.transfer_to_clinic_specialist(
        ctx, reason="Detailed OPD timings and doctor schedule"
    )

    assert "Successfully transferred" in res
    assert ctx.session.update_agent.called

    # Verify new agent instance
    specialist = ctx.session.update_agent.call_args[0][0]
    assert isinstance(specialist, ClinicSpecialist)
    assert specialist.caller_user_id == "user_priya_123"
    assert specialist.caller_facts.get("name") == "Priya"
    assert specialist.caller_facts.get("district") == "Varanasi"
    assert specialist.tts_locale == "en-IN"
    assert specialist.call_id == 42


@pytest.mark.asyncio
async def test_transfer_back_to_main_agent_preserves_state():
    """Return handoff: Returning to main Careva agent restores context."""
    specialist = ClinicSpecialist(
        caller_user_id="user_priya_123",
        caller_facts={"name": "Priya", "district": "Varanasi"},
        tts_locale="hi-IN",
        call_id=42,
    )

    say_mock = MagicMock(return_value=AsyncMock(wait_for_playout=AsyncMock()))
    ctx = MagicMock()
    ctx.session = MagicMock()
    ctx.session.say = say_mock
    ctx.session.update_agent = MagicMock()

    res = await specialist.transfer_back_to_main_agent(
        ctx, reason="Medicine pricing question"
    )

    assert "Successfully returned" in res
    assert ctx.session.update_agent.called

    main_agent = ctx.session.update_agent.call_args[0][0]
    assert isinstance(main_agent, Assistant)
    assert main_agent.caller_user_id == "user_priya_123"
    assert main_agent.caller_facts.get("name") == "Priya"
    assert main_agent.tts_locale == "hi-IN"
    assert main_agent.call_id == 42


@pytest.mark.asyncio
async def test_clinic_specialist_tools():
    """Specialist tools: get_facility_details_and_timings returns in-depth facility info."""
    specialist = ClinicSpecialist(
        caller_user_id="user_test_001",
        caller_facts={"district": "Varanasi"},
    )
    ctx = MagicMock()

    # Tool 1: get_facility_details_and_timings
    details = await specialist.get_facility_details_and_timings(
        ctx, facility_name_or_location="Shivpur PHC", specific_requirement="timings"
    )
    assert "Shivpur" in details or "Varanasi" in details or "OPD" in details
    assert "Timings" in details or "timings" in details.lower()
    assert (
        "Registration" in details or "Token" in details or "walk-in" in details.lower()
    )

    # Tool 2: find_nearest_health_facility
    summary = await specialist.find_nearest_health_facility(
        ctx, location_or_pincode="Varanasi"
    )
    assert (
        "anusaar" in summary
        or "kendra" in summary.lower()
        or "hospital" in summary.lower()
    )


# ─── LLM-as-Judge Evaluation Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_main_routes_detailed_facility_to_specialist():
    """Main agent routes detailed OPD timings or clinic procedures to Clinic Specialist."""
    async with (
        _llm() as eval_llm,
        AgentSession(llm=eval_llm) as session,
    ):
        assistant = Assistant()
        await session.start(assistant)

        result = await session.run(
            user_input="What are the detailed OPD timings and doctor schedules at the Shivpur PHC, and how does the token system work?"
        )

        # Evaluate that the assistant calls transfer_to_clinic_specialist
        result.expect.next_event().is_function_call(
            name="transfer_to_clinic_specialist"
        )


@pytest.mark.asyncio
async def test_main_does_not_handoff_for_generic_medicines():
    """Negative test: Routine medicine query must NOT hand off to clinic specialist."""
    async with (
        _llm() as eval_llm,
        AgentSession(llm=eval_llm) as session,
    ):
        assistant = Assistant()
        await session.start(assistant)

        result = await session.run(
            user_input="What is the generic price of Paracetamol 650 at Jan Aushadhi Kendra?"
        )

        # Evaluate that the assistant calls lookup_generic_medicine directly
        result.expect.next_event().is_function_call(name="lookup_generic_medicine")


@pytest.mark.asyncio
async def test_main_does_not_handoff_for_schemes():
    """Negative test: Health scheme questions must NOT hand off to clinic specialist."""
    async with (
        _llm() as eval_llm,
        AgentSession(llm=eval_llm) as session,
    ):
        assistant = Assistant()
        await session.start(assistant)

        result = await session.run(
            user_input="How much coverage is provided under Ayushman Bharat PM-JAY scheme?"
        )

        # Evaluate that the assistant calls search_health_guidelines directly
        result.expect.next_event().is_function_call(name="search_health_guidelines")
