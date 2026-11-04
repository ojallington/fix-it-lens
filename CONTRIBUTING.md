# Contributing

Thanks for helping. Fix-It Lens takes two kinds of contribution: code, and data (cards and rules files).

## Data

- **A card** for your appliance: `cards/<brand>/<model>.yaml`, in the form described in `schema/card.md`.
  `fixit ingest build manual.pdf --brand <brand> --model <model> --url <maker's page> --pr` drafts one from your copy of
  the manual, and `fixit check-pr` runs the checks CI runs on it. Cards cite pages and carry short verbatim
  quotes (at most two sentences); they never contain the manual's text, and the PDF never enters the repo.
- **A rules file** for your country: `rules/<country>.yaml`, in the form described in `schema/rules.md`. Rule IDs are
  `<country>.<family>.<zone>`.
- A PR that widens a gas or mains allow-list, or adds a step-level zone override, needs two approvals from the
  safety reviewers, the owners of the `/rules/` lines in `.github/CODEOWNERS`. Today that is one person, so such a PR
  waits for a second reviewer.
- Safety stays code and stays on: no flag, env var or config may turn a gate off. A test fails the build if one
  appears.

## Code

- Python 3.12 with type hints; `mypy --strict` on `contracts/`, `calls/` and `rules/`. TypeScript strict.
- `make ci` must pass, offline: ruff, mypy, pytest, vitest, the guard, the import-boundary test, the contracts check,
  `make eval` and `make claims`.
- Only `calls/` talks to a model. Only `app/` imports `rules/`.
- Contracts change through `contracts/*.schema.json` and `make contracts`; never edit generated types.
- Tests never call a live API. Use recorded fixtures under `fixtures/<lane>/` (responses only, never request bodies).
- Install the guard hook once: `make hooks`.

## Never commit

PDFs, plate photos, serial numbers, API keys, recorded request bodies, house identifiers, or faces. The guard
(`tools/guard.py`) blocks the ones it can detect.

## Security

A security or safety problem (a way past a gate, a leak of household data) goes to `SECURITY.md`, not an issue.
