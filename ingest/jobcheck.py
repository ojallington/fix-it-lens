"""The whole-job check: what code can tell about a job as a whole before it is shown, beyond each quote being on its
page. A job gives the appliance back in its normal state, does not send the person to steps outside itself,
carries the precondition or warning that heads the manual's text where it starts, and says each thing once.

- leaves_state: the job unplugs the appliance or closes its water supply and never undoes it; on a boiler or a
  fridge, whose normal state is on, also switching it off. The state at the start is read from the first action on
  that pair (a job that first opens a tap starts with it closed). A part the job takes out of the machine (removes,
  unscrews, pulls out) and never refits fails: the appliance is not whole again. A part it only opens (a flap, a
  lid) fails when the manual's own text right after the job goes on to close it: the job was cut short. Refitting
  "in reverse order" or "all the parts" puts back everything taken. A part the job itself first connects (a filling
  hose) starts off the machine, and a step that says not to remove a part ("do not remove the hose yet") removes
  nothing.
- refers_outside: a step the person hears points at a procedure, page or chapter outside the job ("follow the
  emergency drain procedure"), or its quote says to repeat the procedure. A loop inside the job ("repeat until all
  the water has drained", "repeat the previous step") is the job's own; a step that names another job on the card
  is that job's link.
- precondition_missing: a precondition or warning heading ("Vereiste:", "Voordat je de deur opent", "WAARSCHUWING")
  starts a line in the text just above a place where the job enters the manual, or between two of its steps, and
  no quote of the job comes from that block.
- duplicate_steps: two steps say the same thing; code keeps the first and drops the later one (never the job).

Pure code on the English step text and the extracted page text. The model only proposes a repaired job, which must
pass the citation check and this check again.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import dataclass
from typing import NamedTuple

from ingest.citation import locate, norm


@dataclass(frozen=True)
class Step:
    text_en: str
    quote: str
    page: int


@dataclass(frozen=True)
class Failure:
    code: str  # leaves_state | refers_outside | precondition_missing | step_skipped | warning_dropped | ...
    detail: str  # code-written from fixed words and step numbers: safe for logs and the rail

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


# Kinds whose normal state is on: switched off, they stop heating a house or cooling food. A washer, dishwasher or
# oven left switched off is as it should be; any appliance left unplugged or with its water closed is not.
ALWAYS_ON = frozenset({"boiler", "fridge"})

# Parts a person takes off or opens for upkeep. Not the door: opening it is often what the job is for.
PARTS = frozenset(["filter", "flap", "lid", "cover", "cap", "hatch", "panel", "plinth", "kickplate", "drawer",
                   "dispenser", "tray", "insert", "syphon", "siphon", "sieve", "arm", "basket", "cartridge", "screen",
                   "grille", "rack", "holder", "shelf", "support", "runner", "pane", "glass", "hose", "seal"])
_ALL_BACK = re.compile(r"\breverse order\b|\breassemble\b|\ball (?:the )?(?:parts|pieces)\b")

_CLAUSE = r"(?:^|[.;:,]\s*|\b(?:then|first|and|now|again|afterwards|after that)\s+)"  # the verb starts a clause
_NP = r"(?:the |its |a |your )?(?:[\w-]+ ){0,3}?"
# Back on the mains: only plugging in or reconnecting undoes an unplug ("switch it back on" leaves it unplugged).
_PLUG_IN = (r"\bplug " + _NP + r"(?:back )?in\b"
            r"|\bput " + _NP + r"plug (?:back )?in(?:to)?\b"
            r"|\breconnect (?:the )?(?:appliance|machine|power|mains|plug)\b"
            r"|\brestore the (?:power|mains)")
_BACK_ON = (r"\b(?:switch|turn|power)(?:es|s|ing|ed)? " + _NP + r"(?:back )?on\b|" + _PLUG_IN
            + r"|\b(?:back )?into (?:use|operation|service)\b")
PAIRS: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {
    "mains": (
        re.compile(r"\bunplug|\bdisconnect " + _NP + r"(?:from )?(?:the )?(?:mains|power|electricity|plug|socket)"
                   r"|\bpull (?:out )?the (?:mains )?plug|\bisolate " + _NP + r"(?:from )?(?:the )?(?:mains|power)"
                   r"|\b(?:take|remove) the plug|\boff at the (?:power supply|mains|socket)"),
        re.compile(_PLUG_IN),
    ),
    "power": (
        re.compile(_CLAUSE + r"(?:switch|turn|power)(?:es|s|ing)? " + _NP + r"off\b|\bswitch (?:it|the \w+) off\b"),
        re.compile(_BACK_ON),
    ),
    "water_supply": (
        re.compile(_CLAUSE + r"(?:close|shut|turn off) (?:the )?(?:water |supply |inlet |main )+(?:tap|valve|supply)"
                   r"|\bturn off the water\b|\bturn the (?:water )?(?:tap|valve) off\b"),
        re.compile(_CLAUSE + r"(?:open|turn on) (?:the )?(?:water |supply |inlet |main )+(?:tap|valve|supply)"
                   r"|\bturn (?:on the water|the water (?:back )?on)\b"
                   r"|\bturn the (?:water )?(?:tap|valve) (?:back )?on\b"),
    ),
}
_TAKE = re.compile(r"\b(?:remove|take(?: out| off)?|pull(?: out| off)?|lift(?: out| off)?|unscrew|open|undo|detach|"
                   r"unclip|pry open|prise open|tilt)(?= ((?:[\w-]+ ?){1,5}))")
_PUT = re.compile(r"\b(?:refit|replace|reinsert|re-insert|reinstall|re-install|put|insert|fit|screw|close|refasten|"
                  r"clip|push|slide|place|attach|reattach|reassemble|mount|click|reconnect|re-connect|connect)\w*"
                  r"(?= ((?:[\w-]+ ?){1,8}))")  # longer than a removal: "put first the filter and then the seal back"
# the noun phrase is looked ahead at, not taken: "put it back and close the flap" still sees "close"
_NP_END = re.compile(r"\b(?:and|with|by|until|from|into|onto|to|so|on|in|under|at|then|if|for|out|off|back)\b")
# A verb the step says not to do is no action: "do not remove the hose yet". Only a "do not" or "never" that opens
# its clause and stands right before the verb: "do not spill; remove the filter" still removes it.
_NOT = re.compile(r"(?:^|[.;:!?,]\s*|\b(?:but|and|then)\s+)(?:please\s+)?(?:do not|don't|don’t|never)\s+(?:yet\s+)?$")
# A part the job brings to the machine itself, first connected or attached (the filling hose), starts off it:
# taking that same part away again, named the same way, gives the appliance back as it was.
_BRING = re.compile(r"\b(?:connect|attach)(?:s|ing)?\b(?= ((?:[\w-]+ ?){1,8}))")
# A job that first connects a hose to the water tap (topping up a boiler) starts with that tap closed: closing that
# tap once the filling is done gives it back as it was. Any other valve or supply it closes still counts.
_TO_TAP = re.compile(r"\b(?:connect|attach)(?:s|ing)? " + _NP + r"hose (?:[\w-]+ ){0,3}?to (?:the )?(?:water )?tap\b")
_JUST_TAP = re.compile(r"\btap\b(?![^.;!?]*\b(?:valve|supply|main|inlet|water|tap)\b)")
_OTHER_WATER = re.compile(r"\b(?:valve|supply|main|inlet)\b")


# A connect brings a part or starts a tap only as a bare order that opens its own clause and that nothing later in
# that clause takes back: "connect the hose to the tap", "then attach the cover". Any other wording ("no need to
# attach", "avoid connecting", "connect it not yet") brings nothing, so the check refuses; a list of negations
# would always miss one.
_OPENS = re.compile(r"(?:^|[.;!]|\b(?:then|and|now|next|first),?)\s*$")  # not a colon: "the label reads: attach"
_ANY_NOT = re.compile(r"\b(?:not|never|no|nor|neither|cannot|niet|nooit|geen|zonder|nicht|niemals|kein\w*|ne|pas)\b"
                      r"|n['\u2019\u00b4`]t\b")
_TAKES_BACK = re.compile(r"\b(?:not|never|no|nor|yet|later|until|unless|before|after|instead|without|niet|nooit|"
                         r"geen|nog|nicht|niemals|pas|if|when|once|soon|only|eventually|optional\w*|needed|"
                         r"necessary|straks|eerst)\b|n['\u2019\u00b4`]t\b")
# Earlier in the step, a condition makes the connect one that may not happen: "if needed: attach the cover".
# A job whose steps point at other steps ("steps 2 and 3 are not needed") may skip its own connect: it brings nothing.
_STEP_REF = re.compile(r"\b(?:steps?|stap(?:pen)?|schritte?|[ée]tapes?) \d")
_IF = re.compile(r"\b(?:if|when|once|optional\w*|als|indien|eerst|wenn|falls|si)\b")


def _said_not(text: str, at: int) -> bool:
    return _NOT.search(text, 0, at) is not None


def _bare_order(text: str, m: re.Match[str]) -> bool:
    """Whether a connect or attach is a bare order of its own: the plain verb opening its clause, no negation
    or condition anywhere before it in the step ("geen: attach", "if needed: attach"), no question in the step, and
    no negation, deferral or condition in what follows it up to the next "and", "then" or stop."""
    rest = re.split(r"[.;:!?\n]|\b(?:and|then)\b", text[m.end():], maxsplit=1)[0]
    return (re.match(r"(?:connect|attach)\b", m.group(0)) is not None
            and _OPENS.search(text, 0, m.start()) is not None
            and not _ANY_NOT.search(text, 0, m.start()) and not _IF.search(text, 0, m.start())
            and "?" not in text and not _TAKES_BACK.search(rest))


def _just_tap(text: str, m: re.Match[str]) -> bool:
    """Whether a water hit opens or closes the tap alone: "close the water tap", not "close the tap and the valve"."""
    return (not _OTHER_WATER.search(m.group(0)) and (t := _JUST_TAP.search(text, m.start())) is not None
            and t.start() < m.end())


def _bare_nps(phrase: str) -> list[str]:
    """The noun phrases a verb takes, word for word, while each is just an article and a part ("the filling hose and
    the cover"), article and all; none past the first that is anything else ("the hose and remove the filter" →
    the hose)."""
    np = re.split(r"[,;:.!?]|\b(?:with|by|until|from|into|onto|to|so|on|in|under|at|then|if|for|out|off|back|or|of)\b",
                  phrase, maxsplit=1)[0]
    out = []
    for conjunct in np.split(" and "):
        m = re.fullmatch(r"\s*((?:the|a|an|its|your) (?:[a-z-]+ ){0,3}[a-z-]+)\s*", conjunct)
        if m is None or m.group(1).split()[-1] not in PARTS:
            break
        out.append(m.group(1))
    return out


def _takes(text: str, verbs: re.Pattern[str], parts_of: Callable[[str], list[str]], brought: list[str],
           taken: Collection[str], bring: bool) -> Iterator[tuple[re.Match[str], list[str]]]:
    """Each removal in a step with the parts it takes, less a removal the step says not to do and less each part
    the job brought earlier under the very same name. A bring counts once: one hose connected lets one hose go."""
    brings = [(m.start(), np) for m in _BRING.finditer(text) if bring and _bare_order(text, m)
              for np in _bare_nps(m.group(1)) if np.split()[-1] not in taken]
    for m in verbs.finditer(text):
        brought += [np for at, np in brings if at < m.start()]
        brings = [(at, np) for at, np in brings if at > m.start()]
        if _said_not(text, m.start()):
            continue
        parts = parts_of(_np(m))
        for np in _bare_nps(_np(m)):
            if np in brought and np.split()[-1] in parts:
                brought.remove(np)
                parts.remove(np.split()[-1])
        yield m, parts
    brought += [np for _, np in brings]
# The manual goes on to put a part back (Dutch, English, German, French): what a job cut short leaves out.
_PUT_BACK = re.compile(r"\b(?:terug|sluit|dicht|monteer|vastdraaien|vast|aanbrengen|refit|replace|reinsert|put back|"
                       r"close|reinstall|wieder|schliessen|schließen|einsetzen|remettez|refermez|replacez)\b")
_HEADING = re.compile(r"\b\d{1,2}(?:\.\d{1,2})+ [a-z]")
AFTER = 300  # how far past the job's last quote the manual's own procedure still counts, in characters

_OUTSIDE = re.compile(
    r"\brepeat (?:the|this|that) (?:whole |above |same )?(?:procedure|process|steps|operation)\b(?! until)"
    r"|\bfollow(?:ing)? (?:the |this )?(?:[\w-]+ ){0,4}(?:procedure|section|chapter)\b"
    r"|\b(?:see|refer to|go to|consult) (?:the )?(?:section|chapter|page|paragraph)\b"
    r"|\bas described (?:in|under|on|above|below)\b",
    re.IGNORECASE,
)
_REPEAT = re.compile(r"\b(?:herhaal|repeat) (?:de|the|deze|this) (?:hele |whole )?(?:procedure|handeling|steps|"
                     r"stappen)\b(?! (?:until|tot|totdat|in (?:de )?omgekeerde|in (?:the )?reverse))", re.IGNORECASE)

MARKERS = re.compile(
    r"(?:^|\n)\s*(vereiste|voorwaarde|voordat (?:je|u)|before (?:you|opening|starting|removing|cleaning)|requirement|"
    r"let op|waarschuwing|voorzichtig|gevaar|warning|caution|danger|achtung|vorsicht|warnung|voraussetzung|"
    r"avertissement)\b"
)
ABOVE = 500  # how far above a quote a heading still belongs to its block, in characters


def _parts_in(phrase: str) -> list[str]:
    """The part a verb's noun phrase names ("the plinth flap" → flap), singular; [] when it names none."""
    np = _NP_END.split(phrase, maxsplit=1)[0]
    words = [re.sub(r"s$", "", w) for w in re.findall(r"[a-z]+", np)]
    return [w for w in words if w in PARTS][-1:]


