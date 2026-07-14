"""Interactive CLI wizard for building a workflow -- the real question sequence a business user
would answer, driving the same WorkflowBuilder the automated tests use. No free text description
of the whole process is ever asked for; every question is scoped to one state at a time, and
"anything else?" is asked explicitly before moving on, so a branch can't be skipped silently.

Run: python wizard.py
"""

from builder import WorkflowBuilder
from validate import validate_process_definition


def ask(prompt: str) -> str:
    return input(prompt + " ").strip()


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
        success = ask_yes_no("Is this a good outcome?")
        builder.mark_terminal(state_code, success=success)
        print(f"  -> marked {state.type}")
        return

    while True:
        label = ask("  Name this action (e.g. 'Approve', 'Reject'):")
        existing_codes = [c for c in builder.states if c != state_code]
        target_desc = "an existing state" if existing_codes else "nothing existing yet"
        goes_to_existing = False
        if existing_codes:
            goes_to_existing = ask_yes_no(
                f"  Does '{label}' lead back to an existing state ({', '.join(existing_codes)}), "
                f"or somewhere new?"
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


def main():
    print("=== Workflow wizard ===")
    name = ask("What's this workflow called?")
    code = ask("Give it a short code (e.g. 'trade-license-approval'):")
    description = ask("One-line description (optional):")
    overall_sla = ask_sla_ms()

    builder = WorkflowBuilder(name=name, code=code, description=description, sla_ms=overall_sla)

    first_state_name = ask("What's the very first thing that happens?")
    first_code = builder.add_initial_state(first_state_name)
    print(f"  -> '{first_state_name}' is the INITIAL state ({first_code})")
    configure_state(builder, first_code)

    while True:
        pending = builder.next_unconfigured_state()
        if pending is None:
            break
        configure_state(builder, pending)

    process = builder.build()
    errors = validate_process_definition(process)

    print("\n=== Result ===")
    print(process.model_dump_json(indent=2, exclude_none=True))
    if errors:
        print("\nVALIDATION FAILED:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("\nAll checks passed -- ready for POST /process/definition.")


if __name__ == "__main__":
    main()
