"""SAM 3.1 open weights behind one call: frames plus a prompt in, objects per frame out.

The weights are never bundled: `python -m local.sam_server.fetch` downloads them into SAM_WEIGHTS (default
~/.cache/fixit/sam3.1) with the read token from ~/.config/fleet/hf-read.token, once access to the gated
facebook/sam3.1 repo is approved. torch and sam3 are imported only when the model loads, in the server's own env.

Verified on an RTX 5090 (sam3 f1647e8, weights sha256 0567debe...): text and box prompts on an image,
text on an 8-frame MP4 (found on every frame). Three things the sam3 source does not say, each handled below:
SAM 3.1's init_state rejects an argument start_session passes; the model needs bf16 autocast; and add_prompt answers
frame 0 itself while propagate_in_video re-yields frame 0 empty.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from local.sam_server.encode import Found, Prompt

WEIGHTS_DIR = Path(os.environ.get("SAM_WEIGHTS", Path.home() / ".cache" / "fixit" / "sam3.1"))
CHECKPOINT = "sam3.1_multiplex.pt"


class Segmenter(Protocol):
    def segment(self, media: Path, prompt: Prompt, size: tuple[int, int]) -> list[list[Found]]:
        """`media` is a JPEG folder or an MP4; `size` its (width, height). One list of objects per frame."""
        ...


def weights_present(d: Path = WEIGHTS_DIR) -> bool:
    return (d / CHECKPOINT).is_file()


def crop_found(object_id: int, conf: float, full: Any) -> Found | None:
    """A full-frame boolean mask (H x W, array-like) to a Found cropped to the mask's own bounds."""
    import numpy as np

    m = np.asarray(full, dtype=bool)
    rows, cols = np.flatnonzero(m.any(axis=1)), np.flatnonzero(m.any(axis=0))
    if not rows.size:
        return None
    y1, y2, x1, x2 = int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])
    return Found(object_id, (x1, y1, x2, y2), conf, m[y1:y2 + 1, x1:x2 + 1].astype(int).tolist())


def _drop_unknown_kwargs(obj: Any, name: str) -> None:
    """sam3 f1647e8: the base predictor's start_session passes offload_state_to_cpu, which SAM 3.1's multiplex
    init_state does not take (TypeError). Pass on only the arguments the method accepts."""
    import inspect

    fn = getattr(obj, name)
    params = inspect.signature(fn).parameters
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return

    def call(*args: Any, **kw: Any) -> Any:
        return fn(*args, **{k: v for k, v in kw.items() if k in params})

    setattr(obj, name, call)


class Sam31:
    """The real model. Loaded once, on the GPU; ~3.5 GB of weights beside Glimmer on the 5090."""

    def __init__(self, weights: Path = WEIGHTS_DIR) -> None:
        if not weights_present(weights):
            raise FileNotFoundError(f"no {CHECKPOINT} in {weights}; run python -m local.sam_server.fetch")
        from sam3.model_builder import build_sam3_predictor  # the GPU env only

        # use_fa3 defaults on, but flash-attention 3 is Hopper-only and not in the env: the 5090 is Blackwell
        self.predictor = build_sam3_predictor(checkpoint_path=str(weights / CHECKPOINT), version="sam3.1",
                                              use_fa3=False)
        _drop_unknown_kwargs(self.predictor.model, "init_state")

    def segment(self, media: Path, prompt: Prompt, size: tuple[int, int]) -> list[list[Found]]:
        import torch

        # sam3's own demos run under bf16 autocast; without it the ViT's float input meets bf16 weights (RuntimeError)
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            return self._segment(media, prompt, size)

    def _segment(self, media: Path, prompt: Prompt, size: tuple[int, int]) -> list[list[Found]]:
        width, height = size
        p = self.predictor
        sid = p.handle_request({"type": "start_session", "resource_path": str(media)})["session_id"]
        try:
            req: dict[str, Any] = {"type": "add_prompt", "session_id": sid, "frame_index": 0}
            if prompt.box is not None:
                x1, y1, x2, y2 = prompt.box
                req["bounding_boxes"] = [[x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height]]
                req["bounding_box_labels"] = [1]
            else:
                req["text"] = prompt.text
            # add_prompt answers frame 0 itself; propagate_in_video then yields frame 0 again, empty (sam3
            # f1647e8, measured), so frame 0 is add_prompt's and propagation only adds the later frames
            frames: dict[int, list[Found]] = {0: _founds(p.handle_request(req)["outputs"])}
            if _multi_frame(media):
                for out in p.handle_stream_request({"type": "propagate_in_video", "session_id": sid}):
                    i = int(out["frame_index"])
                    if i > 0 or not frames[0]:
                        frames[i] = _founds(out["outputs"])
            return [frames.get(i, []) for i in range(max(frames) + 1)]
        finally:
            p.handle_request({"type": "close_session", "session_id": sid})


def _founds(o: dict[str, Any]) -> list[Found]:
    found = []
    for oid, prob, m in zip(o["out_obj_ids"], o["out_probs"], o["out_binary_masks"], strict=True):
        f = crop_found(int(oid), float(prob), m)
        if f is not None:
            found.append(f)
    return found


def _multi_frame(media: Path) -> bool:
    return media.is_file() or len(list(media.glob("*.jpg"))) > 1


@contextmanager
def as_media(jpegs: Sequence[bytes] = (), mp4: bytes | None = None) -> Iterator[Path]:
    """Write the request's frames where SAM can read them: a JPEG folder (00000.jpg, ...) or one MP4 file."""
    with tempfile.TemporaryDirectory(prefix="fixit-sam-") as d:
        if mp4 is not None:
            path = Path(d) / "clip.mp4"
            path.write_bytes(mp4)
            yield path
            return
        for i, jpeg in enumerate(jpegs):
            (Path(d) / f"{i:05d}.jpg").write_bytes(jpeg)
        yield Path(d)