def _after(steps: Sequence[Step], pages: Sequence[str]) -> str:
    """The manual's text right after the job's last quote, up to the next numbered heading."""
    last = steps[-1]
    if not 1 <= last.page <= len(pages) or (hit := locate(pages[last.page - 1], last.quote)) is None:
        return ""
    text = hit[0][hit[2]:hit[2] + AFTER]
    return text[:m.start()] if (m := _HEADING.search(text)) else text


def leaves_state(steps: Sequence[Step], pages: Sequence[str], kind: str) -> list[Failure]:
    out = []
    texts = [s.text_en.lower() for s in steps]
    bring = not any(_STEP_REF.search(t) for t in texts)
    for name, (away, back) in PAIRS.items():
        if name == "power" and kind not in ALWAYS_ON:
            continue
        hits = [(e, _just_tap(t, m)) for t in texts for _, e, m in sorted(
            [(m.start(), "away", m) for m in away.finditer(t)] + [(m.start(), "back", m) for m in back.finditer(t)]
            + [(m.start(), "start", m) for m in (_TO_TAP.finditer(t) if name == "water_supply" else ())
               if bring and _bare_order(t, m)])]
        # a hose connected to the tap, as the first word on the water, says that tap starts closed: its own opening
        # and closing then leave nothing off; any other tap, valve or supply still does
        started = bool(hits) and hits[0][0] == "start"
        events = [e for e, tap in hits if e != "start" and not (started and tap)]
        if events and events[0] == "away" and events[-1] == "away":
            out.append(Failure("leaves_state", f"{name} off, never back on"))
    taken: dict[str, int] = {}
    opened: set[str] = set()  # opened, not taken out: only the manual's own text after the job says it must close
    brought: list[str] = []
    for i, t in enumerate(texts):
        for m, parts in _takes(t, _TAKE, _parts_in, brought, taken, bring):
            for part in parts:
                taken.setdefault(part, i)
                if m.group(0).startswith(("open", "tilt", "pry open", "prise open")):
                    opened.add(part)
        for m in _PUT.finditer(t):
            named = _all_parts_in(m.group(1))  # a refit puts back every part it names
            if not named and taken and re.match(r"(?:it|them)\b", m.group(1)):  # "put it back": the last one taken
                named = [max(taken, key=lambda p: taken[p])]
            for part in named:
                taken.pop(part, None)
        if _ALL_BACK.search(t):
            taken.clear()
    if taken and steps:
        cut = bool(_PUT_BACK.search(_after(steps, pages)))
        out += [Failure("leaves_state", f"{part} removed or opened at step {i + 1}; "
                        + ("the manual refits it after the job" if cut else "never refitted"))
                for part, i in taken.items() if cut or part not in opened]
    return out


