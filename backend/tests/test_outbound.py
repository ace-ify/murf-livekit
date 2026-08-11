import json
from unittest.mock import AsyncMock, MagicMock, patch

import outbound
from agent import _outbound_greeting


def test_outbound_greeting_states_who_why_and_optout() -> None:
    g = _outbound_greeting("Ramesh", "your BP medicine reminder")
    assert "Careva" in g and "health centre" in g  # who
    assert "BP medicine reminder" in g  # why
    assert "say stop" in g  # how to make it stop
    # Day 6 rule: all of it inside the first two sentences.
    assert len([s for s in g.split(".") if s.strip()]) <= 3

    # No name / no reason still opens legally.
    bare = _outbound_greeting("", "")
    assert "Careva" in bare and "say stop" in bare and "follow-up" in bare


async def test_place_call_dispatches_with_phone_metadata() -> None:
    lk = MagicMock()
    lk.agent_dispatch.create_dispatch = AsyncMock()
    lk.__aenter__ = AsyncMock(return_value=lk)
    lk.__aexit__ = AsyncMock(return_value=False)

    with patch.object(outbound.api, "LiveKitAPI", return_value=lk):
        room = await outbound.place_call("+919876543210", "Ramesh", "vaccine due")

    assert room.startswith("outbound-919876543210-")
    req = lk.agent_dispatch.create_dispatch.await_args.args[0]
    assert req.agent_name == outbound.AGENT_NAME
    assert req.room == room
    assert json.loads(req.metadata) == {
        "phone": "+919876543210",
        "name": "Ramesh",
        "reason": "vaccine due",
    }


def test_clean_destination_strips_sip_uri() -> None:
    # LiveKit rejects a full URI ("should be a phone number or SIP user").
    assert outbound._clean_destination("sip:naimish21@sip.linphone.org") == "naimish21"
    assert outbound._clean_destination("naimish21") == "naimish21"
    assert outbound._clean_destination("+919876543210") == "+919876543210"
