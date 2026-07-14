"""Interactive CLI wizard for building a workflow -- the real question sequence a business user
would answer, driving the same WorkflowBuilder the automated tests use. No free text description
of the whole process is ever asked for; every question is scoped to one state at a time, and
"anything else?" is asked explicitly before moving on, so a branch can't be skipped silently.

Run: python wizard.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from builder import WorkflowBuilder
from render import render_html
from validate import validate_process_definition


class Cancelled(Exception):
    """Raised when the user types quit/exit at any prompt -- caught once, at the top level."""


def ask(prompt: str) -> str:
    answer = input(prompt + " ").strip()
    if answer.lower() in ("quit", "exit", "q"):
        raise Cancelled
    return answer


def ask_yes_no(prompt: str) -> bool:
    while True:
        answer = ask(prompt + " (yes/no)").lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  please answer yes or no")


def ask_sla_ms() -> int | None:
    raw = ask("How long should this take? (e.g. '2 days', '12 hours', '86400 seconds', or blank for no SLA)")
    if not raw:
        return None
    parts = raw.split()
    try:
        amount = float(parts[0])
    except (ValueError, IndexError):
        print("  couldn't parse that, skipping SLA for now")
        return None
    unit = parts[1].lower() if len(parts) > 1 else "days"
    unit_ms_table = {
        "day": 86_400_000, "days": 86_400_000,
        "hour": 3_600_000, "hours": 3_600_000,
        "minute": 60_000, "minutes": 60_000,
        "second": 1_000, "seconds": 1_000, "sec": 1_000, "secs": 1_000,
    }
    if unit not in unit_ms_table:
        # Never silently guess a unit -- a wrong silent default here (e.g. treating a bare
        # number as days) would produce an SLA that's wrong by orders of magnitude.
        print(f"  unrecognized unit '{unit}' -- please answer again with days/hours/minutes/seconds")
        return ask_sla_ms()
    return int(amount * unit_ms_table[unit])


def ask_roles(action_label: str) -> list[str]:
    raw = ask(f"  Who can perform '{action_label}'? (comma-separated roles, or blank for anyone)")
    return [r.strip() for r in raw.split(",") if r.strip()]


def configure_state(builder: WorkflowBuilder, state_code: str) -> None:
    state = builder.states[state_code]
    print(f"\n--- Configuring '{state.name}' ({state_code}) ---")

    if state.sla is None:
        state.sla = ask_sla_ms()

    has_next = ask_yes_no(f"What can happen from '{state.name}'? Is there at least one next step?")
    if not has_next:
        if state.type == "INITIAL":
            print("  A process can't end at its very first stage -- there has to be at least "
                  "one next step. Let's define what actually happens next.")
        else:
            success = ask_yes_no("Is this a good outcome?")
            builder.mark_terminal(state_code, success=success)
            print(f"  -> marked {state.type}")
            return

    while True:
        label = ask("  Name this action (e.g. 'Approve', 'Reject') -- or leave blank if you didn't mean to add one:")
        if not label:
            break
        # Includes state_code itself -- a self-loop (an action that leads back to the very
        # state it's defined on, e.g. a resubmission or an "adhoc" retry action) is a real,
        # legitimate pattern, not an edge case to exclude.
        existing_codes = list(builder.states.keys())
        goes_to_existing = ask_yes_no(
            f"  Does '{label}' lead back to an existing state ({', '.join(existing_codes)} -- "
            f"could even be back to '{state.name}' itself)?"
        )
        roles = ask_roles(label)

        if goes_to_existing:
            target_code = ask(f"  Which existing state code does it lead to? ({', '.join(existing_codes)})")
            builder.add_action_to_existing_state(state_code, label, target_code, roles=roles)
        else:
            new_name = ask("  What's the name of the state it leads to?")
            new_code = builder.add_action_to_new_state(state_code, label, new_name, roles=roles)
            print(f"  -> queued new state '{new_name}' ({new_code})")

        more = ask_yes_no(f"  Anything else that can happen from '{state.name}'?")
        if not more:
            break


def redo_state(builder: WorkflowBuilder, state_code: str) -> None:
    """Wipes one state's answers and re-asks its questions from scratch -- the targeted fix,
    so a wrong role or transition doesn't force restarting the whole wizard."""
    state = builder.states[state_code]
    state.actions = []
    state.sla = None
    if state.type != "INITIAL":
        state.type = "INTERMEDIATE"
    configure_state(builder, state_code)


def edit_process_fields(builder: WorkflowBuilder) -> None:
    print(f"\n--- Fixing the workflow's own details (blank keeps the current value) ---")
    name = ask(f"What's this workflow called? (currently '{builder.name}')")
    if name:
        builder.name = name
    code = ask(f"Short code? (currently '{builder.code}')")
    if code:
        builder.code = code
    description = ask(f"One-line description? (currently '{builder.description}')")
    if description:
        builder.description = description
    if ask_yes_no(f"Change the overall SLA? (currently {builder.sla})"):
        builder.sla = ask_sla_ms()


