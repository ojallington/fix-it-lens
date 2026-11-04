"""The technician note. After a stop, one tap writes it: the appliance, its fault history, the steps the checker
passed, why the session stopped, and the photos the user picked. For a renter it marks the jobs the country's rules
file lists as the landlord's.

The note is built from the session record, which never holds a held sentence (a G0 hold is logged as its rule id
and a hash). Every line is put through G0 again before it goes in, and a line that does not pass is left out: the
evidence of a stop is the model's own words, and history can hold the user's.

Nothing is sent from here. The phone's share sheet sends the note when the user chooses to.
"""

from __future__ import annotations

from typing import Any

import rules
from app.recorder import Recording, safe_id
from contracts.models.card import Card

HEAD = {
    "en": {"title": "Repair note", "appliance": "Appliance", "stopped": "Stopped", "done": "Steps done (checked)",
           "none": "none", "history": "History", "landlord": "For the landlord", "photos": "Photos",
           "job": "Job", "date": "Date", "ours": "our step, not from the manual: done on your word"},
    "nl": {"title": "Notitie voor de monteur", "appliance": "Apparaat", "stopped": "Gestopt",
           "done": "Gedane stappen (gecontroleerd)", "none": "geen", "history": "Geschiedenis",
           "landlord": "Voor de verhuurder", "photos": "Foto's", "job": "Klus", "date": "Datum",
           "ours": "onze stap, niet uit de handleiding: gedaan op jouw woord"},
}


def _text(d: Any, lang: str) -> str:
    root = getattr(d, "root", d)
    if isinstance(root, dict):
        return str(root.get(lang) or root.get("en") or next(iter(root.values()), ""))
    return str(root)


def build_note(rec: Recording, card: Card | None, *, photo_frame_ids: list[str], renter: bool,
               history: list[dict[str, Any]] | None = None, alias: str | None = None) -> dict[str, Any]:
    meta = rec.meta
    lang = meta.lang if meta.lang in HEAD else "en"
    country = meta.country or "nl"
    h = HEAD[lang]
    s2c = rec.s2c()
    held = 0

    def ok(line: str) -> bool:
        nonlocal held
        if isinstance(rules.check_sentence(line, lang, card, None, country=country), rules.Pass):
            return True
        held += 1
        return False

    lines: list[str] = [f"{h['title']} · {meta.started_at.date().isoformat()}"]
    if card is not None:
        lines.append(f"{h['appliance']}: {card.brand} {card.model}" + (f" ({alias})" if alias else ""))
    job = next((j for j in card.jobs if j.id == meta.job_id), None) if card and meta.job_id else None
    if job is not None:
        lines.append(f"{h['job']}: {_text(job.title, lang)}")

    stops = [m["state"] for m in s2c if m["type"] == "safety" and m["state"]["state"] == "STOP"]
    for st in stops[-1:]:
        lines.append(f"{h['stopped']}: {st['rule_id']}")
        evidence = str(st.get("evidence") or "").strip()
        if evidence and ok(evidence):
            lines.append(f"  {evidence}")

    steps = {s.id: s for s in job.steps} if job is not None else {}
    done = [m["verdict"]["step_id"] for m in s2c if m["type"] == "verdict" and m["verdict"]["result"] == "done"]
    lines.append(f"{h['done']}:")
    shown = [f"  - {_text(steps[sid].text, lang)}" + (f" ({h['ours']})" if steps[sid].origin == "fixit" else "")
             for sid in dict.fromkeys(done) if sid in steps]
    shown = [x for x in shown if ok(x.strip()[2:])]
    lines += shown or [f"  {h['none']}"]

    if history:
        lines.append(f"{h['history']}:")
        for e in history:
            line = f"{e.get('on_date', '')} {e.get('kind', '')}" + (f": {e['text']}" if e.get("text") else "")
            if ok(str(e.get("text") or "")) or not e.get("text"):
                lines.append(f"  - {line.strip()}")

    landlord: list[str] = []
    if renter and card is not None:
        marked = rules.landlord_jobs(card, country)
        landlord = list(marked)
        if landlord:
            lines.append(f"{h['landlord']}:")
            for j in card.jobs:
                if j.id in marked:
                    rid = marked[j.id]
                    lines.append(f"  - {_text(j.title, lang)}" + (f" ({rid})" if rid else ""))

    frames = {x["msg"]["frame_id"] for x in rec.lines if x["dir"] == "c2s" and x["kind"] == "frame"}
    photos = [f"/api/v1/replay/{safe_id(meta.session_id)}/frames/{safe_id(fid)}.jpg"
              for fid in dict.fromkeys(photo_frame_ids) if fid in frames and rec.frame_bytes(fid) is not None]
    if photos:
        lines.append(f"{h['photos']}: {len(photos)}")
    return {"text": "\n".join(lines), "photos": photos, "landlord_jobs": landlord, "_held": held}
