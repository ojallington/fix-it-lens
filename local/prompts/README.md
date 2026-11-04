# Glimmer prompt adaptations

Local mode sends the api prompts unchanged except where Glimmer needs other words. Each `<role>.md` replaces one
exact upstream text, named by its sha256 in the front matter; `calls/local_prompts.py` swaps it in when the request
body is built. If the upstream text changes, the hash stops matching, Glimmer gets the new text unadapted, and
`tests/l7/test_local_prompts.py` fails until the adaptation is redone and re-measured.

| Role | Why | Measured |
|---|---|---|
| `judge` | Under the api judge rules Glimmer returned `visible: false, state: unclear` for every clause, even when its own evidence said the clause was false. Asking the three questions in order, and requiring evidence and state to agree, fixed it. | Smoke run, one public photo, 3 cases x 2 runs: 0/3 → 3/3, same answers on both runs, 1.2-2.5 s. A smoke, not an eval |