def offer_fix(builder: WorkflowBuilder) -> None:
    """The edit menu shown instead of discarding the whole session -- pick a state to redo,
    the workflow's own name/code/SLA, or drop an unwanted state entirely."""
    state_codes = ", ".join(builder.states.keys())
    choice = ask(
        "What do you want to fix? Type a state code to redo its questions, "
        f"'delete STATE_CODE' to remove an unwanted state, or 'process' for the "
        f"workflow's own name/code/description/SLA.\nStates: {state_codes}"
    )
    lowered = choice.lower()
    if lowered == "process":
        edit_process_fields(builder)
    elif lowered.startswith("delete "):
        target = choice.split(None, 1)[1].strip()
        if target not in builder.states:
            print(f"  '{target}' isn't a known state code -- nothing removed")
        else:
            try:
                builder.remove_state(target)
                print(f"  -> removed '{target}'")
            except ValueError as e:
                print(f"  {e}")
    elif choice in builder.states:
        redo_state(builder, choice)
    else:
        print(f"  '{choice}' isn't a known state code -- nothing changed")


def run_session():
    """Runs the full question sequence and returns the final, validated ProcessDefinitionInput.
    Split out from main() so tests can drive the exact same interactive code path (via a mocked
    input()) and inspect the result directly, instead of scraping printed output."""
    print("=== Workflow wizard ===")
    print("(type 'quit' at any question to stop -- nothing is saved until the very end)\n")
    name = ask("What's this workflow called?")
    code = ask("Give it a short code (e.g. 'trade-license-approval'):")
    description = ask("One-line description (optional):")
    overall_sla = ask_sla_ms()

    builder = WorkflowBuilder(name=name, code=code, description=description, sla_ms=overall_sla)

    first_state_name = ask("What's the first stage of this process called? (e.g. 'Pending Review', 'Application Submitted')")
    first_code = builder.add_initial_state(first_state_name)
    print(f"  -> '{first_state_name}' is the INITIAL state ({first_code})")
    configure_state(builder, first_code)

    while True:
        pending = builder.next_unconfigured_state()
        if pending is None:
            break
        configure_state(builder, pending)

    while True:
        process = builder.build()
        errors = validate_process_definition(process)

        if errors:
            print("\nVALIDATION FAILED -- fix these before a preview would mean anything:")
            for e in errors:
                print(f"  - {e}")
            offer_fix(builder)
            continue

        preview_path = os.path.abspath(f"{process.code}_preview.html")
        render_html(process, preview_path)
        print(f"\nAll checks passed. Open this in a browser to review it visually:\n  {preview_path}")
        print("(click any box for its SLA and every action's roles, or an arrow for that one action)")

        if ask_yes_no("\nDoes this look right? Confirm to write it"):
            break

        print("Not confirmed -- let's fix just the part that's wrong (type 'quit' to stop entirely).")
        offer_fix(builder)

    return process


def main():
    write_process_definition(run_session())


def write_process_definition(process) -> None:
    server_url = os.environ.get("DIGIT_SERVER_URL")
    jwt_token = os.environ.get("DIGIT_JWT_TOKEN")
    tenant_id = os.environ.get("DIGIT_TENANT_ID")
    user_id = os.environ.get("DIGIT_USER_ID")
    body = process.model_dump_json(exclude_none=True).encode()

    if not (server_url and jwt_token and tenant_id and user_id):
        print("\n=== DRY RUN (DIGIT_SERVER_URL/DIGIT_JWT_TOKEN/DIGIT_TENANT_ID/DIGIT_USER_ID not "
              "all set -- nothing sent) ===")
        print("Would POST to: {server}/workflow/v3/process/definition")
        print("Headers: Content-Type: application/json, Authorization: Bearer <token>, "
              f"X-Tenant-ID: <tenant>, X-User-ID: <user>")
        print("Body:")
        print(process.model_dump_json(indent=2, exclude_none=True))
        return

    url = server_url.rstrip("/") + "/workflow/v3/process/definition"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {jwt_token}",
        "X-Tenant-ID": tenant_id,
        "X-User-ID": user_id,
    })
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"\nCreated -- {resp.status} {resp.reason}")
            print(json.loads(resp.read())["code"], "is now live.")
    except urllib.error.HTTPError as e:
        print(f"\nWrite failed -- {e.code} {e.reason}")
        print(e.read().decode(errors="replace"))


if __name__ == "__main__":
    try:
        main()
    except Cancelled:
        print("\nCancelled -- nothing was saved.")
    except (KeyboardInterrupt, EOFError):
        print("\n\nCancelled -- nothing was saved.")
