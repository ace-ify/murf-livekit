import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    metrics,
    room_io,
    tokenize,
)
from livekit.agents.llm import FallbackAdapter
from livekit.plugins import (
    deepgram,
    google,
    groq,
    murf,
    noise_cancellation,
    openai,
    silero,
)
from livekit.plugins.turn_detector.multilingual import MultilingualModel

import db
import facilities
import rag
from outbound import place_call as _place_call

logger = logging.getLogger("agent")

load_dotenv(".env.local")

# Track: Health Access (#VoiceForBharat) — Day 5 Real Tools & Facility Locator
SYSTEM_PROMPT = """You are Careva, a female voice assistant for Health Centres in India.

LANGUAGE (check before every reply):
- Reply in the language of the caller's LAST message. Never switch on your own.
- English caller -> plain Indian English ONLY. Zero Hindi words: no "theek hai", "haan", "ji", "aap", "dawai", "aapka din shubh ho". Use "okay", "yes", "medicine".
- Hindi/Hinglish caller -> Hindi in Devanagari. Never mix both languages in one reply.

MEDICAL EMERGENCY (overrides everything else). Heart attack, chest pain, stroke, seizure, unconscious, not breathing, heavy bleeding, poisoning/overdose, suicidal talk:
1. FIRST sentence: call 108 now (or go to the nearest hospital). No greeting, no name, no consent question before it.
2. Then one line of what-to-do-now (keep them still, no food or water, keep them talking).
3. Then ask consent and call `create_escalation` with urgency="emergency".
4. NEVER `end_call`, never say goodbye or "aapka din shubh ho". Stay on the line.
5. Never say you can't do anything — you always have 108 and the nearest facility.

ROLE & OBJECTIVES:
1. Triage symptoms and guide callers when to visit the PHC or call 108.
2. Nearest facility or OPD timings -> `find_nearest_health_facility` (use the district/pincode from memory if known).
3. PHC care, vaccines, schemes (PM-JAY, JSSK) -> `search_health_guidelines`.
4. Cheap generic medicines or drug prices (Dolo, BP, Sugar, Acidity, Antibiotics) -> `lookup_generic_medicine`.
5. New caller shares name/district: ask permission to remember them. Call `save_caller_info` only AFTER they agree, never while still asking.
6. Asks to be forgotten -> `forget_caller`, then confirm deletion.
7. Outbound and they say stop / "abhi busy hoon" / don't call again -> `end_call` at once, no re-pitch. Never during an emergency.
8. Red flag (chest pain, breathing trouble, ongoing bleeding, fainting, fits, pregnancy complication, sick newborn, poisoning, suicidal talk, or worsening despite advice), or they ask you for a diagnosis, a dose, or permission to skip treatment: ask consent in THEIR language ("May I send a short summary to a human health worker?"), then `create_escalation`. If they refuse, call it with consent_given="false". 108 always comes before this question.
9. Asks about an existing case or reads back a ref like "ESC 0007" -> `check_escalation_status`.
10. Detailed facility questions (comparing multiple facilities, specific doctor schedules, detailed OPD timings, appointment/token registration procedures, directions/transport, PHC vs CHC differences) -> `transfer_to_clinic_specialist`.

GUARDRAILS & STYLE:
- Never prescribe specific medicines, doses, or give a final diagnosis.
- Never call `create_escalation` while still asking permission, or for routine questions (facility, timings, schemes, prices, a mild complaint already advised on).
- After escalating, say the reference number digit by digit and that a worker reviews it in working hours. Never promise a callback or a time.
- Escalation is not a substitute for 108.
- 1-2 short sentences per turn. Then stop and listen.
- Never output raw function tags or JSON in your dialogue."""

GREETING = (
    "Namaste, this is Careva from the health centre. "
    "Are you calling about yourself or for someone else?"
)


def _outbound_greeting(name: str, reason: str) -> str:
    """Who is calling, why, and how to stop it — in the first two sentences."""
    who = (
        f"Namaste{' ' + name + ' ji' if name else ''}, this is Careva, the automated "
        "health assistant from your local health centre."
    )
    why = (
        f" I'm calling about {reason}."
        if reason
        else " I'm calling for a quick health follow-up."
    )
    return (
        who
        + why
        + " If this is not a good time, just say stop and I will end the call and not call you again."
    )


# Murf Falcon voice: Anisha (Conversational female voice for Indian English / Hindi)
MURF_VOICE = "Anisha"

# Silent user handling
SILENCE_TIMEOUT = 12.0
SILENCE_RE_PROMPTS = [
    "Are you still there? Take your time — I'm listening.",
    "I'll close the call now. Please call us back whenever you're ready. Take care.",
]

FUNCTION_TAG_REGEX = re.compile(
    r"<function=[^>]*>.*?</function>", re.DOTALL | re.IGNORECASE
)
RAW_TAG_REGEX = re.compile(r"</?function[^>]*>", re.IGNORECASE)

