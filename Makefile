# Fix-It Lens. `make ci` is what CI runs; it needs no key and no network once `make setup` has fetched deps.
.PHONY: demo setup hooks contracts contracts-check lint type test test-py test-ts guard boundary acme eval eval-live wholectx-live spoken claims label pilot manualgrep ci check-pr

UV := uv run --quiet
TS := contracts/ts
# phone/ joins setup, type and test-ts once its package.json lands.
PHONE := $(wildcard phone/package.json)

# The app in replay on http://127.0.0.1:8080/?replay=demo: no key, no camera, no Tailscale, no private folder.
demo:
	@test -d phone/node_modules || (cd phone && npm ci --prefer-offline --no-audit --no-fund --silent)
	@test -f phone/dist/index.html || (cd phone && npm run --silent build)
	$(UV) python -m deploy.demo

setup:
	uv sync --quiet
	cd $(TS) && npm ci --prefer-offline --no-audit --no-fund --silent
	$(if $(PHONE),cd phone && npm ci --prefer-offline --no-audit --no-fund --silent)

hooks:
	git config core.hooksPath .githooks

contracts:
	$(UV) python tools/gen_contracts.py

contracts-check:
	$(UV) python tools/gen_contracts.py --check

lint:
	$(UV) ruff check .

type:
	$(UV) mypy
	cd $(TS) && npx tsc --noEmit
	$(if $(PHONE),cd phone && npx tsc --noEmit)

test: test-py test-ts

test-py:
	$(UV) pytest -q

test-ts:
	cd $(TS) && npx vitest run --silent
	$(if $(PHONE),cd phone && npx vitest run --silent)

guard:
	$(UV) python tools/guard.py --all

CHECK_COUNTRY ?= nl
check-pr:
	$(UV) python -m ingest.check_pr $(if $(CI),--ci)
	$(UV) python -m tools.check_cards --country $(CHECK_COUNTRY)

boundary:
	$(UV) python tools/boundary.py

acme:
	$(UV) python tools/make_acme_manual.py

# eval/harness.py and eval/claims.py. `make eval` is replay only (no key, no network); eval-live records.
eval:
	$(UV) python -m eval run
	$(UV) python -m eval.failures --check

eval-live:
	$(UV) python -m eval run --live

# The whole-context row in one go: what it still lacks, the recorded run (both arms) with --write, then the grep.
wholectx-live:
	$(UV) python -m eval.wholectx check
	$(UV) python -m eval run --live --row wholectx --write
	$(UV) python -m eval.manualgrep eval/results/wholectx
	git status --short eval/results/wholectx eval/claims.json

# Polish #4: no Markdown in any sentence the guide spoke, over every recorded session (private, so not in make eval).
spoken:
	$(UV) python -m eval.spoken

claims:
	$(UV) python -m eval claims

label:
	$(UV) python -m eval.label.server

pilot:
	$(UV) python -m eval.pilot

# Needs FIXIT_PRIVATE/manuals: run on the build machine before a merge (CI has no manuals).
manualgrep:
	$(UV) python -m eval.manualgrep

ci: setup lint type contracts-check guard boundary test eval claims
