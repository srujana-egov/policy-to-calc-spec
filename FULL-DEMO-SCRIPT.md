# Full end-to-end demo script — trade-license, start to finish

One module (`trade-license`) run through all three prototypes in order: register its entity
schema, add a data record against it, author its fee/tax rules (referencing the same schema),
then configure its approval workflow. Every question/prompt below is copied verbatim from the
real wizard code (`ask()`/`ask_yes_no()` calls in each `wizard.py`), not paraphrased — this is
meant to be followed literally, line by line, in three separate terminal runs.

Runs entirely in **dry-run mode** (no `DIGIT_SERVER_URL`/`DIGIT_JWT_TOKEN`/`DIGIT_TENANT_ID`/
`DIGIT_USER_ID` set) — nothing gets written to a real server. Each wizard still does everything
else for real: builds the object, validates it, renders the real preview (schema table / rule
table + worked examples / diagram), and prints the exact request body it *would* have sent.

**One honest limitation, called out up front, not glossed over:** without a live registry server
running, `calc-engine-prototype/wizard.py`'s "pick a field from the registry schema" step can't
actually fetch what Part 1 just registered — there's nothing running to `GET` it from. It'll fall
back to manual path entry automatically. Part 2 below tells you exactly what to type by hand at
that point — the same field path Part 1 defined — so the result is identical to what the live
fetch would have produced, even though the live HTTP call itself doesn't happen in this offline
run.

## Setup (once, before any of the three parts)

```
cd /Users/srujana/Projects/policy-to-calc-spec
python3 -m venv .venv
source .venv/bin/activate
pip install pydantic
```

Keep that terminal (with the venv activated) open for all three parts below — or just prefix
every command with `.venv/bin/python3` instead of `python3` if you open new terminal tabs.

---

## Part 1 — Registry: schema + data (`registry-prototype/`)

```
cd registry-prototype
python3 wizard.py
```

```
=== Registry schema wizard ===
(type 'quit' at any question to stop -- nothing is saved until the very end)

What do you want to call this schema? (e.g. 'license-registry')
> trade-license-application

Name this field (e.g. 'License Number') -- or leave blank if you're done adding fields:
> employeeCount
  What kind of value does 'employeeCount' hold?
    1. text
    2. whole number
    3. decimal number
    4. yes/no
    5. date
    6. one of a fixed list of choices
    7. a group of related fields (e.g. an address with street/city/zip)
  Pick 1-7:
> 2
  Does 'employeeCount' need a minimum or maximum allowed value? (yes/no)
> no
  Is 'employeeCount' required on every record? (yes/no)
> yes
  One-line description for 'employeeCount' (optional):
>

Name this field (e.g. 'License Number') -- or leave blank if you're done adding fields:
> premisesArea
  What kind of value does 'premisesArea' hold?
  Pick 1-7:
> 3
  Does 'premisesArea' need a minimum or maximum allowed value? (yes/no)
> no
  Is 'premisesArea' required on every record? (yes/no)
> yes
  One-line description for 'premisesArea' (optional):
>

Name this field (e.g. 'License Number') -- or leave blank if you're done adding fields:
> hasLiquorLicense
  What kind of value does 'hasLiquorLicense' hold?
  Pick 1-7:
> 4
  Is 'hasLiquorLicense' required on every record? (yes/no)
> no
  One-line description for 'hasLiquorLicense' (optional):
>

Name this field (e.g. 'License Number') -- or leave blank if you're done adding fields:
>

Should any field (or combination of fields) be unique across every record? (yes/no)
> no

Do you want any field indexed for fast search/filtering? (yes/no)
> no

All checks passed. Open this in a browser to review it visually:
  /.../registry-prototype/trade-license-application_schema_preview.html
(click any field for its exact definition)

Does this look right? Confirm to create the schema (yes/no)
> yes

=== DRY RUN (DIGIT_SERVER_URL/DIGIT_TENANT_ID/DIGIT_USER_ID not all set -- nothing sent) ===
[prints the exact POST /registry/v3/schema body it would have sent]

Add data records to this schema now? (yes/no)
> yes

--- New record ---
  employeeCount (required) -- whole number:
> 18
  premisesArea (required) -- number:
> 800
  hasLiquorLicense (optional, blank to skip) -- yes/no:
>

Add another record? (yes/no)
> no

Does this look right? Confirm to create these records (yes/no)
> yes

=== DRY RUN (DIGIT_SERVER_URL/DIGIT_TENANT_ID/DIGIT_USER_ID not all set -- nothing sent) ===
[prints the exact POST /registry/v3/trade-license-application/data body it would have sent]
```

