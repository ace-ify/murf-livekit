"""Day 6 — place an outbound call with Careva (Health Access follow-up / reminder).

Usage (agent must be running: `uv run src/agent.py dev`):

    uv run src/outbound.py +919876543210
    uv run src/outbound.py +919876543210 --name "Ramesh" --reason "BP medicine reminder"

Works with a SIP user too, which is the Twilio-free fallback (Linphone or any
softphone registered to the provider named in the trunk's address):

    uv run src/outbound.py naimish21 --name "Ramesh"

LiveKit wants a phone number or a bare SIP user — never a full sip: URI, since
the host comes from the trunk. A URI is accepted here and trimmed to its user.

This only creates an explicit agent dispatch carrying the call details in job
metadata. The agent itself dials out (see `_dial_out` in agent.py), which is the
pattern LiveKit documents for outbound:
https://docs.livekit.io/telephony/making-calls/outbound-calls/
"""

import argparse
import asyncio
import json
import os
import uuid

from dotenv import load_dotenv
from livekit import api

load_dotenv(".env.local")

AGENT_NAME = "my-agent"


def _clean_destination(dest: str) -> str:
    """LiveKit rejects a full SIP URI: the host lives on the trunk, so send only
    the phone number or SIP user."""
    return dest.removeprefix("sip:").split("@")[0].strip()


async def place_call(phone: str, name: str = "", reason: str = "") -> str:
    dest = _clean_destination(phone)
    room = f"outbound-{dest.lstrip('+')}-{uuid.uuid4().hex[:6]}"
    async with api.LiveKitAPI() as lk:
        await lk.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=AGENT_NAME,
                room=room,
                metadata=json.dumps({"phone": dest, "name": name, "reason": reason}),
            )
        )
    return room


def main() -> None:
    p = argparse.ArgumentParser(description="Place an outbound Careva call.")
    p.add_argument("phone", help="E.164 number (+919876543210) or SIP user (naimish21)")
    p.add_argument("--name", default="", help="Caller's name, if known")
    p.add_argument(
        "--reason",
        default="",
        help='Why we are calling, e.g. "BP medicine reminder"',
    )
    args = p.parse_args()

    if not _clean_destination(args.phone):
        p.error("destination is empty")
    if not os.getenv("SIP_OUTBOUND_TRUNK_ID"):
        p.error("SIP_OUTBOUND_TRUNK_ID is not set in .env.local")

    room = asyncio.run(place_call(args.phone, args.name, args.reason))
    print(
        f"dispatched {AGENT_NAME} to {room} → calling {_clean_destination(args.phone)}"
    )


if __name__ == "__main__":
    main()