# A red flag must not depend on the LLM noticing it. This scan runs on every final
# transcript and latches a flag that blocks end_call until 108 has been given and
# an escalation raised. Covers English, Roman Hinglish and Devanagari.
EMERGENCY_RE = re.compile(
    r"heart attack|cardiac|chest pain|stroke|seizure|convuls|unconscious|not breathing|"
    r"can'?t breathe|cannot breathe|breathing (trouble|problem|difficult)|choking|"
    r"heavy bleeding|bleeding a lot|overdose|poison|suicid|kill (myself|himself|herself)|"
    r"collapsed|fainted|dil ka daura|dil ka dora|seene? mein dard|chhaati mein dard|"
    r"saans nahi|saans lene|behosh|khoon bah|khoon nahi ruk|bahut khoon|zeher|jhatke aa|mirgi|"
    r"दिल का दौरा|सीने में दर्द|सांस नहीं|बेहोश|खून बह|ज़हर|दौरा",
    re.IGNORECASE,
)

# Broader scan for "is a human handover justified at all". The prompt says not to
# escalate routine complaints; llama-3.3 ignores it and files a case for a plain
# fever (measured). create_escalation refuses unless one of these appeared in the
# conversation, or the model itself flags urgency high/emergency.
# ponytail: word match, not triage. It errs open — any red-flag word, any request
# for a human, or high urgency is enough. A caller with a genuine but oddly worded
# red flag gets through the moment they ask for a doctor. Upgrade path if that
# proves too blunt: let the LLM justify in a `why_now` arg and judge that instead.
RED_FLAG_RE = re.compile(
    EMERGENCY_RE.pattern + "|"
    r"pregnan|garbh|labour pain|labor pain|newborn|new born|infant|navjat|nawjat|"
    r"getting worse|got worse|worse despite|bigad raha|badh raha|thik nahi ho raha|"
    r"blood in (stool|vomit|urine)|khoon aa raha|"
    r"talk to (a |an )?(doctor|human|health worker|nurse|someone)|speak to (a |an )?(doctor|human)|"
    r"doctor se baat|kisi se baat|insaan se baat|human se baat|"
    r"what (disease|illness) do|which medicine should|what dose|how many tablets|"
    r"kaunsi dawai|kitni goli|kitni dawa|dawai band|treatment band|skip (the )?(treatment|medicine)|"
    r"डॉक्टर से बात|कौन सी दवा|इलाज बंद",
    re.IGNORECASE,
)


class CleanSentenceTokenizer(tokenize.basic.SentenceTokenizer):
    """Safety tokenizer that cleans any leaked function/XML tags before audio synthesis."""

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        cleaned = FUNCTION_TAG_REGEX.sub("", text)
        cleaned = RAW_TAG_REGEX.sub("", cleaned).strip()
        if not cleaned:
            return []
        return super().tokenize(cleaned, language=language)


def _consent_yes(val: "str | bool") -> bool:
    """Fail-closed consent parse.

    The consent params are typed `str`, not `bool`, on purpose: Groq's llama-3.3
    intermittently emits `"consent_given": "true"` for a boolean param, which Groq
    rejects server-side with tool_use_failed and kills the whole turn. A string
    param can't be malformed. Anything we don't recognise means NO consent.
    """
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "yes", "haan", "haan ji", "1", "y")


def _clean_user_id(val: str) -> str:
    s = (val or "").strip()
    if (
        not s
        or s.lower() in ("default", "none", "null", "undefined")
        or "caller" in s.lower()
    ):
        return ""
    return s


# Day 7 — where an escalation gets pinged to a human. Optional: SQLite is the
# source of truth, the webhook is a best-effort notification.
ESCALATION_WEBHOOK_URL = os.getenv("ESCALATION_WEBHOOK_URL", "")
# When set, Careva calls this number the moment an escalation is confirmed so the
# human gets a real phone alert, not just a Discord ping.
ESCALATION_ADMIN_PHONE = os.getenv("ESCALATION_ADMIN_PHONE", "")


def _room_name(ctx: JobContext | None) -> str:
    try:
        return ctx.room.name or "web"  # type: ignore[union-attr]
    except Exception:
        return "web"


async def _post_escalation_webhook(rec: dict) -> None:
    """Best-effort Discord-compatible ping. Never fails the tool.

    Fields arrive already scrubbed by db.create_escalation, so raw PII cannot reach here.
    """
    if not ESCALATION_WEBHOOK_URL:
        return
    content = (
        f"**{rec['ref']}** · urgency **{rec['urgency']}** · lang {rec['language']}\n"
        f"Caller: {rec['caller_name'] or 'unknown'} ({rec['caller_user_id']})\n"
        f"What happened: {rec['what_happened']}\n"
        f"Agent already checked: {rec['already_checked'] or '-'}\n"
        f"Follow-up: {rec['followup_method'] or '-'}"
    )
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            # Discord rejects content over 2000 chars and would fail silently.
            await client.post(ESCALATION_WEBHOOK_URL, json={"content": content[:1900]})
    except Exception as e:
        logger.warning("escalation webhook failed for %s: %s", rec["ref"], e)


async def _call_admin(rec: dict) -> None:
    """Best-effort outbound call to the admin number when an escalation fires.

    Never raises — a failed call must not lose the escalation row already committed.
    Needs SIP_OUTBOUND_TRUNK_ID set and the agent running (same precondition as Day 6).
    """
    if not ESCALATION_ADMIN_PHONE:
        return
    try:
        name = rec.get("caller_name") or "a caller"
        urgency = rec.get("urgency", "medium")
        reason = (
            f"URGENT escalation {rec['ref']} (urgency: {urgency}) — "
            f"{name} needs human help: {(rec.get('what_happened') or '')[:80]}"
        )
        await _place_call(ESCALATION_ADMIN_PHONE, name="Health Worker", reason=reason)
        logger.info(
            "admin call dispatched for %s to %s", rec["ref"], ESCALATION_ADMIN_PHONE
        )
    except Exception as e:
        logger.warning("admin call failed for %s: %s", rec["ref"], e)


