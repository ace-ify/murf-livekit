"""Day 7 — the human side of an escalation: list open cases, resolve one, call back.

Usage (from backend/):

    uv run src/escalations.py list
    uv run src/escalations.py list --status resolved
    uv run src/escalations.py resolve ESC-0007 --note "ANM visited, referred to CHC"
    uv run src/escalations.py resolve ESC-0007 --note "done" --call
    uv run src/escalations.py resolve ESC-0007 --call --phone +919876543210

`--call` reuses the Day 6 outbound path, so it needs SIP_OUTBOUND_TRUNK_ID set and the
agent running (`uv run src/agent.py dev`) — same precondition as src/outbound.py.

ponytail: no HTTP server and no poller. The admin page writes status straight into SQLite
and prints the command above; if clinicians ever want one-click callback, the smallest
next step is a ~20-line asyncio loop over `status='resolved' AND callback_dialed=0`.
"""

import argparse
import asyncio

from dotenv import load_dotenv

import db
from outbound import place_call

load_dotenv(".env.local")


async def cmd_list(status: str) -> None:
    await db.init_db()  # a fresh checkout has no tables yet
    rows = await db.list_escalations(status=status)
    if not rows:
        print(f"no escalations with status={status or 'any'}")
        return
    for r in rows:
        print(
            f"{r['ref']}  {r['status']:<12} {r['urgency']:<9} {r['language']:<4} "
            f"{r['caller_name'] or 'unknown'} ({r['caller_user_id']})\n"
            f"    what:     {r['what_happened']}\n"
            f"    checked:  {r['already_checked'] or '-'}\n"
            f"    followup: {r['followup_method'] or '-'}  |  raised {r['created_at']}"
        )


async def cmd_resolve(ref: str, note: str, call: bool, phone: str) -> None:
    await db.init_db()
    rec = await db.update_escalation_status(ref, "resolved", note)
    if not rec:
        print(f"no escalation found for {ref}")
        return
    print(
        f"{rec['ref']} -> resolved" + (f" ({rec['resolution_note']})" if note else "")
    )

    if not call:
        return
    dest = phone or rec["callback_phone"]
    if not dest:
        print(
            "  no phone on this case (web caller) — pass --phone +91... to call back anyway"
        )
        return
    room = await place_call(
        dest,
        name=rec["caller_name"],
        reason=f"follow-up on your case {rec['ref']}",
    )
    print(f"  dispatched callback to {dest} in room {room}")


def main() -> None:
    p = argparse.ArgumentParser(description="Careva human escalations (Day 7).")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="show escalations, newest first")
    p_list.add_argument(
        "--status",
        default="open",
        help='open | acknowledged | resolved | "" for all (default: open)',
    )

    p_res = sub.add_parser(
        "resolve", help="mark resolved, optionally call the caller back"
    )
    p_res.add_argument("ref", help="reference number, e.g. ESC-0007")
    p_res.add_argument("--note", default="", help="what the human did")
    p_res.add_argument(
        "--call",
        action="store_true",
        help="call the caller back (needs SIP + agent running)",
    )
    p_res.add_argument("--phone", default="", help="override the callback number")

    args = p.parse_args()
    if args.cmd == "list":
        asyncio.run(cmd_list(args.status))
    else:
        asyncio.run(cmd_resolve(args.ref, args.note, args.call, args.phone))


if __name__ == "__main__":
    main()