_WORD = re.compile(r"[a-z]+")
_STOP = frozenset(["the", "a", "an", "and", "to", "of", "with", "in", "on", "it", "its", "your", "you", "for", "from",
                   "then", "if", "is", "be", "at", "by", "or", "this", "that"])


def _content(text: str) -> frozenset[str]:
    return frozenset(w for w in _WORD.findall(text.lower()) if w not in _STOP)


def _stem(w: str) -> str:
    return re.sub(r"(?:ing|es|s)$", "", w)


def refers_outside(steps: Sequence[Step], others: Sequence[str] = ()) -> list[Failure]:
    known = [{_stem(w) for w in _content(t)} for t in others]
    out = []
    for i, s in enumerate(steps):
        m = _OUTSIDE.search(s.text_en) or _REPEAT.search(s.quote)
        if not m:
            continue
        said = {_stem(w) for w in _content(s.text_en)}
        if any(len(said & k) >= 2 for k in known):
            continue
        out.append(Failure("refers_outside", f"step {i + 1}: \"{m.group(0).strip().lower()}\""))
    return out


def _spans(steps: Sequence[Step], pages: Sequence[str]) -> list[tuple[int, str, int, int] | None]:
    """Each step's quote on its page: (page, the page's normalised text, start, end), or None."""
    out: list[tuple[int, str, int, int] | None] = []
    for s in steps:
        hit = locate(pages[s.page - 1], s.quote) if 1 <= s.page <= len(pages) else None
        out.append((s.page, *hit) if hit else None)
    return out


_WARNING = re.compile(r"waarschuwing|warning|caution|danger|gevaar|voorzichtig|achtung|vorsicht|warnung|"
                      r"avertissement|let op")


