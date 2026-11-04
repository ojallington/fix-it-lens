"""Power off is evidence, not a word. A `power: off` step passes only when the camera sees the plug out of the socket
(SAM 3.1 on "plug" and "socket"); where the appliance has a power entity in Home Assistant, it must also read off
(0 W from a power sensor, else 'off' from a binary one). Code decides; no model says the power is off."""

from __future__ import annotations

from dataclasses import dataclass

import calls
from app.frames import StoredFrame
from app.geometry import Box, box_overlap
from app.models import Models
from app.ports import HomeAssistant
from app.verdict import PowerWitness, Result

PLUG_IN = 0.3  # a plug box overlapping a socket box by this much is in the socket
MIN_CONF = 0.6
ZERO_W = 0.5


@dataclass(frozen=True)
class PowerEvidence:
    camera: Result
    ha: Result | None  # None: no power entity, the camera alone decides
    rail: str
    ha_w: float | None = None  # the watts HA read, for the rules' gate; None when unavailable or no W sensor
    box: Box | None = None  # around every plug and socket SAM found: where a judge's evidence for "unplugged" lies

    def witness(self) -> PowerWitness:
        if self.camera != "done":
            return PowerWitness(self.camera, self.rail)
        if self.ha is not None and self.ha != "done":
            return PowerWitness(self.ha, self.rail)
        return PowerWitness("done", self.rail)


def _boxes(res: calls.SamResult) -> list[Box]:
    return [res.box_1000(o) for o in res.objects if (o.conf or 0) >= MIN_CONF and o.frame in (None, 0)]


def camera_says(plugs: list[Box], sockets: list[Box]) -> tuple[Result, str]:
    if not sockets:
        return "cant_see", "no socket in view"
    if any(box_overlap(p, s) >= PLUG_IN for p in plugs for s in sockets):
        return "not_done", "plug in the socket"
    if not plugs:
        return "cant_see", "socket empty, but no plug in view"
    return "done", "plug out"


async def power_evidence(models: Models, frame: StoredFrame, ha: HomeAssistant, alias: str | None) -> PowerEvidence:
    found: list[Box] = []
    try:
        plug, _ = await models.sam_image(frame, "plug")
        socket, _ = await models.sam_image(frame, "socket")
        plugs, sockets = _boxes(plug), _boxes(socket)
        found = plugs + sockets
        camera, why = camera_says(plugs, sockets)
    except calls.CallError:
        camera, why = "cant_see", "couldn't look"
    ha_result: Result | None = None
    watts: float | None = None
    rail = f"camera: {why}"
    if alias is not None:
        watts = await ha.power_w(alias)
        if watts is not None:
            ha_result = "done" if watts <= ZERO_W else "not_done"
            rail += f", power {watts:g} W"
        else:
            state = await ha.power_state(alias)
            if state is not None:
                ha_result = {"off": "done", "on": "not_done"}.get(state, "cant_see")  # type: ignore[assignment]
                rail += f", power {state}"
    box = (min(b[0] for b in found), min(b[1] for b in found), max(b[2] for b in found),
           max(b[3] for b in found)) if found else None
    return PowerEvidence(camera, ha_result, rail, watts, box)
