import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger("db")

DEFAULT_DB_PATH = os.getenv(
    "HELPLINE_DB_PATH",
    str(Path(__file__).parent.parent / "data" / "helpline.db"),
)


def _ensure_db_dir(db_path: str) -> str:
    """Resolve the DB path and make sure its folder exists.

    Resolving here (not in a default argument) is what lets tests point
    DEFAULT_DB_PATH at a tmp file — a default is bound once at import time.
    """
    resolved = db_path or DEFAULT_DB_PATH
    Path(resolved).parent.mkdir(parents=True, exist_ok=True)
    return resolved


async def init_db(db_path: str = "") -> None:
    """Initialize database and create tables if they do not exist."""
    db_path = _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as db:
        # The Next.js admin route writes this same file through node:sqlite, so the
        # default rollback journal would hand out SQLITE_BUSY. WAL is persistent.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS callers (
                user_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                language_preference TEXT DEFAULT 'hi',
                consent_given INTEGER DEFAULT 1,
                facts TEXT DEFAULT '{}',
                last_interaction TEXT DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_callers_name ON callers(name COLLATE NOCASE)"
        )
        # Day 7 — human escalations. AUTOINCREMENT on purpose: the id *is* the
        # reference number spoken to a caller, so a deleted row's id must never be reused.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS escalations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                caller_user_id  TEXT NOT NULL,
                caller_name     TEXT DEFAULT '',
                language        TEXT DEFAULT 'hi',
                urgency         TEXT DEFAULT 'medium',
                what_happened   TEXT NOT NULL,
                already_checked TEXT DEFAULT '',
                followup_method TEXT DEFAULT '',
                callback_phone  TEXT DEFAULT '',
                dedupe_key      TEXT NOT NULL,
                status          TEXT DEFAULT 'open',
                resolution_note TEXT DEFAULT '',
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Partial unique index = dedupe decided atomically inside SQLite, so two
        # concurrent sessions cannot both insert the same open case.
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_esc_open_dedupe "
            "ON escalations(caller_user_id, dedupe_key) WHERE status = 'open'"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_esc_status ON escalations(status, created_at DESC)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                room_name          TEXT DEFAULT '',
                channel            TEXT DEFAULT 'browser',
                started_at         TEXT NOT NULL,
                ended_at           TEXT,
                duration_secs      REAL,
                outcome            TEXT DEFAULT 'in_progress',
                outcome_reason     TEXT DEFAULT '',
                escalation_created INTEGER DEFAULT 0,
                user_turns         INTEGER DEFAULT 0,
                agent_turns        INTEGER DEFAULT 0
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_calls_outcome ON calls(outcome, started_at DESC)"
        )
        await db.commit()
    logger.info("Database initialized at %s", db_path)


async def get_caller(user_id: str, db_path: str = "") -> dict[str, Any] | None:
    """Fetch caller record by user_id."""
    if not user_id:
        return None
    db_path = _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, name, language_preference, consent_given, facts, last_interaction, created_at "
            "FROM callers WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return _row_to_dict(row)


async def get_caller_by_name(name: str, db_path: str = "") -> dict[str, Any] | None:
    """Fetch caller record by name (case-insensitive lookup)."""
    if not name or not name.strip():
        return None
    db_path = _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, name, language_preference, consent_given, facts, last_interaction, created_at "
            "FROM callers WHERE LOWER(name) = LOWER(?) ORDER BY last_interaction DESC LIMIT 1",
            (name.strip(),),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return _row_to_dict(row)