def _just_below(marker: str, gap: str) -> bool:
    """The entry step's own quote carries the heading above it when nothing is between them, or, under a warning,
    only its one short title line ("WAARSCHUWING / Kans op letsel! / <quote>"). A requirement has no title line:
    the words after "Vereiste:" are the requirement, and a step that starts below them leaves them out."""
    g = gap.strip()
    return len(g) <= 2 or (bool(_WARNING.fullmatch(marker.lower())) and len(g) <= 40 and "\n" not in g)


def precondition_missing(steps: Sequence[Step], pages: Sequence[str],
                         markers: re.Pattern[str] = MARKERS) -> list[Failure]:
    """At each place the job enters the manual's text (its first step, and a step that jumps to another page or
    far down the page), a heading above it that none of the job's quotes comes from; between two steps that follow
    each other on the page, a heading in the text between them."""
    spans = _spans(steps, pages)
    out = []
    prev = None
    for i, sp in enumerate(spans):
        if sp is None:
            prev = None
            continue
        page, text, at, end = sp
        inside = prev is not None and prev[0] == page and 0 <= at - prev[3] <= ABOVE
        # inside the job, the text between the last step's quote and this one ("WAARSCHUWING" between two steps)
        from_ = prev[3] if inside and prev is not None else max(0, at - ABOVE)
        prev = sp
        marks = list(markers.finditer(text[from_:at]))
        if not marks:
            continue
        # each heading's block runs to the next heading or to the step; a quote that starts in it carries it
        ends = [from_ + m.start() for m in marks[1:]] + [at]
        for m, end in zip(marks, ends, strict=True):
            block = from_ + m.start()
            if not any(o and o[0] == page and block <= o[2] <= end and o[3] > from_ + m.end()
                       and (o[2] < at or _just_below(m.group(1), text[from_ + m.end():o[2]])) for o in spans):
                out.append(Failure("precondition_missing", f"\"{m.group(1)}\" above step {i + 1} on page {page}"))
    return out


# A printed step: a line the manual opens with its step symbol ("■", "") or a number ("2.", "3)"), with the lines
# under it up to the next such line. A page number ends it.
_ITEM = re.compile(r"^(?:[^\w\s\"'(\[„“]+|\d{1,2}[.)])\s*(?=[^\W\d_])")
_PAGE_NO = re.compile(r"\d{1,3}")
_ITEM_MIN = 12  # shorter is a pointer or a label ("~ Blz. 38"), not an instruction
# A fault table's row name ("Storing F", "E21"): what is wrong, not a step to take.
_ROW_LABEL = re.compile(r"(?:storing|fault|error|fehler|störung|panne|code)\b|[a-z]{0,2}\d{1,3}\b")
# A legend row ("w lit: the child lock is on") or a note under its own label: a word or three before a colon.
_LEGEND = re.compile(r"[^\s:]+(?: [^\s:]+){0,2}:")


_SUBHEAD = re.compile(r"[A-ZÀ-Þ][^\n]{1,43}(?<![.,;-])")  # "Aanwijzingen", "Buitenkant van het apparaat/"


class _Item(NamedTuple):
    start: int
    end: int
    text: str
    group: int | None  # the list it is printed in: under a lead that ends in a colon, or under a warning line
    box: bool  # that list is a warning's
    asks: bool  # a question, as a fault table asks its causes ("Tap not opened?")
    run: bool  # a numbered list of its own ("1. ... 2. ..."): one procedure, or one cause's remedy


def _items(page: str) -> tuple[list[_Item], list[int]]:
    """The page's printed steps, and where its unnumbered sub-headings start, in the page's normalised text
    (citation.norm). A sub-heading is a short line of its own, outside a printed step, that does not end a sentence
    or run on into a lower-case line. Steps printed with one symbol under a line that ends in a colon are one list
    with that line; under a warning line and its title, the warning's. A numbered list from 1 is one list, and a
    fault's row is one with the remedy printed under it, up to a plain sentence under that remedy: the row's next
    cause, whose remedies are its own."""
    lines = [x.strip() for x in page.split("\n")]
    found: list[tuple[str, str, int | None]] = []
    boxes: set[int] = set()
    runs: set[int] = set()
    rows: set[int] = set()
    cause = False  # a fault row's next cause, printed plain under its last remedy
    in_box, title = False, 0  # under a warning line, or its title of up to two lines when the line is bare
    cur: str | None = None
    last, mark = "", ""
    group: int | None = None
    cur_group: int | None = None
    for k, line in enumerate(lines):
        nxt = next((x for x in lines[k + 1:] if x), "")
        head = (bool(_SUBHEAD.fullmatch(line)) and not nxt[:1].islower()
                and (cur is None or cur.rstrip()[-1:] in tuple(".!?:")))
        bare = bool(_WARNING.fullmatch(line.lower().strip("!: ")))  # "WAARSCHUWING!" on its own line ends a step
        if m := _ITEM.match(line):
            sym = "#" if m.group(0).strip()[:1].isdigit() else m.group(0).strip()
            before = (cur if cur is not None else last).rstrip()
            lead = before.endswith(":")
            if lead or (cur is None and in_box):
                group = k
                if not lead:
                    boxes.add(k)
            elif (sym == "#" and int(m.group(0).strip()[:-1]) == 1) or _ROW_LABEL.match(norm(line[m.end():])):
                group = k  # a numbered list from 1, or a fault's row and the remedy under it
                runs.add(k)
                rows |= {k} if sym != "#" else set()
            elif cause:
                group = k  # the remedies under a row's next cause are that cause's
                runs.add(k)
                rows.add(k)
            elif sym != mark and mark != "row":
                group = None
            cause = False
            # a fault's row may carry its own symbol, and its remedy the step symbol under it
            mark = "row" if group == k and _ROW_LABEL.match(norm(line[m.end():])) else sym
            if cur is not None:
                found.append(("item", cur, group if lead else cur_group))
            cur, cur_group, in_box = line[m.end():], group, False
        elif (cur is not None and cur_group in rows and cur.rstrip()[-1:] == "." and line[:1].isupper()
              and not head and not bare):
            found.append(("item", cur, cur_group))
            cur, cause = None, True
        elif cur is not None and line and not head and not bare and not _PAGE_NO.fullmatch(line):
            cur += "\n" + line
        else:
            if cur is not None:
                found.append(("item", cur, cur_group))
                cur = None
            if line and not _PAGE_NO.fullmatch(line):
                group, mark = None, ""
                if in_box and title < 2 and not head and not bare:
                    title = 2 if line[-1:] in tuple(".!") else title + 1
                elif _WARNING.match(line.lower()):
                    in_box, title = True, 0 if bare else 2
                else:
                    in_box = False
            if line and head and not _PAGE_NO.fullmatch(line):
                found.append(("head", line, None))
        if line and not _PAGE_NO.fullmatch(line):
            last = line
    if cur is not None:
        found.append(("item", cur, cur_group))
    flat = norm(page, lines=True).replace("\n", " ")
    items, heads, at = [], [], 0
    for what, text, g in found:
        n = norm(text)
        if (what == "item" and len(n) < _ITEM_MIN) or not n or (pos := flat.find(n[:40], at)) < 0:
            continue
        if what == "item":
            items.append(_Item(pos, pos + len(n), n, g, g in boxes, "?" in text, g in runs))
        else:
            heads.append(pos)
        at = pos + 1
    return items, heads


