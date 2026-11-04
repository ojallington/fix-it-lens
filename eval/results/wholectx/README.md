# Whole context against retrieval: recorded results

`whole.jsonl` and `retrieved.jsonl` are written by `make wholectx-live` (`python -m eval.wholectx`), one Outcome per
question: verdicts, citation counts, quote hashes (sha256, 16 hex), page numbers, cost, latency and the agent's
lookup queries. No manual text: the recorded responses stay in FIXIT_PRIVATE/fixtures-l6/, and `make manualgrep`
checks this folder before each commit. Until the first recording, `make eval` reports the row as unmeasured.
