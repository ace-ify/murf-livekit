from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
