"""Where the app keeps things. The private folder comes from FIXIT_PRIVATE (calls.private_dir); nothing here turns a
gate off, and nothing here may ever do so."""

from __future__ import annotations

import os
from pathlib import Path

import calls

LANE = "l1"
ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "agent" / "prompt.md"
CARDS_DIR = ROOT / "cards"

S2C_RING_S = 600.0  # resume window: a 10-minute ring of s2c messages
SESSION_LEFT_S = 300.0  # a session no phone has come back to for 5 minutes ends (outcome abandoned; End is ended)
FRAME_RING_S = 6.0  # frames kept in memory: enough for BEFORE-less NOW picks (3 s) and the 2 s SAM clip
JUDGE_WINDOW_S = 3.0  # NOW_1 and NOW_2 come from the last 3 s
NOW_GAP_S = (0.5, 1.0)  # NOW_1 and NOW_2 are 0.5-1 s apart
CLIP_S = 2.0  # the SAM witness tracks the part over the last 2 s
CALL_TIMEOUT_S = 6.0  # every live model call: a slower one is a failure the session says out loud
# Every guide call. Measured live on the Bosch card with its whole manual in the prefix (64 pages, ~44k input tokens,
# prompt-cached after the first call; 35 calls, 27 Oct): p50 2.8 s, p95 5.0 s, max 6.4 s, so 6 s cut the tail and
# the first eval call timed out. 12 s is about 2.4x p95. The step card, its page, the checks, G1 and a stop tap
# never wait on it; only the guide's words do.
GUIDE_TIMEOUT_S = 12.0
ASK_FOR_VIEW_WAIT_S = 20.0
GUIDE_MAX_ROUNDS = 6
# A turn nobody asked for (after a verdict) waits once the step has used this many guide rounds; the person's own
# words still get an answer.
GUIDE_STEP_ROUNDS = 12
VIEW_CHANGED_BITS = 20  # a frame this many phash bits (of 64) from the one at an ask is a changed view
DOOM_REPEATS = 3
MASK_MIN_CONF = 0.6  # a mask under 0.6 falls back to Spark's box, drawn larger
BOX_FALLBACK_GROW = 0.15
MAX_CHECK_ATTEMPTS = 3


def private_dir() -> Path | None:
    return calls.private_dir()


def served_fixtures() -> Path | None:
    """Where the served app's record mode writes its response fixtures: FIXIT_PRIVATE/fixtures, never the repo's
    fixtures/ (a live session's replies quote the manual and the user's words)."""
    priv = private_dir()
    return None if priv is None else priv / "fixtures"


GOLDEN = "golden"  # the replay id that names the curated recording, so a link to it never changes


def golden_replay(replays: Path) -> str | None:
    """The curated recording `golden` resolves to: FIXIT_REPLAY_GOLDEN, else the id in <replays>/GOLDEN."""
    rid = os.environ.get("FIXIT_REPLAY_GOLDEN")
    if not rid and (replays / "GOLDEN").is_file():
        rid = (replays / "GOLDEN").read_text().strip()
    return rid or None


def sessions_dir() -> Path:
    priv = private_dir()
    if priv is None:
        raise RuntimeError("FIXIT_PRIVATE is not set and the default private folder is missing; sessions live there")
    return priv / "sessions"