async def save_caller(
    user_id: str,
    name: str,
    language_preference: str = "hi",
    facts: dict[str, Any] | None = None,
    consent_given: bool = True,
    db_path: str = "",
) -> dict[str, Any] | None:
    """Save or update caller information.

    Hard Rule (Health Access Track):
    If consent_given is False, no data is stored and None is returned.
    """
    if not consent_given:
        logger.warning(
            "Consent not given for user_id=%s. Refusing to store data.", user_id
        )
        return None

    if not user_id or not name:
        logger.error("Cannot save caller without user_id and name")
        return None

    db_path = _ensure_db_dir(db_path)
    facts_dict = facts or {}
    facts_json = json.dumps(facts_dict, ensure_ascii=False)
    now_iso = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO callers (user_id, name, language_preference, consent_given, facts, last_interaction)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name = excluded.name,
                language_preference = excluded.language_preference,
                consent_given = 1,
                facts = excluded.facts,
                last_interaction = excluded.last_interaction
            """,
            (user_id, name.strip(), language_preference, facts_json, now_iso),
        )
        await db.commit()

    logger.info("Saved caller memory for user_id=%s (name=%s)", user_id, name)
    return {
        "user_id": user_id,
        "name": name.strip(),
        "language_preference": language_preference,
        "consent_given": True,
        "facts": facts_dict,
        "last_interaction": now_iso,
    }


async def delete_caller(user_id: str = "", name: str = "", db_path: str = "") -> bool:
    """Delete caller record (forget me feature) by user_id or name."""
    clean_id = (user_id or "").strip()
    clean_name = (name or "").strip()
    if not clean_id and not clean_name:
        return False
    db_path = _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as db:
        if clean_id and clean_name:
            cursor = await db.execute(
                "DELETE FROM callers WHERE user_id = ? OR LOWER(name) = LOWER(?) OR name = ?",
                (clean_id, clean_name, clean_name),
            )
        elif clean_id:
            cursor = await db.execute(
                "DELETE FROM callers WHERE user_id = ? OR LOWER(name) = LOWER(?) OR name = ?",
                (clean_id, clean_id, clean_id),
            )
        else:
            cursor = await db.execute(
                "DELETE FROM callers WHERE LOWER(name) = LOWER(?) OR name = ?",
                (clean_name, clean_name),
            )
        await db.commit()
        return cursor.rowcount > 0


def format_caller_for_agent(caller: dict[str, Any]) -> str:
    """Format caller record into concise context for LLM response generation."""
    name = caller.get("name", "Caller")
    lang = caller.get("language_preference", "hi")
    facts = caller.get("facts", {})
    last_interaction = caller.get("last_interaction", "recently")

    facts_summary = []
    if age_band := facts.get("age_band"):
        facts_summary.append(f"Age band: {age_band}")
    if conditions := facts.get("ongoing_conditions"):
        cond_str = (
            ", ".join(conditions) if isinstance(conditions, list) else str(conditions)
        )
        facts_summary.append(f"Ongoing conditions: {cond_str}")
    if last_triage := facts.get("last_triage_outcome"):
        facts_summary.append(f"Last triage outcome: {last_triage}")

    facts_text = (
        "; ".join(facts_summary)
        if facts_summary
        else "No previous health facts recorded"
    )
    return (
        f"Returning caller: {name} (Preferred language: {lang}). "
        f"Last interaction: {last_interaction}. "
        f"Recorded facts: {facts_text}."
    )


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    raw_facts = row["facts"]
    try:
        facts = json.loads(raw_facts) if raw_facts else {}
    except Exception:
        facts = {}
    return {
        "user_id": row["user_id"],
        "name": row["name"],
        "language_preference": row["language_preference"],
        "consent_given": bool(row["consent_given"]),
        "facts": facts,
        "last_interaction": row["last_interaction"],
        "created_at": row["created_at"],
    }


# ─── Day 7: human escalations ────────────────────────────────────────────────

URGENCY_LEVELS = ("low", "medium", "high", "emergency")
ESCALATION_STATUSES = ("open", "acknowledged", "resolved")

# Order matters: phones are matched before the generic long-number rule.
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)")
_AADHAAR_RE = re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)")
_LONGNUM_RE = re.compile(r"(?<!\d)\d{9,}(?!\d)")
_SECRET_RE = re.compile(
    # "pin code 221002" is a postal address, "upi pin 1234" is a secret — so bare
    # `pin` matches but `pin code` does not.
    r"(?i)\b(otp|pin(?!\s*code)|password|cvv|upi|aadhaar|aadhar|a/?c|"
    r"account(?:\s*(?:no|number|num))?|card)\b(\D{0,12})\d{4,8}(?!\d)"
)


def scrub_pii(text: str) -> str:
    """Strip identifiers from free text before it is stored or sent off-box.

    ponytail: deliberately no blanket 4-8 digit rule — 108, 102, 1075, 14555 (PM-JAY)
    and 6-digit pincodes are load-bearing in this domain, as are age bands like "30-40"
    and vitals like "BP 140/90". A short digit run is only removed when a secret keyword
    sits within 12 non-digit characters of it.
    """
    if not text:
        return ""
    t = _PHONE_RE.sub("[phone removed]", text)
    t = _AADHAAR_RE.sub("[id removed]", t)
    t = _LONGNUM_RE.sub("[number removed]", t)
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[removed]", t)


def escalation_ref(esc_id: int) -> str:
    """Reference spoken to the caller. Digits only — survives a noisy line and STT."""
    return f"ESC-{esc_id:04d}"


def _ref_to_id(ref: str) -> int:
    """Tolerant parse: "ESC-0007", "esc 7", "escalation 0007" all give 7."""
    digits = re.sub(r"\D", "", ref or "")
    return int(digits) if digits else 0


def _dedupe_key(what_happened: str) -> str:
    """Same complaint from the same caller collapses onto one open case."""
    return re.sub(r"[^a-z0-9]+", "", (what_happened or "").lower())[:60]


def _esc_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    rec = dict(row)
    rec["ref"] = escalation_ref(int(row["id"]))
    return rec


async def create_escalation(
    caller_user_id: str,
    what_happened: str,
    urgency: str = "medium",
    caller_name: str = "",
    language: str = "hi",
    already_checked: str = "",
    followup_method: str = "",
    callback_phone: str = "",
    db_path: str = "",
) -> dict[str, Any] | None:
    """Create or update this caller's open escalation.

    Returns the stored (scrubbed) record with "ref" and "deduped", or None on bad input.
    All free text is scrubbed here — the single choke point, so what is stored and what
    any webhook sends can never diverge.
    """
    if not caller_user_id or not (what_happened or "").strip():
        logger.error(
            "Cannot create escalation without caller_user_id and what_happened"
        )
        return None

    level = (urgency or "").strip().lower()
    if level not in URGENCY_LEVELS:
        level = "medium"

    what = scrub_pii(what_happened.strip())
    checked = scrub_pii(already_checked.strip())
    followup = scrub_pii(followup_method.strip())
    key = _dedupe_key(what)
    if not key:
        return None
    now_iso = datetime.now(timezone.utc).isoformat()

    db_path = _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        db.row_factory = aiosqlite.Row
        # ponytail: latest report wins on urgency; swap in a CASE max-ladder if a
        # downgrade ever buries a real emergency.
        await db.execute(
            """
            INSERT INTO escalations (caller_user_id, caller_name, language, urgency,
                what_happened, already_checked, followup_method, callback_phone,
                dedupe_key, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            ON CONFLICT(caller_user_id, dedupe_key) WHERE status = 'open' DO UPDATE SET
                caller_name = excluded.caller_name,
                language = excluded.language,
                urgency = excluded.urgency,
                what_happened = excluded.what_happened,
                already_checked = excluded.already_checked,
                followup_method = excluded.followup_method,
                callback_phone = excluded.callback_phone,
                updated_at = excluded.updated_at
            """,
            (
                caller_user_id,
                caller_name.strip(),
                language or "hi",
                level,
                what,
                checked,
                followup,
                callback_phone.strip(),
                key,
                now_iso,
                now_iso,
            ),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM escalations WHERE caller_user_id = ? AND dedupe_key = ? "
            "AND status = 'open'",
            (caller_user_id, key),
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        logger.error("Escalation upsert produced no row for %s", caller_user_id)
        return None
    rec = _esc_row_to_dict(row)
    rec["deduped"] = rec["created_at"] != rec["updated_at"]
    logger.info(
        "Escalation %s %s (urgency=%s, caller=%s)",
        rec["ref"],
        "updated" if rec["deduped"] else "created",
        rec["urgency"],
        caller_user_id,
    )
    return rec


async def get_escalation(
    ref: str, caller_user_id: str = "", db_path: str = ""
) -> dict[str, Any] | None:
    """Fetch by spoken reference. If caller_user_id is given, the row must belong to them."""
    esc_id = _ref_to_id(ref)
    if not esc_id:
        return None
    db_path = _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM escalations WHERE id = ?", (esc_id,)
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    if caller_user_id and row["caller_user_id"] != caller_user_id:
        logger.warning("Escalation %s does not belong to %s", ref, caller_user_id)
        return None
    return _esc_row_to_dict(row)


async def list_escalations(
    status: str = "open", limit: int = 50, db_path: str = ""
) -> list[dict[str, Any]]:
    """Newest first. status="" returns every status."""
    db_path = _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if status:
            query = (
                "SELECT * FROM escalations WHERE status = ? "
                "ORDER BY created_at DESC LIMIT ?"
            )
            params: tuple = (status, limit)
        else:
            query = "SELECT * FROM escalations ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
    return [_esc_row_to_dict(r) for r in rows]


async def update_escalation_status(
    ref: str,
    status: str,
    resolution_note: str = "",
    db_path: str = "",
) -> dict[str, Any] | None:
    """Set open|acknowledged|resolved. Returns the updated record, or None if invalid."""
    esc_id = _ref_to_id(ref)
    new_status = (status or "").strip().lower()
    if not esc_id or new_status not in ESCALATION_STATUSES:
        logger.warning("update_escalation_status: bad ref=%s status=%s", ref, status)
        return None
    db_path = _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        cursor = await db.execute(
            "UPDATE escalations SET status = ?, resolution_note = ?, updated_at = ? "
            "WHERE id = ?",
            (new_status, scrub_pii(resolution_note.strip()), _now_iso(), esc_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_escalation(ref, db_path=db_path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Day 8: call analytics ───────────────────────────────────────────────────

async def record_call_start(
    room_name: str = "",
    channel: str = "browser",
    db_path: str = "",
) -> int:
    """Insert an in_progress call row. Returns the new row id."""
    db_path = _ensure_db_dir(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        cursor = await db.execute(
            "INSERT INTO calls (room_name, channel, started_at, outcome) VALUES (?, ?, ?, 'in_progress')",
            (room_name or "", channel or "browser", now_iso),
        )
        await db.commit()
        return cursor.lastrowid or 0


async def record_call_end(
    call_id: int,
    outcome: str,
    outcome_reason: str = "",
    escalation_created: bool = False,
    user_turns: int = 0,
    agent_turns: int = 0,
    duration_secs: float = 0.0,
    db_path: str = "",
) -> None:
    """Update the call row with its final outcome. No-op if already finalised."""
    if not call_id:
        return
    valid = ("success", "failed", "no_answer")
    final = outcome if outcome in valid else "failed"
    db_path = _ensure_db_dir(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute(
            """
            UPDATE calls SET
                ended_at           = ?,
                duration_secs      = ?,
                outcome            = ?,
                outcome_reason     = ?,
                escalation_created = ?,
                user_turns         = ?,
                agent_turns        = ?
            WHERE id = ? AND outcome = 'in_progress'
            """,
            (
                now_iso,
                round(duration_secs, 1),
                final,
                outcome_reason or "",
                1 if escalation_created else 0,
                user_turns,
                agent_turns,
                call_id,
            ),
        )
        await db.commit()
    logger.info(
        "Call %d ended: outcome=%s reason=%s duration=%.1fs turns(u=%d a=%d)",
        call_id, final, outcome_reason, duration_secs, user_turns, agent_turns,
    )


async def get_call_stats(db_path: str = "") -> dict[str, Any]:
    """Aggregate counts for the analytics dashboard."""
    db_path = _ensure_db_dir(db_path)
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN outcome = 'success'     THEN 1 ELSE 0 END) AS successful,
                    SUM(CASE WHEN outcome = 'failed'      THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN outcome = 'no_answer'   THEN 1 ELSE 0 END) AS no_answer,
                    SUM(CASE WHEN outcome = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
                    ROUND(AVG(CASE WHEN duration_secs IS NOT NULL
                                    AND outcome NOT IN ('in_progress')
                                   THEN duration_secs END), 1) AS avg_duration_secs
                FROM calls
                """
            ) as cursor:
                row = await cursor.fetchone()
        total       = int(row["total"]       or 0)
        successful  = int(row["successful"]  or 0)
        failed      = int(row["failed"]      or 0)
        no_answer   = int(row["no_answer"]   or 0)
        in_progress = int(row["in_progress"] or 0)
        avg_dur     = float(row["avg_duration_secs"] or 0.0)
        completed   = total - in_progress
        success_rate = round((successful / completed * 100) if completed > 0 else 0)
        return {
            "total": total,
            "successful": successful,
            "failed": failed,
            "no_answer": no_answer,
            "in_progress": in_progress,
            "avg_duration_secs": avg_dur,
            "success_rate": success_rate,
        }
    except Exception as e:
        logger.warning("get_call_stats failed: %s", e)
        return {
            "total": 0, "successful": 0, "failed": 0,
            "no_answer": 0, "in_progress": 0,
            "avg_duration_secs": 0.0, "success_rate": 0,
        }


async def list_recent_calls(limit: int = 20, db_path: str = "") -> list[dict[str, Any]]:
    """Recent call records with no PII. Newest first."""
    db_path = _ensure_db_dir(db_path)
    try:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, room_name, channel, started_at, ended_at,
                       duration_secs, outcome, outcome_reason,
                       escalation_created, user_turns, agent_turns
                FROM calls ORDER BY started_at DESC LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("list_recent_calls failed: %s", e)
        return []