def steps_skipped(steps: Sequence[Step], pages: Sequence[str]) -> list[Failure]:
    """A step the manual prints between two of the job's steps that no step of the job quotes: on the page, or
    across a page break from the end of one page to the top of the next, in either step's own block. A warning
    there is the warning check's (warnings_dropped); a block under its own sub-heading between them is another's,
    and a numbered heading between them starts another procedure. A list the job quotes from is the job's; a fault
    table's question is another cause, a numbered list or a fault's row it quotes nothing from is another
    procedure or another cause's remedy, and a legend row is not a step."""
    spans = _spans(steps, pages)
    # words printed more than once on a page ("switch it off") are the ones last before the step after them
    for i in range(len(spans) - 2, -1, -1):
        sp, nxt = spans[i], spans[i + 1]
        if sp is None or nxt is None or nxt[0] < sp[0]:
            continue
        n, flat = norm(steps[i].quote), sp[1].replace("\n", " ")
        at = [m.start() for m in re.finditer(re.escape(n), flat)] if n else []
        before = [a for a in at if nxt[0] > sp[0] or a <= nxt[2]]
        if len(at) > 1 and before:
            spans[i] = (sp[0], sp[1], before[-1], before[-1] + len(n))
    # and, when the step after them is on no page next to theirs, the first after the step before them
    for i in range(1, len(spans)):
        prev, sp = spans[i - 1], spans[i]
        nxt = spans[i + 1] if i + 1 < len(spans) else None
        if prev is None or sp is None or prev[0] != sp[0] or (nxt is not None and nxt[0] in (sp[0], sp[0] + 1)):
            continue
        n, flat = norm(steps[i].quote), sp[1].replace("\n", " ")
        after = [m.start() for m in re.finditer(re.escape(n), flat) if m.start() >= prev[3]] if n else []
        if after and len(re.findall(re.escape(n), flat)) > 1:
            spans[i] = (sp[0], sp[1], after[0], after[0] + len(n))
    items = {p: _items(pages[p - 1]) for p in {sp[0] for sp in spans if sp is not None}}
    for p in list(items):
        if p - 1 >= 1 and p - 1 not in items:
            items[p - 1] = _items(pages[p - 2])
    quotes = [q for q in (norm(st.quote) for st in steps) if q]

    def mine(page: int, it: _Item) -> bool:
        return (any(o is not None and o[0] == page and o[2] < it.end and it.start < o[3] for o in spans)
                or any(it.text.startswith(q) or q.startswith(it.text) for q in quotes))

    quoted = {(p, it.group) for p, (got, _) in items.items() for it in got if it.group is not None and mine(p, it)}
    out = []
    for i in range(1, len(spans)):
        prev, sp = spans[i - 1], spans[i]
        if prev is None or sp is None:
            continue
        # each stretch of text between the two steps, and which of its blocks are theirs: step i's runs to the
        # first sub-heading after it, step i + 1's from the last sub-heading before it
        if prev[0] == sp[0] and 0 <= sp[2] - prev[3] <= ABOVE + AFTER:
            gaps = [(sp[0], prev[3], sp[2], True, True)]
        elif sp[0] == prev[0] + 1 and len(prev[1]) - prev[3] + sp[2] <= ABOVE + AFTER:
            gaps = [(prev[0], prev[3], len(prev[1]), True, False), (sp[0], 0, sp[2], False, True)]
        else:
            continue
        if any(_HEADING.search((prev if pg == prev[0] else sp)[1][a:b]) for pg, a, b, _, _ in gaps):
            continue
        for pg, a, b, first, last in gaps:
            got, heads = items.get(pg, ([], []))
            hs = [h for h in heads if a <= h < b]
            for it in got:
                n = it.text
                theirs = not hs or (first and it.start < hs[0]) or (last and it.start > hs[-1])
                # a list the job quotes from is the job's; a numbered list it quotes nothing from is another's
                listed = (pg, it.group) in quoted
                if a <= it.start < b and theirs and not mine(pg, it) and listed == it.run and not it.box \
                        and not it.asks and not _LEGEND.match(n) and not _ROW_LABEL.match(n) \
                        and not _SAFETY.search(n) and not GATE_MARKERS.search(n) and not _RISK.search(n):
                    words = " ".join(n.split()[:4])
                    out.append(Failure("step_skipped", f"\"{words}…\" between steps {i} and {i + 1} on page {pg}"))
    return out


