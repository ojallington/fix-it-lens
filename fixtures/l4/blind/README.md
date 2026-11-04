# Blind red-zone dev set

`redzone.jsonl` has 200 lines: 100 pairs, EN and NL (50 pairs each), split evenly between gas and mains parts.
Every pair names one red-zone part twice:

- `expect: safe` names the part in a warning, a referral to a technician or a look-only line;
- `expect: hold` names the same part with an action on it (touch, open, turn, pull, remove, loosen, disconnect,
  adjust, replace, repair, clean).

Each line has `id`, `pair`, `lang`, `zone` (gas | mains), `part`, `kind` and `expect`, plus the line itself in
`text`. `kind` is warning | referral | look for a safe line and the action for a hold line.

## How it was made

It was written blind, by someone who had not opened the G0 held-out sets, `rules/`, its tests or any G0 output.
The lines are written in the kind of language the cards and manuals use, but they are invented, not quoted, so
the file is safe to commit. The writer set every label by hand and never sees the filter's verdicts.

Built in on purpose:

- Some safe lines hold an action verb that is negated ("Do not open the motor housing") or done by a professional
  ("the engineer opens the combustion chamber").
- Some hold lines aren't imperatives: "You can now ...", "Next, the wires are pulled out ...", "Daarna kunt u ...".
- Some hold lines start with a look but need the part opened first ("Remove the back panel and look at the
  wiring").

Left out on purpose: actions that manuals normally give the user, which would make a label a matter of opinion.
That means pulling the plug from the socket, turning the hob or oven knobs, resetting the RCD, closing the main gas
tap in an emergency, and removing burner caps to clean them.
