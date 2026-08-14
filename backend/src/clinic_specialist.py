from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from livekit.agents import Agent, RunContext, function_tool

import db
import facilities

if TYPE_CHECKING:
    from datetime import datetime

    from livekit.agents import JobContext

logger = logging.getLogger("clinic_specialist")

CLINIC_SPECIALIST_PROMPT = """You are Samar, the male Clinic and Appointment Specialist for Health Centres in India.
You specialize in detailed health facility information, doctor availability, OPD schedules, clinic procedures, and navigation.

LANGUAGE (check before every reply):
- Reply in the language of the caller's LAST message. Never switch on your own.
- English caller -> plain Indian English ONLY. Zero Hindi words. Use "okay", "yes", "clinic", "doctor".
- Hindi/Hinglish caller -> Hindi in Devanagari. Never mix both languages in one reply.

FACILITY KNOWLEDGE & GUIDANCE:
1. Primary Health Centres (PHC): OPD 8:00 AM - 2:00 PM (or 9 AM - 4 PM). Free essential medicines, rapid tests (malaria, CBC), 24x7 normal delivery (JSSK), child immunization. Walk-in token system (arrive early morning).
2. Community Health Centres (CHC): 30-bed inpatient, 24x7 emergency & maternity, specialists (General Physician, Gynecologist, Pediatrician, Surgeon), X-ray, lab diagnostics.
3. District Hospitals: Comprehensive multi-specialty care, trauma unit, ICU, advanced pathology and diagnostics.
4. Appointment Process: Government PHCs and CHCs operate on walk-in token systems. Advise callers to carry Aadhaar/identity card, ration card, and previous medical slips, and to arrive between 8:00 AM and 9:00 AM for OPD registration.

TOOLS & ROUTING:
- Facility lookup or comparison -> `find_nearest_health_facility`.
- Detailed timings, doctor schedules, departments -> `get_facility_details_and_timings`.
- Questions outside facility guidance (medicine rates/discounts, health scheme eligibility, general triage/prescriptions) or when the caller says they are done with facility questions -> `transfer_back_to_main_agent`.
- Caller asks to end the call -> `end_call`.

GUARDRAILS:
- Never give medical diagnoses, prescriptions, or dosages.
- 1-2 short sentences per turn. Stop and listen.
- Never output raw function tags or JSON."""


