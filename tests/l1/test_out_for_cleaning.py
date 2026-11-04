"""A step about cleaning a part that comes out (rinse the filter): the person shows the part out of its seat, and the
check reads it there. The judge is told so, the part is outlined where it is now, and SAM tracks it there; a step
that ties the part to its seat (an anchor) or is not about cleaning is checked in place, as before."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import calls
from app import judge
from contracts.models.card import Card
from tests.l1.helpers import frame
from tests.l1.test_session import acme, session
from tests.l1.test_stepcheck import Judge

SEAT = (200, 200, 400, 400)
HAND = (600, 600, 800, 800)


def square(b: tuple[int, int, int, int]) -> str:
    return f"M {b[0]} {b[1]} L {b[2]} {b[1]} L {b[2]} {b[3]} L {b[0]} {b[3]} Z"


def found(b: tuple[int, int, int, int], frames: int = 1) -> calls.SamResult:
    return calls.SamResult(1000, 1000, [calls.SamObject("o1", b, 0.9, square(b), i) for i in range(frames)], "",
                           5.0, 0.002, "0" * 64)


def card(text: dict[str, str], anchor: str | None = None) -> Card:
    data = acme().model_dump(mode="json")
    for st in data["jobs"][0]["steps"]:
        if st["id"] == "refit_filter":
            st["text"] = text
            st["done_when"][0]["text"] = {"en": "The filter mesh is clean"}
            st["witness"]["anchor"] = anchor
            st["zones"], st["zone_rings"] = [], []  # no red zone to outline first: here only the filter matters
    return Card.model_validate(data)


def step(c: Card) -> Any:
    return next(s for s in c.jobs[0].steps if s.id == "refit_filter")


def test_which_steps_take_the_part_out_of_its_seat() -> None:
    assert judge.out_for_cleaning(step(card({"en": "Rinse the filter under the tap."})))
    assert judge.out_for_cleaning(step(card({"nl": "Spoel het filter af onder de kraan."})))
    assert judge.out_for_cleaning(step(card({"en": "Clean the fine filter."})))
    assert not judge.out_for_cleaning(step(acme()))  # screw the filter back in: in its seat
    assert not judge.out_for_cleaning(step(card({"en": "Rinse the filter under the tap."}, anchor="filter_flap")))


class Moved(Judge):
    """SAM segments what is in a box prompt; by text it finds the filter where it is now, in the hand."""

    def __init__(self, point: list[int]) -> None:
        super().__init__(point)
        self.where = SEAT

    async def sam_image(self, frame: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        self.called.append("sam_image")
        return found(self.where if isinstance(prompt, str) else tuple(prompt))  # type: ignore[arg-type]

    async def sam_video(self, clip: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        self.called.append("sam_video")
        return found(self.where if isinstance(prompt, str) else tuple(prompt), kw.get("frames", 20))  # type: ignore[arg-type]

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        if role == "locate":
            self.called.append(role)
            return calls.Response(role, "m", "completed", '{"found": false, "cue_seen": false, "box": null, '
                                  '"note": ""}', [], [], calls.Usage(), 500.0, None, 0.0, "3" * 64, 1, "c")
        return await super().respond(role, input, **kw)


async def checked(tmp_path: Path, c: Card) -> tuple[Any, Moved]:
    client = Moved([700, 700])  # the judge points at the filter in the hand
    s = session(tmp_path, client)
    s.card = c
    s.start()
    for i in range(6):
        s.frames.add(frame(i, i * 100))
    s.power_off_seen = True
    await s.begin_step(2)
    await s.idle()  # the step's outline comes after the step, off the worker
    assert s.run is not None and s.run.step.id == "refit_filter"
    assert s.regions["debris_filter"].box == SEAT  # outlined in its seat when the step began
    client.where = HAND  # the person takes it out and holds it up, rinsed
    v = await s.check("tap done")
    await s.close()
    return v, client


async def test_a_rinsed_filter_shown_in_the_hand_passes(tmp_path: Path) -> None:
    v, client = await checked(tmp_path, card({"en": "Rinse the filter under the tap."}))
    assert v is not None and v.result == "done"
    told = [x["text"] for x in client.inputs[-1][1]["content"] if x.get("type") == "input_text"]
    assert judge.OUT_FOR_CLEANING in told


async def test_a_seated_step_is_still_read_at_the_seat(tmp_path: Path) -> None:
    v, client = await checked(tmp_path, card({"en": "Rinse the filter under the tap."}, anchor="filter_flap"))
    assert v is not None and v.result != "done"  # the point in the hand is off the part's seat
    told = [x["text"] for x in client.inputs[-1][1]["content"] if x.get("type") == "input_text"]
    assert judge.OUT_FOR_CLEANING not in told
