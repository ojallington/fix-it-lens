"""python -m eval run [--live] [--write] | claims | pilot"""

from __future__ import annotations

import sys


def main() -> int:
    cmd, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("run", [])
    if cmd == "run":
        from eval.harness import main as run

        return run(rest)
    if cmd == "claims":
        from eval.claims import main as claims

        return claims()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