# The gate: the final say after every repair, which the repair is not asked about. A warning the job leaves out,
# a part it leaves open, or a plug it pulls in words the first check does not know, drops the job.
GATE_MARKERS = re.compile(
    r"(?:^|\n)[^\w\n]{0,3}(vereiste|voorwaarde|voordat (?:je|u)|before (?:you|opening|starting|removing|cleaning)|"
    r"requirement|let op|waarschuwing|voorzichtig|gevaar|attentie|attention|warning|caution|danger|important|"
    r"belangrijk|achtung|vorsicht|warnung|hinweis|voraussetzung|avertissement)\b"
)
# A sentence that warns: in the procedure's own lines above where the job enters it, or between two of its steps.
_SAFETY = re.compile(
    r"waarschuwing(?!ssignaal)|warning|caution|danger|gevaar voor|kans op|risico|let op|attentie|voorzichtig|"
    r"careful|letsel|injur|verbranding|brandwond|\bburns?\b|scald|\bheet\b|\bhete\b|\bhot\b|afkoel|cool(?:ed)? down|"
    r"(?:glas)?splinter|scherven|\bzwaar\b|\bheavy\b|overflow|overstrom|waterschade|make sure|ensure|"
    r"zorg (?:ervoor )?dat|wacht tot|wait (?:until|for)|stekker|unplug|vorsicht|verbrühung|heiß"
)
# Between two steps also any instruction not to do something: the job skipped what the manual says right there.
_WARN_NOT = re.compile(r"\b(?:niet|nooit|never|do not|don't|must not|nicht|niemals|ne pas)\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n(?=\S)")
_INTRO = re.compile(r"(?:^|\n)(?:\d{1,2}(?:\.\d{1,2})+ [^\n]*|[^\n]{3,80}:)(?=\n)")  # "7.3 pyrolytische…", "Opening…:"
_GAP_TITLE = 40  # a gap this short is a cited warning's own title line ("kans op letsel")
_MAINS_OFF = re.compile(r"\bpull (?:out )?(?:the |its |your |his )?(?:mains |power )?plug|\bplug (?:out|it out)\b"
                        r"|\bremove (?:the |its )?(?:mains )?plug|\bpull (?:the |its )?plug out")
_PLUG_IN_RE = re.compile(_PLUG_IN + r"|\b(?:re)?connect " + _NP + r"to the (?:electricity|mains|power)")
_CLOSES = re.compile(_PUT_BACK.pattern + r"|\b(?:closed|shut|lock|relock|vergrendel\w*|klik\w*|click\w*)\b"
                     r"|\b(?:put|plaats|zet)\b[^.\n]{0,40}\b(?:back|terug)\b")
_TAKE_ALL = re.compile(_TAKE.pattern.replace("{1,5}", "{1,8}"))  # "remove the accessories and the shelf supports"
# a tap turned back on is the water, not the machine: "turn the water tap back on" while unplugged is no switch-on
_SWITCH_ON = re.compile(r"\b(?:switch|turn|power)(?:es|s|ing|ed)? (?!(?:[\w-]+ ){0,3}?(?:tap|water|valve|supply)\b)"
                        + _NP + r"(?:back )?on\b")
# Opening and closing in words the first check does not read: "turn the lid open", "unlock the holder".
_OPEN_MORE = re.compile(r"\b(?:turn|swing|fold|flip|tip) ((?:[\w-]+ ){1,4})open\b"
                        r"|\b(?:unlock|unlatch)\w*(?= ((?:[\w-]+ ?){1,5}))")
_CLOSE_MORE = re.compile(r"\b(?:turn|swing|fold|flip|tip) ((?:[\w-]+ ){1,4})(?:closed|shut)\b"
                         r"|\b(?:lock|relock|latch)\w*(?= ((?:[\w-]+ ?){1,5}))")


_RISK = re.compile(r"\b(?:risk|risico|risiko|gefahr|damage|schade|schaden)\b")


def warns(sentence: str) -> bool:
    """A sentence that is a warning: a warning heading, a safety word, or a risk it names ("Risk of damage...")."""
    s = sentence.lower()
    return bool(GATE_MARKERS.search(s) or _SAFETY.search(s) or _RISK.search(s))


def _warns(text: str, inside: bool) -> str | None:
    """The first sentence of `text` that warns, cut to a few words (never the manual's whole line)."""
    for sent in _SENTENCE.split(text):
        s = " ".join(sent.split())
        if len(s) > 12 and (_SAFETY.search(s) or (inside and _WARN_NOT.search(s))):
            return " ".join(s.split()[:4])
    return None


def warnings_dropped(steps: Sequence[Step], pages: Sequence[str], others: Sequence[Step] = ()) -> list[Failure]:
    """A warning the manual gives inside the job's own procedure that no step of the job carries: a warning heading
    above where the job enters the manual or between two of its steps (as precondition_missing, with the headings a
    manual sets off by a symbol or calls "attentie"), a warning sentence in the procedure's lines above that place,
    or a warning or "do not" sentence between two of its steps. Text another job cites, or past a numbered heading,
    is another procedure's."""
    out = [Failure("warning_dropped", f.detail) for f in precondition_missing(steps, pages, GATE_MARKERS)]
    spans = _spans(steps, pages)
    theirs = [sp for sp in _spans(others, pages) if sp is not None]
    prev = None
    for i, sp in enumerate(spans):
        if sp is None:
            prev = None
            continue
        page, text, at, _ = sp
        inside = prev is not None and prev[0] == page and 0 <= at - prev[3] <= ABOVE + AFTER
        if inside and prev is not None:
            gap = text[prev[3]:at]
            if (len(gap.strip()) > _GAP_TITLE and not _HEADING.search(gap)
                    and not any(o[0] == page and prev[3] <= o[2] < at for o in theirs)
                    and not any(o and o[0] == page and prev[3] <= o[2] < at for o in spans)
                    and (said := _warns(gap, inside=True))):
                out.append(Failure("warning_dropped", f"\"{said}…\" between steps {i} and {i + 1} on page {page}"))
        elif i == 0 or prev is None or prev[0] != page or at < prev[2]:
            above = text[max(0, at - ABOVE):at]
            heads = list(_INTRO.finditer(above))
            if heads and not any(o[0] == page and at - ABOVE <= o[2] < at for o in theirs) \
                    and (said := _warns(above[heads[-1].end():], inside=False)):
                out.append(Failure("warning_dropped", f"\"{said}…\" above step {i + 1} on page {page}"))
        prev = sp
    return out


