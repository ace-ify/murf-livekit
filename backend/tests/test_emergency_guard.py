"""Emergency guard + language mirroring checks.

The heart-attack bug: agent said "theek hai, main call rakh rahi hoon" and hung up.
These are the smallest checks that fail if that regresses.
"""

import asyncio
import re

import pytest

from agent import EMERGENCY_RE, RED_FLAG_RE, Assistant, _consent_yes


@pytest.mark.parametrize(
    "utterance",
    [
        "my friend has fever",
        "mere dost ko bukhar hai",
        "mujhe halka sardi jukam hai",
        "where is the nearest PHC",
        "Dolo 650 ka rate kya hai",
        "OPD kitne baje khulta hai",
    ],
)
def test_routine_is_not_a_red_flag(utterance):
    """These must NOT unlock escalation — this is the "fever got escalated" bug."""
    assert not RED_FLAG_RE.search(utterance), utterance


@pytest.mark.parametrize(
    "utterance",
    [
        "my friend just had a heart attack",
        "the fever is getting worse",
        "bukhar badh raha hai",
        "I want to talk to a doctor",
        "mujhe kisi se baat karni hai",
        "she is pregnant and bleeding",
        "my newborn is not feeding",
        "which medicine should I take",
        "kaunsi dawai leni chahiye",
    ],
)
def test_red_flag_unlocks_escalation(utterance):
    assert RED_FLAG_RE.search(utterance), utterance


@pytest.mark.asyncio
async def test_escalation_refused_for_routine_complaint():
    """No red flag in the conversation + low/medium urgency -> no case is filed."""
    a = Assistant(caller_user_id="+919876543210")
    res = await a.create_escalation(
        None, consent_given="true", what_happened="friend has fever", urgency="low"
    )
    assert "NOT escalated" in res
    assert a.escalation_created_flag is False


@pytest.mark.asyncio
async def test_escalation_allowed_once_red_flag_seen(monkeypatch, tmp_path):
    """Same routine urgency, but a red flag was heard -> the guard steps aside."""
    import agent as agent_mod
    import db

    dbp = str(tmp_path / "t.db")
    monkeypatch.setattr(db, "DB_PATH", dbp, raising=False)
    monkeypatch.setattr(agent_mod, "ESCALATION_WEBHOOK_URL", "")
    monkeypatch.setattr(agent_mod, "ESCALATION_ADMIN_PHONE", "")
    await db.init_db(dbp)

    a = Assistant(caller_user_id="+919876543210")
    a.redflag_flag = True
    res = await a.create_escalation(
        None,
        consent_given="true",
        what_happened="fever getting worse for 5 days",
        urgency="low",
    )
    assert "NOT escalated" not in res


@pytest.mark.parametrize("val", ["true", "TRUE", " yes ", "haan", "1", True])
def test_consent_granted(val):
    assert _consent_yes(val) is True


@pytest.mark.parametrize(
    "val", ["false", "no", "nahi", "", "maybe", "null", "None", None, False]
)
def test_consent_fails_closed(val):
    """Anything unrecognised must mean NO consent — never leak on a garbage value."""
    assert _consent_yes(val) is False


def test_consent_params_are_strings_not_booleans():
    """Groq's llama-3.3 emits "true" for boolean params and Groq 400s the turn.

    Assert on the schema actually sent to the LLM, not the Python annotation.
    """
    from livekit.agents.llm import utils as llm_utils

    a = Assistant()
    for tool in (a.save_caller_info, a.create_escalation):
        schema = llm_utils.build_legacy_openai_schema(tool, internally_tagged=True)
        prop = schema["parameters"]["properties"]["consent_given"]
        assert prop["type"] == "string", (tool, prop)


@pytest.mark.parametrize(
    "utterance",
    [
        "my friend just had a heart attack",
        "mere dost ko dil ka daura pada hai",
        "उसको सीने में दर्द हो रहा है",
        "he is not breathing properly",
        "saans nahi aa rahi hai",
        "she is unconscious",
        "bahut khoon bah raha hai",
    ],
)
def test_emergency_detected(utterance):
    assert EMERGENCY_RE.search(utterance), utterance


@pytest.mark.parametrize(
    "utterance",
    [
        "mujhe halka bukhar hai",
        "where is the nearest PHC",
        "Dolo 650 ka rate kya hai",
        "my head hurts a little",
    ],
)
def test_no_false_emergency(utterance):
    assert not EMERGENCY_RE.search(utterance), utterance


def test_end_call_refuses_unhandled_emergency():
    a = Assistant()
    a.emergency_flag = True
    out = asyncio.run(a.end_call.__wrapped__(a, None, reason="caller done"))
    assert "REFUSED" in out and "108" in out


def test_end_call_allowed_after_escalation():
    """Once 108 + escalation happened, hanging up is legitimate — but in English."""
    a = Assistant()
    a.emergency_flag = True
    a.escalation_created_flag = True
    a.tts_locale = "en-IN"

    said = []

    class _S:
        def say(self, text):
            said.append(text)
            return self

        async def wait_for_playout(self):
            return None

        def shutdown(self):
            said.append("<shutdown>")

    class _Ctx:
        session = _S()

    out = asyncio.run(a.end_call.__wrapped__(a, _Ctx()))
    assert out == "Call ended."
    # English locale must not produce a Hinglish goodbye.
    assert not re.search(r"theek hai|shubh ho", said[0], re.IGNORECASE), said[0]
