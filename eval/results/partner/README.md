# Partner results

The eval gate and cost for free-credit routing, written by `python -m eval.partner`:

- `<provider>/judge_sam.jsonl`: the step-check clips' predictions with the judge on that partner (`run`);
- `cost.jsonl`: per public session a partner served, anonymised (`cost`).

`python -m eval.partner score` prints the claims from these files; with none yet, every claim is unmeasured.