class ClinicSpecialist(Agent):
    def __init__(
        self,
        caller_user_id: str = "",
        caller_facts: dict | None = None,
        job_ctx: JobContext | None = None,
        tts_locale: str = "en-IN",
        emergency_flag: bool = False,
        redflag_flag: bool = False,
        call_id: int = 0,
        call_start_dt: datetime | None = None,
        user_turns: int = 0,
        agent_turns: int = 0,
        escalation_created_flag: bool = False,
    ) -> None:
        super().__init__(instructions=CLINIC_SPECIALIST_PROMPT)
        self.caller_user_id = caller_user_id
        self.caller_facts = dict(caller_facts or {})
        self.job_ctx = job_ctx
        self.tts_locale = tts_locale
        self.emergency_flag = emergency_flag
        self.redflag_flag = redflag_flag
        self.call_id = call_id
        self.call_start_dt = call_start_dt
        self.user_turns = user_turns
        self.agent_turns = agent_turns
        self.escalation_created_flag = escalation_created_flag

    @function_tool
    async def get_facility_details_and_timings(
        self,
        context: RunContext,
        facility_name_or_location: str,
        specific_requirement: str = "general",
    ) -> str:
        """Look up in-depth OPD timings, doctor schedules, available departments, walk-in token procedures, and transport directions.

        Use this tool when a caller asks:
        - "What are the exact OPD timings or doctor days at the clinic?"
        - "Do I need an appointment or is it walk-in?"
        - "Which days is the gynecologist or pediatrician available?"
        - "Can I get an X-ray or ultrasound done at this facility?"
        - "How do I reach the health centre from the station or bus stand?"

        Args:
            facility_name_or_location: Name of the facility or area (e.g. "Shivpur PHC", "Cholapur CHC", "Varanasi").
            specific_requirement: Specific service or question (e.g. "OPD timings", "X-ray", "appointment procedure", "doctors", "directions").
        """
        loc = (
            facility_name_or_location
            or self.caller_facts.get("district")
            or self.caller_facts.get("location")
            or "Varanasi"
        )
        logger.info(
            "get_facility_details_and_timings query='%s', req='%s'",
            loc,
            specific_requirement,
        )

        res = await facilities.find_health_facilities_async(loc, facility_type="any")
        primary = res.get("primary_facility")

        if not primary:
            return (
                f"No specific records found for '{facility_name_or_location}'. "
                "Generally, government PHCs are open 8:00 AM to 2:00 PM Monday through Saturday "
                "with walk-in registration. Emergency services are available 24x7 at CHCs and District Hospitals."
            )

        # Publish facility card if in room
        if self.job_ctx and self.job_ctx.room and self.job_ctx.room.local_participant:
            try:
                payload = json.dumps(
                    {
                        "type": "facility_card",
                        "facility": primary,
                        "all_facilities": res.get("facilities", []),
                        "timestamp": res.get("verified_timestamp", "Live Public API"),
                    }
                ).encode("utf-8")
                await self.job_ctx.room.local_participant.publish_data(
                    payload, topic="facility_card"
                )
            except Exception as e:
                logger.warning("Failed to publish facility_card: %s", e)

        doctors = ", ".join(
            primary.get("available_doctors", ["General Medical Officer"])
        )
        services = ", ".join(
            primary.get("free_services", ["General OPD", "Essential Medicines"])
        )
        timings = primary.get("opd_timings", "8:00 AM - 2:00 PM (Mon-Sat)")
        address = primary.get("address", "")
        contact = primary.get("contact_number", "108")
        emergency = (
            "24x7 Emergency available"
            if primary.get("emergency_24x7")
            else "OPD hours only"
        )

        details = (
            f"Facility: {primary.get('name')}\n"
            f"Address: {address}\n"
            f"OPD Timings: {timings}\n"
            f"Emergency: {emergency}\n"
            f"Available Doctors/Staff: {doctors}\n"
            f"Services & Facilities: {services}\n"
            f"Registration/Token Process: Free walk-in registration at the OPD counter. Bring Aadhaar card or ID.\n"
            f"Contact: {contact}"
        )
        return details

    @function_tool
    async def find_nearest_health_facility(
        self,
        context: RunContext,
        location_or_pincode: str = "",
        facility_type: str = "any",
    ) -> str:
        """Find nearest PHC, CHC, or Hospital and compare facilities for specific services.

        Args:
            location_or_pincode: District, city, area or PIN code.
            facility_type: Filter by 'PHC', 'CHC', 'District Hospital', or 'any'.
        """
        loc = (location_or_pincode or "").strip()
        if not loc:
            loc = (
                self.caller_facts.get("district")
                or self.caller_facts.get("location")
                or ""
            )

        res = await facilities.find_health_facilities_async(
            loc, facility_type=facility_type
        )

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
                    payload, topic="facility_card"
                )
            except Exception as e:
                logger.warning("Failed to publish facility_card: %s", e)

        return res.get("spoken_summary", "")

    @function_tool
    async def lookup_caller(
        self,
        context: RunContext,
        user_id: str = "",
        name: str = "",
    ) -> str:
        """Look up caller's history, district, and previously saved preferences.

        Args:
            user_id: Caller's phone number or ID.
            name: Caller's name.
        """
        target_id = user_id.strip() or self.caller_user_id
        caller = None
        if target_id:
            caller = await db.get_caller(target_id)
        if not caller and name and name.lower() not in ("null", "none"):
            caller = await db.get_caller_by_name(name.strip())

        if caller:
            self.caller_facts = caller.get("facts") or {}
            return db.format_caller_for_agent(caller)

        return "No previous record found for this caller."

    @function_tool
    async def transfer_back_to_main_agent(
        self,
        context: RunContext,
        reason: str = "Facility questions completed",
    ) -> str:
        """Transfer the conversation back to Careva (main health assistant).

        Call this when:
        - The caller has finished their facility/OPD/appointment questions.
        - The caller asks about medicine prices, schemes, or general triage.
        - The caller wants to talk about other general health topics.

        Args:
            reason: Short note on why returning to the main agent.
        """
        import handoff

        logger.info("transfer_back_to_main_agent called: %s", reason)
        return await handoff.transfer_to_main(
            specialist_agent=self,
            context=context,
            reason=reason,
        )

    @function_tool
    async def end_call(self, context: RunContext, reason: str = "") -> str:
        """End the call politely when the caller is finished.

        Args:
            reason: Reason why the call is ending.
        """
        logger.info("ClinicSpecialist end_call: %s", reason or "completed")
        goodbye = (
            "Okay, I'm ending the call now. Take care."
            if self.tts_locale.startswith("en")
            else "Theek hai, main call rakh raha hoon. Aapka din shubh ho."
        )
        await context.session.say(goodbye).wait_for_playout()
        context.session.shutdown()
        return "Call ended."
