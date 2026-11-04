"""Named unions that codegen collapses away (`--collapse-root-models`). Hand-written; a test checks each one
against its schema's oneOf, so they cannot drift."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from contracts.models import safety, ws

SafetyState = Annotated[safety.Ok | safety.Hold | safety.Stop, Field(discriminator="state")]
C2S = Annotated[
    ws.Hello | ws.Frame | ws.G1 | ws.Utterance | ws.Tap | ws.JobReady | ws.View | ws.PlatePhoto
    | ws.Ping | ws.Timings,
    Field(discriminator="type"),
]
S2C = Annotated[
    ws.HelloOk | ws.Outline | ws.ZoneMsg | ws.JobPreview | ws.StepMsg | ws.Checking | ws.VerdictMsg | ws.Sentence
    | ws.Held | ws.SafetyMsg | ws.Rail | ws.Notice | ws.Replay | ws.Pong | ws.PlateRead,
    Field(discriminator="type"),
]
Result = Literal["done", "not_done", "cant_see"]

safety_state: TypeAdapter[safety.Ok | safety.Hold | safety.Stop] = TypeAdapter(SafetyState)
c2s: TypeAdapter[
    ws.Hello | ws.Frame | ws.G1 | ws.Utterance | ws.Tap | ws.JobReady | ws.View | ws.PlatePhoto
    | ws.Ping | ws.Timings
] = TypeAdapter(C2S)
s2c: TypeAdapter[
    ws.HelloOk | ws.Outline | ws.ZoneMsg | ws.JobPreview | ws.StepMsg | ws.Checking | ws.VerdictMsg | ws.Sentence
    | ws.Held | ws.SafetyMsg | ws.Rail | ws.Notice | ws.Replay | ws.Pong | ws.PlateRead
] = TypeAdapter(S2C)
