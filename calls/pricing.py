"""Standard-tier prices (USD), from /docs/pricing-rate-limits, read 26 Oct 2026."""

from __future__ import annotations

from dataclasses import dataclass

PER_M = 1_000_000


@dataclass(frozen=True)
class TokenPrice:
    input: float
    cached: float
    output: float


TOKEN_PRICES = {
    "muse-spark-1.3": TokenPrice(input=1.25, cached=0.15, output=4.25),
    "muse-wonder-1.0": TokenPrice(input=4.00, cached=0.20, output=20.00),
    # Muse Spark Open on the partners: list price, the same at all three
    "meta-models/muse-spark-open": TokenPrice(input=1.25, cached=0.15, output=4.25),
    "accounts/fireworks/models/muse-spark-open": TokenPrice(input=1.25, cached=0.15, output=4.25),
    "meta-models/Muse-Spark-Open-816B-A42B": TokenPrice(input=1.25, cached=0.15, output=4.25),
}
SAM_IMAGE_USD = 2.50 / 1000
SAM_VIDEO_FRAME_USD = 0.20 / 1000
SAM_VIDEO_MIN_FRAMES = 50
TRANSCRIBE_USD_PER_HOUR = 0.18
TOKENS_PER_MEGAPIXEL = 1350  # measured 1,300-1,360 tokens per megapixel on day 0


def token_usd(model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    p = TOKEN_PRICES.get(model)
    if p is None:
        return 0.0
    fresh = max(input_tokens - cached_tokens, 0)
    return (fresh * p.input + cached_tokens * p.cached + output_tokens * p.output) / PER_M


def estimate_respond_usd(model: str, text_chars: int, image_pixels: list[int], max_output_tokens: int) -> float:
    """A worst-case estimate for the budget check: every output token used, nothing cached."""
    tokens_in = text_chars // 3 + sum(int(px / 1e6 * TOKENS_PER_MEGAPIXEL) + 1 for px in image_pixels) + 200
    return token_usd(model, tokens_in, 0, max_output_tokens)


def sam_video_usd(frames: int) -> float:
    return max(frames, SAM_VIDEO_MIN_FRAMES) * SAM_VIDEO_FRAME_USD


def transcribe_usd(seconds: float) -> float:
    return int(seconds) / 3600 * TRANSCRIBE_USD_PER_HOUR