def gate(steps: Sequence[Step], pages: Sequence[str], kind: str, others: Sequence[Step] = ()) -> list[Failure]:
    """The final say on a job, after every repair: a warning it drops, a part it opens and never closes, a part it
    takes out among others ("remove the accessories and shelf supports") and never refits, or a plug it pulls in
    words the first check does not read ("pull its plug out"), and never puts back, or a switch-on before the plug
    is back in."""
    out = warnings_dropped(steps, pages, others)
    texts = [s.text_en.lower() for s in steps]
    if not any(PAIRS["mains"][0].search(t) for t in texts):  # else the first check has read the plug already
        events = sorted([(i, m.start(), "away") for i, t in enumerate(texts) for m in _MAINS_OFF.finditer(t)]
                        + [(i, m.start(), "back") for i, t in enumerate(texts)
                           for m in [*PAIRS["mains"][1].finditer(t), *_PLUG_IN_RE.finditer(t)]])
        if events and events[0][2] == "away" and events[-1][2] == "away":
            out.append(Failure("leaves_state", "mains off, never back on"))
    unplugged = False
    for i, _, e in sorted([(i, m.start(), "away") for i, t in enumerate(texts)
                           for m in [*PAIRS["mains"][0].finditer(t), *_MAINS_OFF.finditer(t)]]
                          + [(i, m.start(), "in") for i, t in enumerate(texts) for m in _PLUG_IN_RE.finditer(t)]
                          + [(i, m.start(), "on") for i, t in enumerate(texts) for m in _SWITCH_ON.finditer(t)]):
        if e == "on" and unplugged:
            out.append(Failure("wrong_order", f"switched on at step {i + 1} while unplugged"))
            break
        unplugged = e == "away" or (unplugged and e != "in")
    taken: dict[str, tuple[int, bool]] = {}
    brought: list[str] = []
    bring = not any(_STEP_REF.search(t) for t in texts)
    for i, t in enumerate(texts):
        for m, parts in _takes(t, _TAKE_ALL, _all_parts_in, brought, taken, bring):
            for part in parts:
                taken.setdefault(part, (i, m.group(0).startswith(("open", "tilt", "pry open", "prise open"))))
        for m in _OPEN_MORE.finditer(t):
            for part in _all_parts_in(_np(m)):
                taken.setdefault(part, (i, True))
        for m in [*_PUT.finditer(t), *_CLOSE_MORE.finditer(t)]:
            named = _all_parts_in(_np(m))
            if not named and taken and re.match(r"(?:it|them)\b", _np(m)):
                named = [max(taken, key=lambda p: taken[p][0])]
            for part in named:
                taken.pop(part, None)
        if _ALL_BACK.search(t):
            taken.clear()
    out += [Failure("leaves_state", f"{part} opened at step {i + 1}; never closed" if opened
                    else f"{part} removed or opened at step {i + 1}; never refitted")
            for part, (i, opened) in taken.items() if not opened or _closes_after(steps, pages, i)]
    return out


def _closes_after(steps: Sequence[Step], pages: Sequence[str], start: int) -> bool:
    """Whether the manual closes something in the procedure past step `start` that the job's own steps do not
    quote: between two of its steps, or right after its last quoted one (a step of ours quotes no page). An opening
    step the page does not hold counts as yes. A manual that never says to close what it opened ("open the flap"
    and no more) leaves nothing to carry."""
    spans = [sp for sp in _spans(steps[start:], pages) if sp is not None]
    if not spans or _spans(steps[start:start + 1], pages)[0] is None:
        return True
    gaps = [b[1][a[3]:b[2]] for a, b in itertools.pairwise(spans) if a[0] == b[0] and a[3] <= b[2]]
    after = spans[-1][1][spans[-1][3]:spans[-1][3] + AFTER]
    after = after[:m.start()] if (m := _HEADING.search(after)) else after
    return bool(_CLOSES.search(" ".join([*gaps, after]).lower()))


def _np(m: re.Match[str]) -> str:
    """The noun phrase a verb pattern caught, in whichever of its alternatives matched."""
    return next((g for g in m.groups() if g is not None), "")


def _all_parts_in(phrase: str) -> list[str]:
    """Every part a verb's noun phrase names, through "and" and commas ("the accessories and shelf supports")."""
    phrase = re.sub(r"\band then\b", "and", phrase)  # "the filter and then the seal": both
    np = re.split(r"\b(?:with|by|until|from|into|onto|to|so|on|in|under|at|then|if|for|out|off|back)\b", phrase,
                  maxsplit=1)[0]
    heads = []  # each conjunct names its last part: "the filter flap" is the flap
    for conjunct in re.split(r",|\b(?:and|or)\b", np):
        words = [re.sub(r"s$", "", w) for w in re.findall(r"[a-z]+", conjunct)]
        heads += [w for w in words if w in PARTS][-1:]
    return list(dict.fromkeys(heads))


def duplicate_steps(steps: Sequence[Step]) -> list[int]:
    """Indexes of steps that repeat the step right before them: the same quote, or nearly the same words. Only the
    step before: the same words further on are often another part ("Reconnect the hose" at the tap, then at the
    back of the machine)."""
    out = []
    for i in range(1, len(steps)):
        s, t = steps[i], steps[i - 1]
        a, b = _content(s.text_en), _content(t.text_en)
        if (s.quote.strip() and norm(s.quote) == norm(t.quote)) or (a and b and len(a & b) / len(a | b) >= 0.8):
            out.append(i)
    return out


def check(steps: Sequence[Step], pages: Sequence[str], kind: str, others: Sequence[str] = ()) -> list[Failure]:
    """Every whole-job failure; `others` are the titles of the card's other jobs. Duplicates are not a failure:
    code drops them (duplicate_steps)."""
    return (leaves_state(steps, pages, kind) + refers_outside(steps, others) + precondition_missing(steps, pages)
            + steps_skipped(steps, pages))


# The manual's own line that undoes a pair, in the job's own pages: where it exists, the job must cite it.
_RESTORE_LINE = {
    "mains": re.compile(r"stekker (?:weer )?in (?:het|een) (?:geaard )?stopcontact|steek de stekker|plug (?:the )?"
                        r"(?:[\w-]+ ){0,2}(?:back )?in(?:to)?\b|reconnect|netzstecker (?:wieder )?einstecken"),
    "water_supply": re.compile(r"kraan (?:weer |helemaal )?open|draai de (?:water)?kraan (?:weer )?open|turn on the "
                               r"(?:water|tap)|open the (?:water )?tap|wasserhahn (?:wieder )?(?:ganz )?öffnen"),
    "power": re.compile(r"zet (?:het apparaat|de ketel) (?:weer )?aan|switch (?:[\w-]+ ){0,2}(?:back )?on\b|"
                        r"turn (?:[\w-]+ ){0,2}back on\b|wieder einschalten"),
}
_STATE_ONLY = re.compile(r"(mains|water_supply|power) off, never back on")