**What just happened:** a real, validated JSON Schema for `trade-license-application` (two
required fields, one optional), plus one real data record against it — both previewed as tables,
both confirmed explicitly, both ready to send the moment real server credentials exist.

---

## Part 2 — Calculation Engine: rules referencing the same schema (`calc-engine-prototype/`)

```
cd ../calc-engine-prototype
python3 wizard.py
```

```
=== Calculation rule wizard ===
(type 'quit' at any question to stop -- nothing is saved until the very end)

What module are these rules for? (e.g. 'trade-license')
> trade-license

Do you want to pick fields from an existing registry schema (recommended, avoids typos in field paths)? (yes/no)
> yes
  What's the schema code? (e.g. 'trade-license-application')
> trade-license-application
  Couldn't fetch that schema -- DIGIT_SERVER_URL/DIGIT_TENANT_ID/DIGIT_USER_ID must all be set to look up a registry schema.
  You'll type field paths manually for the rest of this session.
```

*(This is the fallback mentioned up top — expected, not an error. From here on, type the field
path by hand exactly as shown; it's the same path the real fetch would have handed you.)*

```
What kind of charge is this?
  1. A flat amount every time (optionally depending on conditions)
  2. A rate multiplied by some field (e.g. per square foot)
  3. A rate charged once per item in a repeating list (e.g. per accessory)
  4. Tiered/marginal bands of the same field (e.g. income tax brackets)
  5. A percentage of another fee (a tax or cess on top of it)
  6. A rebate or deduction from another fee
  7. Totaling up a repeating list into one number (e.g. total floor area)
  8. Real math combining more than one input
  Pick 1-8:
> 1
What do you want to call this fee/component? (e.g. 'BASE_LICENSE_FEE')
> STAFFING_FEE
How much is it?
> 1200
Does this rule only apply under certain conditions? (yes/no)
> yes
  Give this condition a short name (e.g. 'premisesArea'):
> employeeCount
  Type the field path does 'employeeCount' come from (e.g. 'tradeLicenseDetail.premisesArea'):
> certificateDetail.employeeCount
    1. Must equal a specific value
    2. Must fall within a range
    3. Just needs to be present (any value counts)
    Pick 1-3:
> 2
  Lowest allowed value for 'employeeCount'? (blank for no minimum)
> 10
  Highest allowed value for 'employeeCount'? (blank for no maximum)
> 24
  Another condition? (yes/no)
> no
Any specific ordering priority? (lower runs first; blank for default 100)
> 10
  Round the result to the nearest:
    1. Whole currency unit (default)
    2. 10
    3. 100
    4. Don't round
  Pick 1-4 (blank for default):
>
What date does this take effect? (YYYY-MM-DD)
> 2024-04-01
Does it stop applying on some later date? (YYYY-MM-DD, or blank if it doesn't expire)
>

Add another rule? (yes/no)
> yes
```

*(One thing worth knowing before typing the next three rules: the "which component" list is
sorted **alphabetically**, not in the order you created them — so the number for `STAFFING_FEE`
shifts as more components get added. Pay attention to the actual printed list each time rather
than assuming the number stays the same; the transcript below shows exactly what each one prints
and picks.)*

```
What kind of charge is this?
  Pick 1-8:
> 5
What do you want to call this tax/cess/component?
> CGST
What percentage?
> 9
  Which component is this a percentage of?
    1. STAFFING_FEE
  Pick 1-1:
> 1
Is this a statutory tax (rather than a general fee)? (yes/no)
> yes
Does this rule only apply under certain conditions? (yes/no)
> no
Any specific ordering priority? (lower runs first; blank for default 100)
> 20
  Pick 1-4 (blank for default):
>
What date does this take effect? (YYYY-MM-DD)
> 2024-04-01
Does it stop applying on some later date? (YYYY-MM-DD, or blank if it doesn't expire)
>

Add another rule? (yes/no)
> yes

What kind of charge is this?
  Pick 1-8:
> 5
What do you want to call this tax/cess/component?
> SGST
What percentage?
> 9
  Which component is this a percentage of?
    1. CGST
    2. STAFFING_FEE
  Pick 1-2:
> 2
Is this a statutory tax (rather than a general fee)? (yes/no)
> yes
Does this rule only apply under certain conditions? (yes/no)
> no
Any specific ordering priority? (lower runs first; blank for default 100)
> 20
  Pick 1-4 (blank for default):
>
What date does this take effect? (YYYY-MM-DD)
> 2024-04-01
Does it stop applying on some later date? (YYYY-MM-DD, or blank if it doesn't expire)
>

Add another rule? (yes/no)
> yes

What kind of charge is this?
  Pick 1-8:
> 5
What do you want to call this tax/cess/component?
> FIRE_CESS
What percentage?
> 1
  Which component is this a percentage of?
    1. CGST
    2. SGST
    3. STAFFING_FEE
  Pick 1-3:
> 3
Is this a statutory tax (rather than a general fee)? (yes/no)
> yes
Does this rule only apply under certain conditions? (yes/no)
> no
Any specific ordering priority? (lower runs first; blank for default 100)
> 20
  Pick 1-4 (blank for default):
>
What date does this take effect? (YYYY-MM-DD)
> 2024-04-01
Does it stop applying on some later date? (YYYY-MM-DD, or blank if it doesn't expire)
>

Add another rule? (yes/no)
> no

All checks passed. Open this in a browser to review it visually:
  /.../calc-engine-prototype/trade-license_rules_preview.html
(click any row for its exact rule definition -- N worked example(s) included, showing what this actually computes)

Does this look right? Confirm to create these rules (yes/no)
> yes

=== DRY RUN (DIGIT_SERVER_URL/DIGIT_TENANT_ID/DIGIT_USER_ID/DIGIT_JWT_TOKEN not all set -- nothing sent) ===
Would send 4 separate POST request(s) to: {server}/calculation/v3/trade-license/rules (one per rule)
[prints each rule's exact JSON body]
```

**Open `trade-license_rules_preview.html` in a browser here** — this is the payoff moment. Click
into the worked examples: an `employeeCount` around 18 falls inside the 10–24 band, so
`STAFFING_FEE` = ₹1200, `CGST` = ₹108, `SGST` = ₹108, `FIRE_CESS` = ₹12 — **total ₹1428** — computed
for real by `simulate.py`, not just four rule definitions someone has to add up by hand. This is
the exact same math (base fee + CGST + SGST + a cess, all percentages of one component) the real
`calculation-engine-3.0.0.yaml` spec's own canonical example describes.

---

## Part 3 — Workflow: the approval process (`workflow-prototype/`)

```
cd ../workflow-prototype
python3 wizard.py
```

Use the short "Leave Approval" example from earlier in this session if you want the fastest
version (3 states, ~1 minute), or the full `trade-license-approval` example (5 states, loops,
role-restricted actions) if you want to show a real production-shaped process — that transcript is
already written out in `DEMO-2026-07-15.md`'s §4 walkthrough, verbatim-runnable the same way as
above.

Either way, it ends the same way: a rendered, clickable diagram, an explicit confirmation, then a
dry-run print of the exact `POST /workflow/v3/process/definition` body.

---

## What this demonstrated

One module, three independent tools, one consistent thread through all of them: the
`employeeCount`/`premisesArea` fields Part 1 registered are the exact fields Part 2's rules
condition on; Part 2's rules are what a `trade-license` application actually gets charged; Part 3
is the process that application moves through to get approved. Nothing here required a live DIGIT
deployment — every step is real, validated, previewed, and confirmed; only the final network call
is simulated.
