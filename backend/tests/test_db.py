from pathlib import Path

import pytest

import db


@pytest.fixture
def test_db_path(tmp_path: Path) -> str:
    return str(tmp_path / "test_helpline.db")


@pytest.mark.asyncio
async def test_init_and_save_caller_with_consent(test_db_path: str):
    await db.init_db(test_db_path)

    # Save caller with explicit consent
    caller = await db.save_caller(
        user_id="user_98765",
        name="Ramesh Kumar",
        language_preference="hi",
        facts={
            "age_band": "45-55",
            "ongoing_conditions": ["hypertension"],
            "last_triage_outcome": "Advised routine PHC visit for persistent fever",
        },
        consent_given=True,
        db_path=test_db_path,
    )

    assert caller is not None
    assert caller["user_id"] == "user_98765"
    assert caller["name"] == "Ramesh Kumar"
    assert caller["language_preference"] == "hi"
    assert caller["facts"]["age_band"] == "45-55"
    assert caller["consent_given"] is True

    # Retrieve caller by user_id
    retrieved = await db.get_caller("user_98765", db_path=test_db_path)
    assert retrieved is not None
    assert retrieved["name"] == "Ramesh Kumar"
    assert retrieved["facts"]["ongoing_conditions"] == ["hypertension"]


@pytest.mark.asyncio
async def test_save_refusal_when_consent_false(test_db_path: str):
    await db.init_db(test_db_path)

    # Attempt to save without consent
    result = await db.save_caller(
        user_id="user_unconsenting",
        name="Sita Devi",
        language_preference="hi",
        facts={"age_band": "30-40", "last_triage_outcome": "Cough triage"},
        consent_given=False,
        db_path=test_db_path,
    )

    # Hard rule: Must return None and NOT save in DB
    assert result is None
    record = await db.get_caller("user_unconsenting", db_path=test_db_path)
    assert record is None


@pytest.mark.asyncio
async def test_lookup_by_name(test_db_path: str):
    await db.init_db(test_db_path)
    await db.save_caller(
        user_id="user_123",
        name="Sunita Sharma",
        language_preference="hi",
        facts={"age_band": "50-60", "ongoing_conditions": ["diabetes"]},
        consent_given=True,
        db_path=test_db_path,
    )

    # Case-insensitive search by name
    found = await db.get_caller_by_name("sunita sharma", db_path=test_db_path)
    assert found is not None
    assert found["user_id"] == "user_123"

    not_found = await db.get_caller_by_name("Unknown Person", db_path=test_db_path)
    assert not_found is None


@pytest.mark.asyncio
async def test_delete_caller_forget_me(test_db_path: str):
    await db.init_db(test_db_path)
    await db.save_caller(
        user_id="user_forget",
        name="Anil",
        facts={"age_band": "25-35"},
        consent_given=True,
        db_path=test_db_path,
    )

    deleted = await db.delete_caller("user_forget", db_path=test_db_path)
    assert deleted is True

    record = await db.get_caller("user_forget", db_path=test_db_path)
    assert record is None


@pytest.mark.asyncio
async def test_format_caller_for_agent(test_db_path: str):
    caller = {
        "name": "Ramesh",
        "language_preference": "hi",
        "facts": {
            "age_band": "45-55",
            "ongoing_conditions": ["hypertension", "asthma"],
            "last_triage_outcome": "PHC checkup advised",
        },
        "last_interaction": "2026-08-09T10:00:00Z",
    }
    summary = db.format_caller_for_agent(caller)
    assert "Ramesh" in summary
    assert "45-55" in summary
    assert "hypertension, asthma" in summary
    assert "PHC checkup advised" in summary
