"""Day 7 — escalation store: refs, dedupe, PII scrub, status transitions."""

from pathlib import Path

import pytest

import db

RED_FLAG = "Papa ko seene mein dard aur saans phool rahi hai, 2 din se."


@pytest.fixture
async def esc_db(tmp_path: Path) -> str:
    db_file = str(tmp_path / "test_helpline.db")
    await db.init_db(db_file)
    return db_file


@pytest.mark.asyncio
async def test_create_escalation_and_get_by_ref(esc_db: str):
    rec = await db.create_escalation(
        caller_user_id="+919876543210",
        what_happened=RED_FLAG,
        urgency="emergency",
        caller_name="Ramesh",
        already_checked="advised 108, gave nearest CHC",
        followup_method="call back on this number",
        db_path=esc_db,
    )
    assert rec is not None
    assert rec["ref"] == "ESC-0001"
    assert rec["status"] == "open"
    assert rec["urgency"] == "emergency"
    assert rec["deduped"] is False

    # The caller reads the number back however they like.
    for spoken in ("ESC-0001", "esc 1", "escalation 0001"):
        found = await db.get_escalation(spoken, db_path=esc_db)
        assert found is not None and found["ref"] == "ESC-0001"

    # Scoped lookup: another caller cannot read this case.
    assert (
        await db.get_escalation("ESC-0001", caller_user_id="other", db_path=esc_db)
        is None
    )
    assert await db.get_escalation(
        "ESC-0001", caller_user_id="+919876543210", db_path=esc_db
    )
    assert await db.get_escalation("no digits here", db_path=esc_db) is None


@pytest.mark.asyncio
async def test_dedupe_updates_same_ref(esc_db: str):
    first = await db.create_escalation(
        "+91999", RED_FLAG, urgency="high", db_path=esc_db
    )
    second = await db.create_escalation(
        "+91999", RED_FLAG, urgency="emergency", caller_name="Ramesh", db_path=esc_db
    )
    assert second["ref"] == first["ref"]
    assert second["deduped"] is True
    assert second["urgency"] == "emergency"  # latest report wins
    assert second["caller_name"] == "Ramesh"
    assert len(await db.list_escalations("open", db_path=esc_db)) == 1

    # A different caller with the same complaint is a separate case.
    # (Its ref is not necessarily ESC-0002: an upsert that hits the conflict still
    # burns an autoincrement id, so refs may have gaps. Uniqueness is what matters.)
    other = await db.create_escalation("+91888", RED_FLAG, db_path=esc_db)
    assert other["ref"] != first["ref"]
    assert len(await db.list_escalations("open", db_path=esc_db)) == 2


@pytest.mark.asyncio
async def test_dedupe_slot_frees_after_resolve(esc_db: str):
    first = await db.create_escalation("+91999", RED_FLAG, db_path=esc_db)
    await db.update_escalation_status(
        first["ref"], "resolved", "ANM visited", db_path=esc_db
    )
    again = await db.create_escalation("+91999", RED_FLAG, db_path=esc_db)
    assert again["ref"] != first["ref"]
    assert again["deduped"] is False
    assert len(await db.list_escalations("", db_path=esc_db)) == 2


@pytest.mark.asyncio
async def test_scrub_pii_strips_identifiers_keeps_clinical_numbers(esc_db: str):
    for secret in (
        "+919876543210",
        "9876543210",
        "1234 5678 9012",
        "1234-5678-9012",
        "123456789012",
        "OTP is 448213",
        "otp 4482",
        "account no 55012",
        "a/c 998877665544",
        "my upi pin is 1234",
        "card 4111111111111111",
    ):
        assert "removed" in db.scrub_pii(secret), secret

    keep = (
        "45-55 age band, child 5-10, call 108 or 1075, PM-JAY 14555, "
        "pin code 221002, BP 140/90, temp 102"
    )
    assert db.scrub_pii(keep) == keep

    # Bare "pin <digits>" is ambiguous (postal PIN vs UPI PIN) and is scrubbed on
    # purpose: losing a pincode costs a little context, leaking a PIN does not.
    assert "removed" in db.scrub_pii("pin 221002")

    # Round trip: what is stored is already scrubbed.
    rec = await db.create_escalation(
        "+91999",
        f"{RED_FLAG} Unka number 9876543210 hai, OTP 448213, aadhaar 1234 5678 9012. BP 140/90.",
        already_checked="advised 108",
        db_path=esc_db,
    )
    assert "9876543210" not in rec["what_happened"]
    assert "448213" not in rec["what_happened"]
    assert "1234 5678 9012" not in rec["what_happened"]
    assert "BP 140/90" in rec["what_happened"]
    assert rec["already_checked"] == "advised 108"


@pytest.mark.asyncio
async def test_status_transitions_and_listing(esc_db: str):
    rec = await db.create_escalation("+91999", RED_FLAG, db_path=esc_db)
    assert (
        await db.update_escalation_status(rec["ref"], "banana", db_path=esc_db) is None
    )
    assert (
        await db.update_escalation_status("ESC-0099", "resolved", db_path=esc_db)
        is None
    )

    ack = await db.update_escalation_status(rec["ref"], "acknowledged", db_path=esc_db)
    assert ack["status"] == "acknowledged"
    assert await db.list_escalations("open", db_path=esc_db) == []

    done = await db.update_escalation_status(
        rec["ref"], "resolved", "ANM visited, referred to CHC", db_path=esc_db
    )
    assert done["status"] == "resolved"
    assert "ANM visited" in done["resolution_note"]
    assert len(await db.list_escalations("resolved", db_path=esc_db)) == 1
    assert len(await db.list_escalations("", db_path=esc_db)) == 1


@pytest.mark.asyncio
async def test_bad_input_and_urgency_fallback(esc_db: str):
    assert await db.create_escalation("", RED_FLAG, db_path=esc_db) is None
    assert await db.create_escalation("+91999", "   ", db_path=esc_db) is None
    assert (
        await db.create_escalation("+91999", "!!!", db_path=esc_db) is None
    )  # no dedupe key

    rec = await db.create_escalation(
        "+91999", RED_FLAG, urgency="URGENT!!", db_path=esc_db
    )
    assert rec["urgency"] == "medium"
