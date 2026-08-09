import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger("db")

DEFAULT_DB_PATH = os.getenv(
    "HELPLINE_DB_PATH",
    str(Path(__file__).parent.parent / "data" / "helpline.db"),
)


def _ensure_db_dir(db_path: str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)


async def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Initialize database and create tables if they do not exist."""
    _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as db:
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
        await db.commit()
    logger.info("Database initialized at %s", db_path)


async def get_caller(
    user_id: str, db_path: str = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    """Fetch caller record by user_id."""
    if not user_id:
        return None
    _ensure_db_dir(db_path)
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


async def get_caller_by_name(
    name: str, db_path: str = DEFAULT_DB_PATH
) -> dict[str, Any] | None:
    """Fetch caller record by name (case-insensitive lookup)."""
    if not name or not name.strip():
        return None
    _ensure_db_dir(db_path)
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
    db_path: str = DEFAULT_DB_PATH,
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

    _ensure_db_dir(db_path)
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


async def delete_caller(user_id: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Delete caller record (forget me feature)."""
    if not user_id:
        return False
    _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("DELETE FROM callers WHERE user_id = ?", (user_id,))
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
