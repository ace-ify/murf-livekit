from pathlib import Path
from unittest.mock import MagicMock

import pytest

import agent
import db
from agent import Assistant


@pytest.fixture
def test_db(tmp_path: Path, monkeypatch) -> str:
    db_file = str(tmp_path / "test_helpline.db")
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", db_file)
    return db_file


@pytest.mark.asyncio
async def test_assistant_lookup_and_save_tools(test_db: str):
    await db.init_db(test_db)

    assistant = Assistant(caller_user_id="user_caller_001")
    ctx = MagicMock()

    # Step 1: Lookup before anything is saved -> should return new caller message
    lookup_res1 = await assistant.lookup_caller(ctx, user_id="user_caller_001")
    assert "No previous record found" in lookup_res1

    # Step 2: Attempt save with consent_given=False -> should refuse
    refuse_res = await assistant.save_caller_info(
        ctx,
        name="Vikram",
        consent_given=False,
        age_band="35-45",
    )
    assert "Consent was NOT given" in refuse_res
    record = await db.get_caller("user_caller_001", db_path=test_db)
    assert record is None

    # Step 3: Save with consent_given=True -> should succeed
    save_res = await assistant.save_caller_info(
        ctx,
        name="Vikram Singh",
        consent_given=True,
        language_preference="hi",
        age_band="35-45",
        ongoing_conditions="asthma",
        last_triage_outcome="routine PHC visit for seasonal cough",
    )
    assert "Successfully saved" in save_res

    # Step 4: Lookup again -> should return formatted facts
    lookup_res2 = await assistant.lookup_caller(ctx, user_id="user_caller_001")
    assert "Vikram Singh" in lookup_res2
    assert "35-45" in lookup_res2
    assert "asthma" in lookup_res2
    assert "routine PHC visit for seasonal cough" in lookup_res2

    # Step 5: Forget caller
    forget_res = await assistant.forget_caller(ctx, user_id="user_caller_001")
    assert "permanently deleted" in forget_res

    lookup_res3 = await assistant.lookup_caller(ctx, user_id="user_caller_001")
    assert "No previous record found" in lookup_res3


@pytest.mark.asyncio
async def test_search_health_guidelines_rag():
    assistant = Assistant()
    ctx = MagicMock()

    # Query 1: PMJAY Ayushman Bharat
    pmjay_res = await assistant.search_health_guidelines(
        ctx, query="Ayushman Bharat coverage amount 5 lakh"
    )
    assert "5 Lakh" in pmjay_res
    assert "Hospital" in pmjay_res or "hospitalization" in pmjay_res.lower()

    # Query 2: Universal Immunization Programme
    vaccine_res = await assistant.search_health_guidelines(
        ctx, query="6 weeks pentavalent rotavirus polio vaccine"
    )
    assert "Pentavalent" in vaccine_res
    assert "Polio" in vaccine_res or "OPV" in vaccine_res

    # Query 3: JSSK Free Delivery
    jssk_res = await assistant.search_health_guidelines(
        ctx, query="Janani Shishu Suraksha free delivery pregnant women"
    )
    assert "Institutional Delivery" in jssk_res or "Free" in jssk_res


@pytest.mark.asyncio
async def test_find_nearest_health_facility_tool():
    assistant = Assistant()
    ctx = MagicMock()

    # Direct lookup with location
    res = await assistant.find_nearest_health_facility(
        ctx, location_or_pincode="Varanasi"
    )
    assert "Shivpur" in res or "Varanasi" in res or "hospital" in res.lower()
    assert "anusaar" in res

    # Tool chaining: auto-uses caller_facts if location is empty
    assistant.caller_facts = {"district": "Pune"}
    res_chained = await assistant.find_nearest_health_facility(
        ctx, location_or_pincode=""
    )
    assert (
        "hospital" in res_chained.lower()
        or "kendra" in res_chained.lower()
        or "pune" in res_chained.lower()
    )
    assert "anusaar" in res_chained


# ─── Day 7: human escalation tools ───────────────────────────────────────────

RED_FLAG = "Papa ko seene mein dard aur saans phool rahi hai, 2 din se."


@pytest.mark.asyncio
async def test_create_escalation_refuses_without_consent(test_db: str):
    await db.init_db(test_db)
    assistant = Assistant(caller_user_id="+919876543210")
    ctx = MagicMock()

    res = await assistant.create_escalation(
        ctx, consent_given=False, what_happened=RED_FLAG, urgency="emergency"
    )
    assert "Consent was NOT given" in res
    assert await db.list_escalations(db_path=test_db) == []


@pytest.mark.asyncio
async def test_create_escalation_returns_ref_then_status_lookup(test_db: str):
    await db.init_db(test_db)
    # job_ctx stays None on purpose: proves the publish guard holds off-room.
    assistant = Assistant(caller_user_id="+919876543210")
    ctx = MagicMock()

    res = await assistant.create_escalation(
        ctx,
        consent_given=True,
        what_happened=RED_FLAG,
        urgency="emergency",
        already_checked="advised 108, gave nearest CHC",
        followup_method="call back on this number",
        caller_name="Ramesh",
    )
    assert "ESC-0001" in res
    assert "do NOT promise an immediate" in res

    rows = await db.list_escalations(db_path=test_db)
    assert len(rows) == 1 and rows[0]["callback_phone"] == "+919876543210"

    status = await assistant.check_escalation_status(ctx, ref="esc 1")
    assert "ESC-0001" in status and "status open" in status

    # Another caller's session cannot read this case.
    stranger = Assistant(caller_user_id="+910000000000")
    assert "No escalation found" in await stranger.check_escalation_status(
        ctx, ref="ESC-0001"
    )


@pytest.mark.asyncio
async def test_escalation_survives_dead_webhook(test_db: str, monkeypatch):
    await db.init_db(test_db)
    monkeypatch.setattr(agent, "ESCALATION_WEBHOOK_URL", "http://127.0.0.1:1/hook")
    assistant = Assistant(caller_user_id="+919876543210")

    res = await assistant.create_escalation(
        MagicMock(), consent_given=True, what_happened=RED_FLAG, urgency="high"
    )
    assert "ESC-0001" in res
    assert len(await db.list_escalations(db_path=test_db)) == 1