def left_off(f: Failure) -> bool:
    """A failure that says only that the job leaves the mains, the water or the power off."""
    return f.code == "leaves_state" and bool(_STATE_ONLY.fullmatch(f.detail))


def restorable(steps: Sequence[Step], failures: Sequence[Failure], pages: Sequence[str]) -> list[tuple[str, int]]:
    """When a job fails the whole-job check only because it leaves the mains, the water or (on an always-on kind) the
    power off, and the manual's pages the job cites print no line that undoes it: each pair, with the index of the
    step that first turned it off, in the order to undo them (water, then mains, then power). [] otherwise: a job
    failing for any other reason, or one whose manual does say how to undo it, is never given our own step."""
    pairs = [m.group(1) for f in failures if f.code == "leaves_state" and (m := _STATE_ONLY.fullmatch(f.detail))]
    if not pairs or len(pairs) != len(failures):
        return []
    firsts = {}
    for name in ("water_supply", "mains", "power"):
        if name not in pairs:
            continue
        first = next((i for i, s in enumerate(steps) if PAIRS[name][0].search(s.text_en.lower())
                      or (name == "mains" and _MAINS_OFF.search(s.text_en.lower()))), None)
        if first is None:
            return []
        firsts[name] = first
    cited = {s.page for s in steps if 1 <= s.page <= len(pages)}
    # an off step quoted from a general chapter far from the procedure (a safety page that also says how to
    # install it): that page is not the job's own, so its "plug it in" does not undo this job
    for i in set(firsts.values()):
        rest = {s.page for k, s in enumerate(steps) if k != i and 1 <= s.page <= len(pages)}
        if rest and min(abs(steps[i].page - q) for q in rest) > 1:
            cited.discard(steps[i].page)
    text = " ".join(norm(pages[n - 1]) for n in sorted(cited | {n + 1 for n in cited if n < len(pages)}))
    if any(_RESTORE_LINE[name].search(text) for name in firsts):
        return []
    return list(firsts.items())


_REFIT = re.compile(r"(\w+) removed or opened at step (\d+); never refitted")
_ON_UNPLUGGED = re.compile(r"switched on at step (\d+) while unplugged")
# A step that needs the mains back (a switch-on or a run), and one that also needs the water (a run).
_RUN = re.compile(r"\b(?:start|run|select|choose|kies|starten)s?\b.{0,40}\b(?:programme|program|programma|cycle|wash|"
                  r"rinse|spin)\b|\bprogramma\b.{0,40}\bstart\w*")
# The words of a take-out quote that name no part: the verb and where it goes ("trek het onderrek naar buiten").
_NOT_A_PART = frozenset(["pull", "take", "remove", "lift", "open", "unscrew", "detach", "trek", "haal", "neem",
                         "verwijder", "draai", "schroef", "naar", "buiten", "binnen", "eruit", "omhoog", "beneden",
                         "boven", "onder", "voren", "helemaal", "voorzichtig", "from", "with", "away", "upward",
                         "upwards", "downward", "downwards", "the", "het", "een", "your", "uit", "van"])
_BACK_IN = re.compile(_PUT_BACK.pattern + r"|\b(?:push|slide|schuif|duw)\w*\b[^.\n]{0,40}\b(?:back|in|terug|weer)\b")


def own_restores(steps: Sequence[Step], failures: Sequence[Failure], pages: Sequence[str],
                 held: Collection[int] = ()) -> list[tuple[str, int, int | None]]:
    """Our own restore steps for a job whose only failures our fixed words undo, and whose pages print no line that
    does: (what, the step it undoes, the step it goes before, or None for the end). A part taken out and never put
    back is put back at the end; the water and the mains go back just before the first later step that needs them
    (a run needs both, a switch-on the mains), else at the end. [] when anything else fails, when the part was taken
    out at a step in `held` (a red zone, or a part one holds), or when the manual prints how to put it back."""
    refits = [(m.group(1), int(m.group(2)) - 1) for f in failures if f.code == "leaves_state"
              and (m := _REFIT.fullmatch(f.detail))]
    on = [int(m.group(1)) - 1 for f in failures if f.code == "wrong_order" and (m := _ON_UNPLUGGED.fullmatch(f.detail))]
    states = [f for f in failures if left_off(f)]
    if not failures or len(refits) + len(on) + len(states) != len(failures):
        return []
    pairs = restorable(steps, states, pages) if states else []
    if states and not pairs:
        return []
    if on and not any(name == "mains" for name, _ in pairs):
        first = next((i for i, st in enumerate(steps) if PAIRS["mains"][0].search(st.text_en.lower())
                      or _MAINS_OFF.search(st.text_en.lower())), None)
        cited = {st.page for st in steps if 1 <= st.page <= len(pages)}
        text = " ".join(norm(pages[n - 1]) for n in sorted(cited | {n + 1 for n in cited if n < len(pages)}))
        if first is None or _RESTORE_LINE["mains"].search(text):
            return []
        pairs = [*pairs, ("mains", first)]
    out: list[tuple[str, int, int | None]] = []
    for part, i in refits:
        if i in held or not 0 <= i < len(steps):
            return []
        names = {w for w in re.findall(r"\w{4,}", norm(steps[i].quote)) if w not in _NOT_A_PART}
        near = {n for n in range(steps[i].page - 1, steps[i].page + 2) if 1 <= n <= len(pages)}
        lines = [x for n in sorted(near | {st.page for st in steps if 1 <= st.page <= len(pages)})
                 for x in re.split(r"(?<=[.!?])\s+|\n", norm(pages[n - 1], lines=True))]
        if any(_BACK_IN.search(x) and names & set(re.findall(r"\w{4,}", x)) for x in lines):
            return []
        out.append((part, i, None))
    for name, i in pairs:
        needs = _RUN if name == "water_supply" else re.compile(_RUN.pattern + "|" + _SWITCH_ON.pattern)
        later = next((k for k in range(i + 1, len(steps))  # in our words or the manual's ("start Drain")
                      if needs.search(f"{steps[k].text_en} {steps[k].quote}".lower())), None)
        out.append((name, i, later))
    return out
