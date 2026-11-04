"""python -m eval.gap run [--kind K] | score [--kind K]"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from eval.gap.measure import claims, load_results, load_stops, run, write_results


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.gap")
    ap.add_argument("cmd", choices=["run", "score"])
    ap.add_argument("--kind", action="append")
    a = ap.parse_args(argv)
    kinds = a.kind or ["gas", "casing"]
    if a.cmd == "run":
        stops = [s for s in load_stops() if s.kind in kinds]
        results = asyncio.run(run(stops))
        for k in kinds:
            print(write_results(k, [r for r in results if r.kind == k]))
        return 0
    for k in kinds:
        for c in claims(k, load_results(k)):
            print(json.dumps(c, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