class Assistant(Agent):
    def __init__(self, caller_user_id: str = "") -> None:
        super().__init__(instructions=SYSTEM_PROMPT)
        self.caller_user_id = caller_user_id
        self.caller_facts: dict = {}
        self.job_ctx: JobContext | None = None
        # Day 8 analytics
        self.call_id: int = 0
        self.call_start_dt: datetime | None = None
        self.escalation_created_flag: bool = False
        self.user_turns: int = 0
        self.agent_turns: int = 0
        # Language mirroring + emergency latch (set from user_input_transcribed)
        self.tts_locale: str = "en-IN"
        self.emergency_flag: bool = False
        self.redflag_flag: bool = False

    @function_tool
    async def lookup_caller(
        self,
        context: RunContext,
        user_id: str = "",
        name: str = "",
    ) -> str:
        """Look up a caller's previous records, name, language preference, and health access facts.

        Use this tool when a caller mentions their name, phone number, or asks if you remember them.

        Args:
            user_id: The caller's phone number or identifier. If not provided, defaults to current session caller ID.
            name: The caller's name to search for if user_id is not known.
        """
        clean_id = _clean_user_id(user_id)
        target_id = clean_id or self.caller_user_id
        caller = None
        if target_id:
            caller = await db.get_caller(target_id)
        if not caller and name and name.lower() not in ("null", "none"):
            caller = await db.get_caller_by_name(name.strip())

        if caller:
            self.caller_facts = caller.get("facts") or {}
            logger.info(
                "lookup_caller found record for %s (%s)",
                caller.get("name"),
                caller.get("user_id"),
            )
            return db.format_caller_for_agent(caller)

        logger.info(
            "lookup_caller: No previous record found for id='%s', name='%s'",
            target_id,
            name,
        )
        return "No previous record found for this caller. Treat them as a new caller."

    @function_tool
    async def save_caller_info(
        self,
        context: RunContext,
        name: str,
        consent_given: str,
        user_id: str = "",
        language_preference: str = "hi",
        district: str = "",
        age_band: str = "",
        ongoing_conditions: str = "",
        last_triage_outcome: str = "",
    ) -> str:
        """Save or update caller information in the helpline database.

        CRITICAL HEALTH ACCESS RULE:
        You MUST ask for explicit permission before calling this tool.
        If the caller refuses or says no, consent_given MUST be "false".

        Args:
            name: Caller's name.
            consent_given: "true" ONLY if the caller explicitly agreed to have their info saved, else "false".
            user_id: Phone number or caller ID. If empty, defaults to current caller.
            language_preference: Preferred language ("hi", "en", or "hinglish").
            district: Caller's home district or town (e.g., "Varanasi", "Pune", "Patna").
            age_band: Age range (e.g., "30-40", "elderly", "child 5-10"). Do NOT store sensitive medical history.
            ongoing_conditions: Known general conditions mentioned (e.g., "hypertension, diabetes").
            last_triage_outcome: Short summary of triage advice given (e.g., "Advised routine PHC visit for fever").
        """
        if not _consent_yes(consent_given):
            logger.info("save_caller_info: Consent was denied. No data saved.")
            return (
                "Consent was NOT given. No caller information was saved. "
                "Reassure the caller that their privacy is respected."
            )

        clean_id = _clean_user_id(user_id)
        target_id = (
            clean_id
            or self.caller_user_id
            or f"caller_{name.lower().strip().replace(' ', '_')}"
        )
        facts = {}
        if district and district.lower() not in ("null", "none"):
            facts["district"] = district.strip()
        if age_band and age_band.lower() not in ("null", "none"):
            facts["age_band"] = age_band.strip()
        if ongoing_conditions and ongoing_conditions.lower() not in ("null", "none"):
            facts["ongoing_conditions"] = [
                c.strip()
                for c in ongoing_conditions.split(",")
                if c.strip() and c.strip().lower() not in ("null", "none")
            ]
        if last_triage_outcome and last_triage_outcome.lower() not in ("null", "none"):
            facts["last_triage_outcome"] = last_triage_outcome.strip()

        self.caller_facts.update(facts)

        saved = await db.save_caller(
            user_id=target_id,
            name=name,
            language_preference=language_preference,
            facts=facts,
            consent_given=True,
        )
        if saved:
            logger.info(
                "save_caller_info: Successfully saved data for %s (%s)",
                name,
                target_id,
            )
            return f"Successfully saved details for {name} with explicit consent."
        return "Failed to save caller information due to an internal error."

    @function_tool
    async def forget_caller(
        self,
        context: RunContext,
        user_id: str = "",
        name: str = "",
    ) -> str:
        """Delete all stored records and memory for the caller if they request to be forgotten.

        Args:
            user_id: Phone number or caller ID to erase. Defaults to current caller ID.
            name: Caller's name to erase if user_id is not known.
        """
        clean_id = _clean_user_id(user_id)
        target_id = clean_id or self.caller_user_id
        target_name = (
            name.strip() if name and name.lower() not in ("null", "none") else ""
        )
        deleted = await db.delete_caller(user_id=target_id, name=target_name)
        if not deleted and target_name:
            deleted = await db.delete_caller(name=target_name)
        if not deleted and self.caller_user_id:
            deleted = await db.delete_caller(user_id=self.caller_user_id)

        if deleted:
            self.caller_user_id = ""
            self.caller_facts.clear()
            logger.info(
                "forget_caller: Deleted record for user_id=%s, name=%s",
                target_id,
                target_name,
            )
            return "All personal records for this caller have been permanently deleted from the database."
        return "No record was found to delete."

    @function_tool
    async def end_call(self, context: RunContext, reason: str = "") -> str:
        """End the call politely. Call this when the caller says stop, asks not to be
        called again, says it's a bad time, or the conversation is finished.

        NEVER call this when the caller has described a medical emergency (heart attack,
        chest pain, breathing trouble, unconsciousness, heavy bleeding, poisoning,
        suicidal talk) unless they have been told to call 108 and an escalation exists.

        Args:
            reason: Short note on why the call is ending (e.g. "caller asked to stop").
        """
        # Hard guard: the LLM once said goodbye to a caller whose friend was having a
        # heart attack. A prompt line is not enough — refuse the hangup outright.
        if self.emergency_flag and not self.escalation_created_flag:
            logger.warning("end_call BLOCKED: unhandled emergency (reason=%r)", reason)
            return (
                "REFUSED — you cannot end this call. The caller reported a medical "
                "emergency. Right now, in their language: tell them to call 108 "
                "immediately (or go to the nearest hospital), then ask permission to "
                "send a summary to a human health worker and call create_escalation."
            )

        logger.info("end_call: %s", reason or "conversation complete")
        goodbye = (
            "Okay, I'm ending the call now. Take care."
            if self.tts_locale.startswith("en")
            else "Theek hai, main call rakh rahi hoon. Aapka din shubh ho."
        )
        await context.session.say(goodbye).wait_for_playout()
        context.session.shutdown()
        return "Call ended."

    async def _publish_escalation_card(self, rec: dict) -> None:
        """Push the escalation to the UI, same data-channel path as the facility card."""
        if not (
            self.job_ctx and self.job_ctx.room and self.job_ctx.room.local_participant
        ):
            return
        try:
            payload = json.dumps({"type": "escalation_card", **rec}).encode("utf-8")
            await self.job_ctx.room.local_participant.publish_data(
                payload, topic="escalation_card"
            )
        except Exception as e:
            logger.warning("Failed to publish escalation_card to room: %s", e)

    @function_tool
    async def create_escalation(
        self,
        context: RunContext,
        consent_given: str,
        what_happened: str,
        urgency: str = "medium",
        already_checked: str = "",
        followup_method: str = "",
        caller_name: str = "",
    ) -> str:
        """Hand this caller over to a human health worker and give them a reference number.

        Call this ONLY for one of these two situations:
        1. A red-flag or emergency-like clinical situation needing a human clinician
           (chest pain, breathing difficulty, severe or continuing bleeding, fainting or
           unconsciousness, fits, a pregnancy complication, a sick newborn, poisoning or
           overdose, suicidal talk, or a symptom getting worse despite advice).
        2. The caller asks for something you must not decide: a diagnosis, whether to
           start or stop a medicine, a dose, or whether they may skip treatment or
           hospital care.

        CRITICAL CONSENT RULE (same as save_caller_info):
        You MUST first ask the caller for permission to share a short summary with a
        human health worker, and only call this tool after they clearly agree. If they
        refuse, call it with consent_given="false" — nothing will be shared.
        Do NOT call this for ordinary helpline questions (facility location, OPD timings,
        scheme eligibility, medicine prices, or a mild complaint you already advised on).

        Args:
            consent_given: "true" ONLY if the caller explicitly agreed to share the summary, else "false".
            what_happened: 1-2 sentences on who this is and what is wrong. Do not include
                phone numbers, OTPs, Aadhaar or account numbers.
            urgency: "low", "medium", "high", or "emergency".
            already_checked: What you already did or advised (e.g. "advised 108, gave
                nearest CHC, checked Jan Aushadhi rate").
            followup_method: How they want to be reached (e.g. "call back on this number",
                "SMS", "will visit the PHC").
            caller_name: Caller's name if known.
        """
        # Routine-complaint guard. Measured: llama-3.3 files a case for "my friend
        # has fever" on the very first turn. Prompt rules alone don't hold, so the
        # tool checks whether anything in the conversation actually warrants a human.
        # Anything the model marks high/emergency passes — fail open on the side of
        # a real emergency getting through.
        if urgency.lower() not in ("high", "emergency") and not (
            self.redflag_flag or self.emergency_flag
        ):
            logger.info(
                "create_escalation REFUSED as routine (urgency=%s, what=%r)",
                urgency,
                what_happened[:80],
            )
            return (
                "NOT escalated — this is a routine helpline question and no red flag "
                "has been mentioned. Do not tell the caller anything was escalated. "
                "Give simple self-care advice, say when to visit the PHC, and offer to "
                "find the nearest facility. Escalate only if they describe a red flag, "
                "ask to speak to a human, or say it is getting worse."
            )

        if not _consent_yes(consent_given):
            logger.info("create_escalation: consent denied, nothing shared.")
            return (
                "Consent was NOT given. Nothing was shared with a human worker. "
                "Reassure the caller their privacy is respected, and if this is an "
                "emergency tell them to call 108."
            )

        target_id = self.caller_user_id or f"anon-{_room_name(self.job_ctx)}"
        rec = await db.create_escalation(
            caller_user_id=target_id,
            what_happened=what_happened,
            urgency=urgency,
            caller_name=caller_name or self.caller_facts.get("name", ""),
            language=self.caller_facts.get("language_preference", "hi"),
            already_checked=already_checked,
            followup_method=followup_method,
            callback_phone=target_id if target_id.startswith("+") else "",
        )
        if not rec:
            return (
                "Could not create the escalation. Tell the caller to call 108 if this is "
                "an emergency, or to visit the nearest PHC."
            )

        self.escalation_created_flag = True
        await self._publish_escalation_card(rec)
        await _post_escalation_webhook(rec)
        await _call_admin(rec)

        return (
            f"Escalation {rec['ref']} recorded (urgency: {rec['urgency']}"
            f"{', updated the existing case' if rec['deduped'] else ''}). "
            f"Say the reference number {rec['ref']} out loud, digit by digit. Say a health "
            "worker will review it during working hours — do NOT promise an immediate "
            "call. If urgency is high or emergency, repeat that they should call 108 or go "
            "to the nearest facility now, without waiting for us."
        )

    @function_tool
    async def check_escalation_status(self, context: RunContext, ref: str = "") -> str:
        """Check what happened to an escalation the caller has a reference number for.

        Use when the caller says "mere case ka kya hua", "koi update hai?", or reads back
        a reference number like "ESC 0007".

        Args:
            ref: The reference as the caller said it ("ESC-0007", "esc 7"). If empty, uses
                the caller's most recent case.
        """
        if ref.strip():
            rec = await db.get_escalation(ref, caller_user_id=self.caller_user_id)
        else:
            rows = await db.list_escalations(status="")
            rec = next(
                (r for r in rows if r["caller_user_id"] == self.caller_user_id), None
            )
        if not rec:
            return (
                "No escalation found for this caller with that reference. Ask them to "
                "repeat the number, or offer to raise a fresh escalation."
            )
        return (
            f"{rec['ref']}: status {rec['status']}, urgency {rec['urgency']}, raised "
            f"{rec['created_at']}. Note: {rec['resolution_note'] or 'none yet'}. "
            "Tell the caller the status plainly. Do not promise a time."
        )

    @function_tool
    async def find_nearest_health_facility(
        self,
        context: RunContext,
        location_or_pincode: str = "",
        facility_type: str = "any",
    ) -> str:
        """Find the nearest Primary Health Centre (PHC), Community Health Centre (CHC), or Hospital.

        Use this tool when a caller asks:
        - "Mera paas ka PHC/hospital kahan hai?" or "Where is the nearest health centre?"
        - "OPD kitne baje tak khula hai?" or "What are the hospital timings?"
        - "Emergency mein kahan jayein?" or "Which facility is open 24x7?"
        - "Where can I get free blood tests, fever tests, or delivery care nearby?"

        Args:
            location_or_pincode: District name, city name, area, or 6-digit Indian PIN code. If empty, automatically checks caller memory.
            facility_type: Filter by "PHC", "CHC", "District Hospital", or "any".
        """
        loc = (location_or_pincode or "").strip()
        if not loc:
            # Tool chaining: auto-resolve from Day 4 memory
            loc = (
                self.caller_facts.get("district")
                or self.caller_facts.get("location")
                or ""
            )

        logger.info(
            "find_nearest_health_facility live query loc='%s', type='%s'",
            loc,
            facility_type,
        )
        res = await facilities.find_health_facilities_async(
            loc, facility_type=facility_type
        )

        # Advanced Day 5 Extra: Push results to UI via LiveKit Data Channel
        if (
            self.job_ctx
            and self.job_ctx.room
            and self.job_ctx.room.local_participant
            and res.get("primary_facility")
        ):
            try:
                payload = json.dumps(
                    {
                        "type": "facility_card",
                        "facility": res["primary_facility"],
                        "all_facilities": res.get("facilities", []),
                        "timestamp": res.get("verified_timestamp", "Live Public API"),
                    }
                ).encode("utf-8")
                await self.job_ctx.room.local_participant.publish_data(
                    payload,
                    topic="facility_card",
                )
            except Exception as e:
                logger.warning("Failed to publish facility_card to room: %s", e)

        return res["spoken_summary"]

    @function_tool
    async def search_health_guidelines(
        self,
        context: RunContext,
        query: str,
    ) -> str:
        """Search official government health scheme documents, benefits, eligibility, vaccination schedules, and PHC services.

        Use this tool when a caller asks about:
        - Ayushman Bharat / PM-JAY coverage (up to 5 Lakhs), eligibility, 14555 helpline.
        - Janani Shishu Suraksha Karyakram (JSSK) free delivery, newborn care, free hospital transport.
        - Universal Immunization Programme (UIP) / vaccine schedule for infants and children (birth, 6/10/14 weeks, 9 months).
        - Primary Health Centre (PHC) standard services, OPD hours, free diagnostics and medicines.

        Args:
            query: The specific health scheme, vaccination, or PHC service question to look up.
        """
        logger.info("search_health_guidelines query='%s'", query)
        results = rag.search_health_rag(query, top_k=2)
        return results

    @function_tool
    async def lookup_generic_medicine(
        self,
        context: RunContext,
        medicine_or_condition: str,
    ) -> str:
        """Lookup PMBJP Jan Aushadhi generic medicine rates, active salts, and up to 80% cost savings compared to branded market medicines.

        Use this tool when a caller asks:
        - "Dolo 650 ya Paracetamol ka sasta generic rate kya hai?"
        - "Sugar / Diabetes / BP ki dawaiyan sasti kahan milengi?"
        - "Jan Aushadhi kendra par kitna discount milta hai?"
        - "What is the generic alternative for Pan 40 / Augmentin / Telma?"

        Args:
            medicine_or_condition: Medicine brand name, generic salt name, or medical condition (e.g. "Dolo", "Sugar", "BP", "Telmisartan", "Acidity").
        """
        import health_mcp_server

        logger.info(
            "lookup_generic_medicine query: '%s'",
            medicine_or_condition,
        )
        return await health_mcp_server.lookup_generic_medicine(medicine_or_condition)

    @function_tool
    async def get_district_health_advisory(
        self,
        context: RunContext,
        district_or_city: str = "",
    ) -> str:
        """Query real-time Open-Meteo Air Quality & Respiratory Health API for any Indian district.

        Use this tool when a caller asks:
        - "Varanasi / Pune / Lucknow mein aaj hawa (AQI) aur mausam kaisa hai?"
        - "Asthma ya sans ke mareezon ke liye aaj koi health precaution ya alert hai?"

        Args:
            district_or_city: District or city name in India. If empty, uses caller memory district.
        """
        import health_mcp_server

        loc = (district_or_city or "").strip()
        if not loc:
            loc = self.caller_facts.get("district") or "India"

        logger.info("get_district_health_advisory query: '%s'", loc)
        return await health_mcp_server.get_district_health_advisory(loc)

    @function_tool
    async def transfer_to_clinic_specialist(
        self,
        context: RunContext,
        reason: str,
    ) -> str:
        """Hand the conversation to the Clinic and Appointment Specialist.

        Call this ONLY when the caller asks about:
        - Finding or comparing multiple health facilities
        - Detailed OPD timings, doctor schedules, or appointment procedures
        - Which facility to visit for a specific service (e.g. X-ray, ultrasound, specialist)
        - Directions, transport, or how to reach a facility
        - Differences between PHC, CHC, and District Hospitals

        Do NOT call this for:
        - Simple "where is the nearest PHC" (use find_nearest_health_facility)
        - Medicine prices (use lookup_generic_medicine)
        - Scheme eligibility (use search_health_guidelines)
        - General triage or emergency advice (handle it yourself)

        Args:
            reason: What the caller needs specialist help with (1 sentence).
        """
        import handoff

        logger.info("transfer_to_clinic_specialist called: %s", reason)
        return await handoff.transfer_to_specialist(
            current_agent=self,
            context=context,
            reason=reason,
            specialist_type="clinic",
        )


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
        # Gemini 2.5 Flash primary, then Groq llama-3.3-70b, then llama-3.1-70b
        # on NVIDIA NIM. Two llama fallbacks are the same family, so voice and
        # Hinglish quality don't change on a Groq failover.
        # ponytail: OpenAI-compatible endpoint, so the openai plugin covers NIM;
        # no separate nvidia plugin needed.
        # NIM's meta/llama-3.3-70b-instruct is listed but never responds
        # (measured: read timeout at 45s+), which is why the last fallback used
        # to be dead weight. 3.1-70b measures 0.8s TTFT with working tool calls.
        llm=FallbackAdapter(
            [
                google.LLM(
                    model="gemini-2.5-flash",
                    temperature=0.2,
                    # 2.5-flash thinks by default, which lands before the first
                    # token and is dead air on a phone call. 0 = off.
                    thinking_config={"thinking_budget": 0},
                ),
                groq.LLM(
                    model="llama-3.3-70b-versatile",
                    # Default temperature makes llama freestyle its tool calls
                    # (see _consent_yes). 0.2 also matches the other two, so a
                    # failover doesn't change how blunt the triage advice sounds.
                    temperature=0.2,
                    parallel_tool_calls=False,
                ),
                openai.LLM(
                    model="meta/llama-3.1-70b-instruct",
                    base_url="https://integrate.api.nvidia.com/v1",
                    api_key=os.getenv("NVIDIA_API_KEY"),
                    temperature=0.2,
                ),
            ],
            attempt_timeout=15.0,
        ),
        # Text-to-speech (TTS) is your agent's voice, turning the LLM's text into speech that the user can hear
        # See all available models as well as voice selections at https://docs.livekit.io/agents/models/tts/
        tts=murf.TTS(
            voice=MURF_VOICE,
            style="Conversational",
            tokenizer=CleanSentenceTokenizer(min_sentence_len=4),
            text_pacing=True,
        ),
        # VAD and turn detection are used to determine when the user is speaking and when the agent should respond
        # See more at https://docs.livekit.io/agents/build/turns
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        # allow the LLM to generate a response while waiting for the end of turn
        # See more at https://docs.livekit.io/agents/build/audio/#preemptive-generation
        preemptive_generation=True,
        # Fires "user_state_changed" -> "away" once user AND agent are both
        # silent for this long. Drives the silent-caller handling below.
        user_away_timeout=SILENCE_TIMEOUT,
    )

    # ── Silent caller handling ───────────────────────────────────────────────
    # Follow LiveKit's documented cancellable-task pattern: start one task on
    # "away", re-prompt once, wait for a response, then close. Voice activity
    # cancels via user_state_changed. Typed chat uses the RoomIO text callback
    # below because text input does not change the audio user state.
    inactivity_task: asyncio.Task[None] | None = None

    def _cancel_inactivity() -> None:
        nonlocal inactivity_task

        if inactivity_task is not None and not inactivity_task.done():
            inactivity_task.cancel()
        inactivity_task = None

    async def _handle_inactivity() -> None:
        logger.info("SILENCE re-prompt after %.0fs idle", SILENCE_TIMEOUT)
        try:
            await session.say(
                SILENCE_RE_PROMPTS[0], allow_interruptions=True
            ).wait_for_playout()
            await asyncio.sleep(SILENCE_TIMEOUT)

            logger.info("SILENCE no response after re-prompt, closing call")
            await session.say(
                SILENCE_RE_PROMPTS[1], allow_interruptions=True
            ).wait_for_playout()
            session.shutdown()
        except asyncio.CancelledError:
            logger.info("SILENCE caller returned, cancelling close")
            raise

    @session.on("user_state_changed")
    def _on_user_state_changed(ev) -> None:
        nonlocal inactivity_task

        if ev.new_state == "away":
            if inactivity_task is None or inactivity_task.done():
                inactivity_task = asyncio.create_task(_handle_inactivity())
            return

        _cancel_inactivity()

    async def _on_text_input(sess, ev) -> None:
        # LiveKit's default callback is interrupt() + generate_reply(). Add only
        # the missing inactivity cancellation before preserving that behavior.
        _cancel_inactivity()
        await sess.interrupt()
        sess.generate_reply(user_input=ev.text)

    # ── Latency logging (Day 1 optional extra) ───────────────────────────────
    # Components arrive as separate metrics events, so accumulate then log.
    # eou is None whenever LiveKit judges VAD unreliable, so it prints "n/a"
    # rather than 0 — a 0 would read as "no delay" when it means "unknown".
    turn: dict = {}

    @session.on("metrics_collected")
    def _on_metrics(ev) -> None:
        m = ev.metrics
        if isinstance(m, metrics.EOUMetrics):
            turn["eou"] = m.end_of_utterance_delay or None
        elif isinstance(m, metrics.LLMMetrics):
            turn["llm"] = m.ttft
        elif isinstance(m, metrics.TTSMetrics):
            eou = turn.get("eou")
            llm = turn.get("llm", 0.0)
            logger.info(
                "LATENCY reply=%.0fms (llm_ttft=%.0f + tts_ttfb=%.0f), turn_detect=%s",
                (llm + m.ttfb) * 1000,
                llm * 1000,
                m.ttfb * 1000,
                f"{eou * 1000:.0f}ms" if eou else "n/a",
            )
            turn.clear()

    assistant = Assistant()  # caller_user_id set after ctx.connect()
    assistant.job_ctx = ctx

    # ── Language mirroring + emergency latch ────────────────────────────────
    # Deepgram multi tags every final transcript with the language actually spoken.
    # Without switching the Murf locale, English text was synthesised through the
    # hi-IN voice — that is the bad accent. Locale fixes accent; the prompt fixes
    # word choice. Same handler latches red flags so end_call can refuse.
    def _set_locale(code: str) -> None:
        locale = "en-IN" if code.lower().startswith("en") else "hi-IN"
        if locale == assistant.tts_locale:
            return
        assistant.tts_locale = locale
        try:
            session.tts.update_options(locale=locale)
            logger.info("LANG TTS locale -> %s (stt=%s)", locale, code)
        except Exception as e:
            logger.warning("LANG locale switch failed: %s", e)

    @session.on("user_input_transcribed")
    def _on_transcribed(ev) -> None:
        if not ev.is_final:
            return
        if ev.language:
            _set_locale(ev.language)
        text = ev.transcript or ""
        if not assistant.emergency_flag and EMERGENCY_RE.search(text):
            assistant.emergency_flag = True
            logger.warning("EMERGENCY detected in transcript: %r", text[:120])
        if not assistant.redflag_flag and RED_FLAG_RE.search(text):
            assistant.redflag_flag = True
            logger.info("RED FLAG detected, escalation unlocked: %r", text[:120])

    # Start the session, which initializes the voice pipeline and warms up the models
    await session.start(
        agent=assistant,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            # A helpline "close call" should end the room for the caller too,
            # not merely disconnect the agent participant.
            delete_room_on_close=True,
            text_input=room_io.TextInputOptions(text_input_cb=_on_text_input),
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

    # Parse dispatch metadata before anything reads it. It used to be parsed down
    # in the outbound block, below record_call_start() — which made that call raise
    # NameError, get swallowed, leave call_id=0, and silently record zero calls.
    dial_info = {}
    if ctx.job.metadata:
        try:
            dial_info = json.loads(ctx.job.metadata)
        except json.JSONDecodeError:
            logger.warning("ignoring non-JSON job metadata: %r", ctx.job.metadata)

    # Join the room and connect to the user, initializing DB concurrently
    db_init_task = asyncio.create_task(db.init_db())
    await ctx.connect()
    await db_init_task

    # Day 8: record call start and set up turn counting / close hook
    call_start_dt = datetime.now(timezone.utc)
    call_id = 0
    try:
        call_id = await db.record_call_start(
            room_name=ctx.room.name,
            channel="sip" if dial_info.get("phone") else "browser",
        )
        assistant.call_id = call_id
        assistant.call_start_dt = call_start_dt
    except Exception as _e:
        logger.warning("record_call_start failed: %s", _e)

    # Turn counting. livekit-agents 1.4 has no user_speech_committed /
    # agent_speech_committed events — those handlers never fired, so every call
    # looked like a silent disconnect. conversation_item_added is the real event.
    @session.on("conversation_item_added")
    def _on_conversation_item(ev) -> None:
        role = getattr(ev.item, "role", "")
        if role == "user":
            assistant.user_turns += 1
        elif role == "assistant":
            assistant.agent_turns += 1

    finalized = False

    async def _finalize_call(override_reason: str = "") -> None:
        nonlocal finalized
        if not call_id or finalized:
            return
        finalized = True
        elapsed = (datetime.now(timezone.utc) - call_start_dt).total_seconds()
        if assistant.escalation_created_flag:
            outcome, reason = "success", "escalation_created"
        elif assistant.user_turns >= 2 and assistant.agent_turns >= 2:
            outcome, reason = "success", "conversation_completed"
        elif assistant.user_turns == 0:
            outcome, reason = "no_answer", override_reason or "silent_disconnect"
        else:
            outcome, reason = "failed", override_reason or "user_declined_early"
        try:
            await db.record_call_end(
                call_id=call_id,
                outcome=outcome,
                outcome_reason=reason,
                escalation_created=assistant.escalation_created_flag,
                user_turns=assistant.user_turns,
                agent_turns=assistant.agent_turns,
                duration_secs=elapsed,
            )
        except Exception as _e:
            logger.warning("record_call_end failed: %s", _e)

    # Finalize on job shutdown, not on the session "close" event: an
    # ensure_future there loses the race with the loop closing, which left rows
    # stuck at outcome='in_progress'. LiveKit awaits shutdown callbacks.
    async def _on_shutdown() -> None:
        await _finalize_call()

    ctx.add_shutdown_callback(_on_shutdown)

    # ── Day 6: outbound call ────────────────────────────────────────────────
    # Dispatch metadata (see src/outbound.py) means "call this number", so dial
    # out before greeting. wait_until_answered lets LiveKit surface the real SIP
    # outcome — no answer / busy / declined — as a TwirpError instead of us
    # timing anything ourselves. dial_info is parsed at the top of this function.
    phone = dial_info.get("phone", "")
    if phone:
        logger.info("OUTBOUND dialing %s (reason=%r)", phone, dial_info.get("reason"))
        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=os.environ["SIP_OUTBOUND_TRUNK_ID"],
                    sip_call_to=phone,
                    participant_identity=phone,
                    participant_name=dial_info.get("name") or "Caller",
                    wait_until_answered=True,
                    krisp_enabled=True,
                )
            )
            logger.info("OUTBOUND answered by %s", phone)
        except api.TwirpError as e:
            # ponytail: no retry loop — re-run src/outbound.py to redial.
            logger.warning(
                "OUTBOUND not connected to %s: %s (sip_status=%s)",
                phone,
                e.message,
                e.metadata.get("sip_status"),
            )
            await _finalize_call(override_reason="outbound_not_answered")
            ctx.shutdown(reason="outbound call not answered")
            return

    # Determine caller ID from connected room participant (must be after ctx.connect())
    caller_id = ""
    for p in ctx.room.remote_participants.values():
        if p.identity:
            caller_id = p.identity
            break

    if not caller_id:
        try:
            remote_p = await ctx.wait_for_participant(timeout=2.0)
            if remote_p and remote_p.identity:
                caller_id = remote_p.identity
        except Exception:
            pass

    assistant.caller_user_id = caller_id
    caller_record = await db.get_caller(caller_id) if caller_id else None
    if caller_record:
        assistant.caller_facts = caller_record.get("facts") or {}

    # Step 4: Greet returning callers by name and context, or default greeting for new callers
    if phone:
        greeting = _outbound_greeting(
            dial_info.get("name") or (caller_record or {}).get("name", ""),
            dial_info.get("reason", ""),
        )
    elif caller_record:
        name = caller_record.get("name", "")
        lang = caller_record.get("language_preference", "hi")
        facts = caller_record.get("facts", {})
        last_outcome = facts.get("last_triage_outcome", "") or (
            ", ".join(facts.get("ongoing_conditions", []))
            if facts.get("ongoing_conditions")
            else ""
        )

        if lang == "en":
            if last_outcome:
                greeting = f"Namaste {name}, welcome back to the health centre. Last time we spoke about {last_outcome}. How are you feeling today?"
            else:
                greeting = f"Namaste {name}, welcome back to the health centre. How can I assist you today?"
        else:
            if last_outcome:
                greeting = f"नमस्ते {name} जी, स्वास्थ्य केंद्र में आपका फिर से स्वागत है। पिछली बार हमने {last_outcome} के बारे में बात की थी। आज आप कैसा महसूस कर रहे हैं?"
            else:
                greeting = f"नमस्ते {name} जी, स्वास्थ्य केंद्र में आपका फिर से स्वागत है। आज मैं आपकी क्या सहायता कर सकती हूँ?"
    else:
        greeting = GREETING

    # Match the voice locale to the greeting we are about to speak.
    _set_locale("hi" if re.search(r"[ऀ-ॿ]", greeting) else "en")

    # Greet only after the caller is actually subscribed, so the opening line
    # isn't played to an empty room.
    await session.say(greeting, allow_interruptions=True)


if __name__ == "__main__":
    cli.run_app(server)
