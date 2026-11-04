"""G0, the sentence filter. Every sentence the agent streams is checked here before it is drawn or spoken.

How a sentence is read against the country's table for the answer language:

0. Text this lane wrote passes whole and unchanged: a rule's line or script, the emergency text, a fail-closed line,
   each optionally with the fixed follow-on and its card citation (`warning_for`). A held warning is replaced by its
   rule's line, and that line must be heard, not held in turn. Nothing the model writes reaches this unless it is that
   exact text.
1. In STOP, every other sentence is held: the stop script is what is said.
2. An answer language with no table, or a country with no file, fails closed (no repair text; English line plus the
   international emergency advice). With no country file, only gas and mains are refused: those families' part names
   from every shipped table are held.
3. A line whose sha256 is a rule's look-only exemption passes that rule's part-name hold, and only that rule's.
4. ALLOW rules blank out their own part names, so a broader rule cannot match inside them ("filter cover", "pump
   housing"). "Its housing" (cover, casing) is blanked too when it closely follows an allowed part in the same clause
   ("the pump cover is out of its housing"): it is that part's own housing. A comma, "and", "then" or more than six
   words in between, and it is any housing again ("lift the pump cover and remove the board from its housing").
   A plain "the empty housing" near a part the card lets the user take out, in a sentence that opens nothing, is that
   part's seat and is blanked too (`card_seats`).
5. A STOP rule's part name, or a card red-zone part's name, is held whatever the verb (a red-zone name).
6. A HOLD rule holds when one of its part names and one of its verbs are in the same sentence.
7. A cover or hazard thing opened in other words holds as its zone (rules/compose.py), also when a pronoun opens it
   that points back to a part named earlier in the sentence or in the sentence before.
8. A sentence that only warns about the part it names passes (rules/reading.py); never for an emergency.
9. Wiping the outside of the casing with a dry or damp cloth, and nothing else, passes the casing rules
   (`outside_wipe`): the whole sentence must be that form, so a wet cloth, a spray, the inside or an opening holds.

It is code, not a second model. There is no setting that skips it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache

from contracts.models.card import Card
from contracts.models.rules import Rule
from contracts.models.safety import Hold as HoldState
from contracts.models.safety import Ok, Stop
from rules import compose, reading
from rules import text as tx
from rules.country import Country, load_country, no_country_line, no_language_line, shipped
from rules.disclaimer import disclaimer

State = Ok | HoldState | Stop
FAIL_CLOSED_FAMILIES = ("gas", "mains")


@dataclass(frozen=True)
class Pass:
    ok: bool = True


@dataclass(frozen=True)
class Hold:
    rule_id: str
    line: str
    words: int
    ok: bool = False
    # What matched, for the log (never sent to the phone).
    matched: tuple[str, ...] = ()


Result = Pass | Hold


def _lang_terms(rule: Rule, lang: str, which: str) -> list[str]:
    table = rule.parts if which == "parts" else rule.verbs
    return [t.root for t in table.root.get(lang, [])]


def _line(rule: Rule, lang: str) -> str:
    texts = rule.line.root
    return texts.get(lang) or texts.get("en") or next(iter(texts.values()))


def match_rule(norm: str, rule: Rule, lang: str) -> tuple[str, ...] | None:
    parts = [p for p in _lang_terms(rule, lang, "parts") if tx.find_part(norm, p, lang) >= 0]
    if not parts:
        return None
    if rule.action == "STOP":
        return tuple(parts)
    verbs = [v for v in _lang_terms(rule, lang, "verbs") if tx.has_verb(norm, v)]
    return (*parts, *verbs) if verbs else None


def _card_red_parts(card: Card | None, lang: str) -> Iterable[tuple[str, str]]:
    """(part name, rule_id) for each red-zone part on the card, in this language."""
    if card is None:
        return
    zones = {z.part_id: z.rule_id for z in card.red_zones}
    for part in card.parts:
        name = part.names.root.get(lang)
        if part.kind == "red_zone" and part.id in zones and name:
            yield name, zones[part.id]


def check_sentence(text: str, lang: str, card: Card | None, state: State | None, *, country: str | Country,
                   before: str = "") -> Result:
    """Pass, or Hold{rule_id, line, words}. A sentence that has not passed is never shown or spoken.
    `before` is the sentence said just before it, so "Find the gas pipe." then "Loosen it." holds."""
    c = country if isinstance(country, Country) else load_country(country)
    code = country.code if isinstance(country, Country) else country
    words = tx.word_count(text)

    if isinstance(state, Stop):
        if text.strip() == state.script.strip():
            return Pass()
        return Hold(state.rule_id, state.script, words, matched=("state:STOP",))
    if _own_line(text, lang, c):
        return Pass()
    if c is None:
        return _no_country(text, lang, code, words)
    if lang not in c.languages:
        return Hold(f"{code}.language.{lang}" if lang.isalpha() else f"{code}.language.unknown",
                    no_language_line(lang), words, matched=("language",))

    digest = tx.line_hash(text)
    rules = c.rules.rules
    allowed = [p for rule in rules if rule.action == "ALLOW" for p in _lang_terms(rule, lang, "parts")]
    # "The pump cover is out of its housing": that housing is the allowed part's own (step 4), blanked first.
    allowed = _own_housings(text, lang, allowed) + _own_housings(before, lang, allowed) + allowed
    allowed += no_gas_names(card.appliance_kind if card else None, lang, c, text, before)
    # A card part's seat (below); the sentence before keeps its own only for a sentence that tells what the user did.
    allowed_before = allowed + (card_seats(before, lang, card, c) if told_done(text, lang, c) else [])
    allowed = allowed + card_seats(text, lang, card, c)
    norm = _masked(tx.normalise(text), allowed)

    holds: list[Hold] = []
    emergency = False
    for rule in rules:
        if rule.action == "ALLOW":
            continue
        if digest in {h.root for h in rule.look_only_exemptions}:
            continue
        hit = match_rule(norm, rule, lang)
        if hit:
            holds.append(Hold(rule.id, _line(rule, lang), words, matched=hit))
            emergency = emergency or _zone(rule.id) in EMERGENCY_ZONES

    exempt = {h.root for r in rules for h in r.look_only_exemptions}
    clauses = [_masked(f" {k} ", allowed) for k in reading.clauses(text, lang)]
    if digest not in exempt:
        earlier = [_masked(f" {k} ", allowed_before) for k in reading.clauses(before, lang)] if before.strip() else []
        # Joined conductors and touched terminals come after the carried zone, never in its place: "Find the gas valve.
        # Turn it and touch the terminals." is the gas tap first.
        joined = ["mains.wiring"] if compose.joined_conductor(norm, lang) or _touched_terminals(norm, lang) else []
        for zone in dict.fromkeys(([z for k in clauses if (z := compose.composed(k, lang))] or _carried(clauses, lang)
                                   or _carried_over(earlier, reading.clauses(text, lang), lang, rules)) + joined):
            r = c.rule(f"{c.code}.{zone}")
            holds.append(Hold(r.id, _line(r, lang), words, matched=(f"composed:{zone}",)) if r is not None
                         else Hold(f"{c.code}.{zone}", no_country_line(lang), words, matched=(f"composed:{zone}",)))
    red = list(_card_red_parts(card, lang))
    for name, rule_id in red:
        if tx.find_part(norm, name, lang) >= 0 and digest not in exempt:
            r = resolve(c, rule_id)
            holds.append(Hold(rule_id, no_country_line(lang), words, matched=(name,)) if r is None
                         else Hold(r.id, _line(r, lang), words, matched=(name,)))
            emergency = emergency or _zone(rule_id) in EMERGENCY_ZONES
    if holds and outside_wipe(norm, lang):
        holds = [h for h in holds if _zone(h.rule_id) not in CASING_ZONES]
    if not holds:
        return Pass()
    # A warning about a dangerous part ("don't touch the back panel, that's for a technician") is not an
    # instruction to work on it. Never for an emergency, whose own line is what is said.
    if not emergency and reading.is_warning(text, lang, lambda k: _names_part(_masked(f" {k} ", allowed), rules,
                                                                               lang, red), _table_verbs(c, lang)):
        return Pass()
    return holds[0]


# Wiping the outside of the casing, as the manuals have the user do: the whole normalised sentence is this form and
# nothing more ("wipe the housing and control panel with a soft damp cloth", "neem de behuizing af met een vochtige
# doek"). A wet cloth, a spray or a cleaning agent, the inside, the vents, or any other verb is not this form, so it
# holds as before. Drying takes a dry or soft cloth, never a damp one. It clears only the casing zones; any other part
# named still holds.
CASING_ZONES = frozenset({"mains.casing", "gas.casing"})
# A dishwasher, washer or fridge has no gas, so there a "yellow handle" is a plastic grip (the PowerDisk holder's),
# not the gas tap's. Only that colour name is blanked on those appliances, and only while no gas part is named in
# the text (or the sentence before it: "Find the gas tap." then "Turn its yellow handle." holds), and only as whole
# words: "the yellow-handled lever" is not blanked. "Gas tap", "yellow tap" and every other gas word still hold on
# every card.
NO_GAS_KINDS = frozenset({"dishwasher", "washer", "fridge"})
_GAS_COLOUR_NAMES = {"en": ["yellow handle"]}


def no_gas_names(kind: str | None, lang: str, c: Country, *texts: str) -> list[str]:
    """The gas-tap colour names that name some other part here: an appliance with no gas, no gas part in `texts`."""
    names = _GAS_COLOUR_NAMES.get(lang, []) if kind in NO_GAS_KINDS else []
    if not names:
        return []
    gas = [p for r in c.rules.rules if r.family == "gas" and r.action != "ALLOW"
           for p in _lang_terms(r, lang, "parts") if p not in names]
    norms = [tx.normalise(t) for t in texts if t.strip()]
    if any(tx.find_part(n, p, lang) >= 0 for n in norms for p in gas):
        return []
    # Whole words only (a plural "s" aside): "the yellow-handled lever" is the gas tap's shape, so nothing is blanked.
    inside = [rf"\b{re.escape(tx.normalise(n).strip())}(?!s?\b)" for n in names]
    return [] if any(re.search(r, t) for t in norms for r in inside) else names


# "The round filter is out, I can see the empty housing": next to a part the card lets the user take out, a plain
# housing is that part's seat, not the machine's casing. The housing noun is blanked only when every housing in the
# sentence is plain (an article, or an article and a small adjective, before it; no "of ..." after it) and within a
# few words of an allowed card part (its name, or its head noun when that is a part users take out: "filter"), and
# nothing in the sentence opens or works on anything (no table verb, no opening verb). "Remove the filter and the
# housing", "the machine housing", "the housing of the dishwasher" and a bare "housing" with no card part still hold.
# The sentence before keeps its seat for a pronoun here only if this sentence just tells what the user did ("you
# took it out to clean"; `told_done`). No word of power, damage or a loose or opened state may be in either sentence.
_SEAT_NOUN = {"en": "housing", "nl": "behuizing"}
_SEAT_DET = {"en": frozenset({"the", "its", "that", "this"}), "nl": frozenset({"de", "zijn", "haar", "die", "deze"})}
_SEAT_ADJ = {"en": frozenset({"empty", "clean", "dirty", "round", "plastic"}),
             "nl": frozenset({"lege", "schone", "vuile", "ronde"})}
# The housing ends its noun phrase: the sentence ends after it, or one of these follows ("the housing panel", "the
# housing cover", "the housing of the dishwasher" do not).
_SEAT_NEXT = {"en": frozenset({"in", "is", "was", "looks", "should", "with", "where", "at", "on", "has", "now", "and",
                               "still", "here", "there", "sits", "stays", "seems", "appears", "around"}),
              "nl": frozenset({"in", "is", "was", "ziet", "moet", "met", "waar", "op", "heeft", "nu", "en", "nog",
                               "hier", "daar", "zit", "blijft", "lijkt"})}
# A word of power, damage, a loose or opened state, or what lies behind or inside: no seat in that sentence.
_SEAT_HAZARD = {
    "en": frozenset({"mains", "power", "live", "electric", "electrical", "electricity", "voltage", "current",
                     "terminal", "terminals", "wire", "wires", "wiring", "cable", "cables", "connector", "connectors",
                     "shock", "spark", "sparks", "sparking", "smoke", "smoking", "burning", "burnt", "hot", "warm",
                     "loose", "cracked", "broken", "damaged", "exposed", "open", "apart", "off", "behind", "inside",
                     "into", "through", "under", "underneath", "beneath", "panel", "screw", "screws", "cover",
                     "plate", "board", "motor", "heating", "element", "valve", "tap", "gas", "carries"}),
    "nl": frozenset({"netspanning", "stroom", "spanning", "elektrisch", "elektrische", "elektriciteit", "draad",
                     "draden", "bedrading", "kabel", "kabels", "aansluiting", "aansluitingen", "klem", "klemmen",
                     "vonk", "vonken", "rook", "heet", "hete", "warm", "warme", "los", "losse", "kapot", "gebarsten",
                     "open", "eraf", "achter", "binnen", "erin", "door", "onder", "paneel", "schroef", "schroeven",
                     "deksel", "plaat", "print", "motor", "element", "klep", "kraan", "gas"})}
_SEAT_GAP_WORDS = 8
_PAST = frozenset({"took", "got", "came"})


def _card_part_words(card: Card | None, lang: str) -> list[list[str]]:
    """The card's allowed parts as word lists: each full name, and its head noun when users take that part out."""
    out: list[list[str]] = []
    for part in card.parts if card else []:
        name = part.names.root.get(lang)
        if part.kind == "red_zone" or not name:
            continue
        words = tx.normalise(name).split()
        out.append(words)
        if len(words) > 1 and words[-1] in compose.USER_PARTS.get(lang, ()):
            out.append(words[-1:])
    return out


def _works_on_something(text: str, lang: str, c: Country) -> bool:
    norm = tx.normalise(text)
    return (any(tx.has_verb(norm, v.split()[0]) for v in _table_verbs(c, lang))
            or any(compose.opens(f" {k} ", lang) or compose.composed(f" {tx.normalise(k)} ", lang)
                   for k in reading.clauses(text, lang))
            or any(w in _SEAT_HAZARD[lang] for w in norm.split()))


def card_seats(text: str, lang: str, card: Card | None, c: Country) -> list[str]:
    """The housing noun to blank in `text` when each housing there is a card part's seat; else []."""
    noun = _SEAT_NOUN.get(lang)
    if noun is None or not text.strip():
        return []
    words = tx.normalise(text).split()
    at = [i for i, w in enumerate(words) if w == noun]
    parts = _card_part_words(card, lang)
    if (not at or not parts or any(w.startswith(noun) and w != noun for w in words)
            or _works_on_something(text, lang, c)):
        return []
    spans = [(i, i + len(p)) for p in parts for i in range(len(words)) if words[i:i + len(p)] == p]
    det, adj = _SEAT_DET[lang], _SEAT_ADJ[lang]
    for i in at:
        plain = i >= 1 and (words[i - 1] in det or (i >= 2 and words[i - 1] in adj and words[i - 2] in det))
        ends = i + 1 == len(words) or words[i + 1] in _SEAT_NEXT[lang]
        near = any(0 <= i - e <= _SEAT_GAP_WORDS or 0 < s - i <= _SEAT_GAP_WORDS + 1 for s, e in spans)
        if not (plain and ends and near):
            return []
    return [noun]


def told_done(text: str, lang: str, c: Country) -> bool:
    """Does `text` only tell what the user already did ("that matches you took it out")? Every opening verb in it is
    a past form right after "you", and it names nothing of power or damage. Only then may a pronoun in it point at a
    seat named the sentence before; anything else ("try unscrewing it", "it unscrews", "cut it open") is any housing.
    English only."""
    if lang != "en":
        return False
    words = tx.normalise(text).split()
    if any(w in _SEAT_HAZARD[lang] for w in words) or any(tx.has_verb(f" {' '.join(words)} ", v.split()[0])
                                                          for v in _table_verbs(c, lang)):
        return False
    verbs = (*compose.STRONG[lang], *compose.WEAK[lang])
    said = False
    for i, w in enumerate(words):
        past = w in _PAST or (w.endswith("ed") and any(tx.verb_form(w, v, lang) for v in verbs))
        if past and i > 0 and words[i - 1] == "you":
            said = True
        elif w in _PAST or any(tx.verb_form(w, v, lang) for v in verbs if v not in _PAST):
            return False
    return said


_DRY_WORDS = {"en": "soft|clean|dry|lint free|microfibre|microfiber", "nl": "zachte|droge|schone|pluisvrije"}
_CLOTH = {"en": rf"an? (?:(?:{_DRY_WORDS['en']}|damp|slightly damp) )+cloth",
          "nl": rf"een (?:(?:{_DRY_WORDS['nl']}|vochtige|licht vochtige) )+doek"}
_DRY_CLOTH = {"en": rf"an? (?:(?:{_DRY_WORDS['en']}) )*(?:soft|dry) (?:(?:{_DRY_WORDS['en']}) )*cloth",
              "nl": rf"een (?:(?:{_DRY_WORDS['nl']}) )*(?:zachte|droge) (?:(?:{_DRY_WORDS['nl']}) )*doek"}
_OUTSIDE = {"en": r"(?:the )?(?:outside of the |exterior of the |outer |exterior )?(?:casing|housing|cabinet|exterior|"
                  r"outside)(?: of the (?:appliance|machine))?(?: and (?:the )?(?:control |operating )?panel)?",
            "nl": r"(?:de |het )?(?:buitenkant van (?:de|het) )?(?:behuizing|ommanteling|buitenkant)"
                  r"(?: van het apparaat)?(?: en (?:het )?(?:bedienings)?paneel)?"}
OUTSIDE_WIPE = {
    "en": re.compile(rf"(?:(?:wipe|clean|polish)(?: down| over)? {_OUTSIDE['en']} with {_CLOTH['en']}"
                     rf"|dry(?: off)? {_OUTSIDE['en']} with {_DRY_CLOTH['en']})"
                     rf"(?: and (?:then )?dry (?:it|them|both|both parts)(?: off)? with {_DRY_CLOTH['en']})?"
                     rf"|{_OUTSIDE['en']} (?:has|have) been (?:(?:wiped|cleaned)(?: clean| down)? with {_CLOTH['en']}"
                     rf"|dried(?: off)? with {_DRY_CLOTH['en']})"),
    "nl": re.compile(rf"(?:(?:neem|veeg|reinig) {_OUTSIDE['nl']} (?:af |schoon )?met {_CLOTH['nl']}"
                     rf"|droog {_OUTSIDE['nl']} (?:af )?met {_DRY_CLOTH['nl']})(?: af)?"
                     rf"(?: en droog (?:ze|hem|het|deze|beide)(?: af| na)? met {_DRY_CLOTH['nl']})?"),
}


def outside_wipe(norm: str, lang: str) -> bool:
    """Is the whole normalised sentence a wipe of the casing's outside with a dry or damp cloth, and nothing more?"""
    return lang in OUTSIDE_WIPE and OUTSIDE_WIPE[lang].fullmatch(norm.strip()) is not None


# Touching terminals is wiring work, in every country's table ("pull the fuse and touch the terminals"). A battery's own
# terminals are not ("touch the battery terminals"), unless the sentence also names a mains place: the exemption is
# for those terminals alone, never the sentence ("touch the battery terminals inside the mains box" holds).
_TERMINALS = {"en": re.compile(r"\btouch(?:es|ed|ing)?\b(?: \w+){0,4}? terminals?\b")}
_BATTERY_TERMINALS = {"en": re.compile(r"\bbatter(?:y|ies) terminals?\b|"
                                       r"\bterminals? (?:of|on) (?:the |a |your )?batter(?:y|ies)\b")}
_MAINS_PLACE = {"en": re.compile(r"\b(?:mains|fuse|fuses|consumer|junction|socket|outlet|live|wiring|wire|wires|cable|"
                                 r"cables|housing|casing|panel|box|machine|appliance|inside|behind|plug)\b")}


def _touched_terminals(norm: str, lang: str) -> bool:
    if lang not in _TERMINALS or not _TERMINALS[lang].search(norm):
        return False
    return not _BATTERY_TERMINALS[lang].search(norm) or bool(_MAINS_PLACE[lang].search(norm))


# Emergencies are never read as warnings: a sentence about one is held, and its rule's line is said instead.
EMERGENCY_ZONES = frozenset({"gas.smell", "co.alarm", "heat.fire", "heat.burning", "water.flood"})


def _carried(clauses: list[str], lang: str) -> list[str]:
    """A cover or a hazard thing named in one clause and opened by a pronoun in a later one: "find the metal skin on
    the side of the dryer and prise it loose", "if the socket feels warm, open it up". Not "without opening it"."""
    for i, k in enumerate(clauses):
        zone = compose.named(k, lang) or compose.thing(k, lang)
        if zone and any(compose.opens(later, lang) and reading.points_back(later, lang)
                        and not reading.cued(later, lang) for later in clauses[i + 1:]):
            return [zone]
    return []


_NOUN = {"en": re.compile(r"\b(?:the|a|an|your|its|this|that) (?!(?:off|out|open|up|away|apart|loose)\b)\w+"),
         "nl": re.compile(r"\b(?:de|een|je|uw|jouw|dit|deze|het) (?!(?:los|open|eraf|af|uit|eruit|weg|dicht|vast)\b)"
                          r"\w+")}


_PRONOUN_AT = {"en": re.compile(r"\b(?:the one|it|them|they|this|that|these|those)\b"),
               "nl": re.compile(r"\b(?:hem|ze|zij|het|die|dat|deze|dit|eraf|eruit|erop|ertegen|er)\b")}


# Verbs that work on a thing without opening it ("drill through it", "reach into it", "press on it until it gives
# way", "poke the terminals in it"). They count only here, for a pronoun that points back at a held part named in the
# sentence before, whatever that part is ("Find the gas pipe." then "Give it a hard twist.").
_INTRUDE = {"en": ("reach", "drill", "break", "press", "push", "poke", "prod", "jab", "stick", "probe", "pierce",
                   "punch", "hammer", "smash", "bash", "unplug", "touch", "grab", "cut", "saw", "bend", "squeeze",
                   "crush", "dig", "scrape", "twist", "wrench", "yank", "rip", "tug", "wiggle", "jiggle",
                   "rotate", "turn", "tap", "pull", "puncture", "put"),
            "nl": ("reik", "boor", "breek", "druk", "duw", "prik", "steek", "por", "sla", "ram", "pak", "grijp", "raak",
                   "knip", "snijd", "zaag", "buig", "graaf", "schraap", "verdraai", "wring", "draai", "trek", "tik",
                   "ruk", "wiebel")}
# A pronoun after one of these is where the work happens or whose the thing is: what is named before it ("the wires
# behind it", "the back of it", "steek er een schroevendraaier in") is in or of that part, not this sentence's own.
_INSIDE_IT = {"en": re.compile(r"\b(?:behind|in|into|inside|through|under|underneath|beneath|within|on|of) "
                               r"(?:it|them)\b"),
              "nl": re.compile(r"\b(?:erachter|erin|erdoor|eronder|erop|ertegen|daarachter|daarin|daardoor|"
                               r"(?:achter|in|door|onder|op) (?:hem|het|ze))\b|"
                               r"\ber (?:\w+ ){0,3}?(?:in|door|doorheen|achter|onder|tegen|tegenaan|op)\b")}
# "it's removed" reads "its removed" once normalised: a verb after "its" is no noun.
_ITS_VERB = {"en": re.compile(r"\bits \w+(?:ed|en)\b"), "nl": re.compile(r"(?!x)x")}


# Things a hand holds to do the work: named in an earlier clause, they are not what "it" means.
_TOOLS = {"en": frozenset({"screwdriver", "screwdrivers", "pliers", "spanner", "wrench", "hammer", "drill", "knife",
                           "coin", "key", "tool", "tools", "cloth", "brush", "torch", "hand", "hands", "finger",
                           "fingers", "chisel", "crowbar", "pry", "prybar", "flathead"}),
          "nl": frozenset({"schroevendraaier", "tang", "sleutel", "moersleutel", "hamer", "boor", "boormachine", "mes",
                           "munt", "muntje", "gereedschap", "doek", "borstel", "zaklamp", "hand", "handen", "vinger",
                           "vingers", "beitel", "breekijzer", "inbussleutel"})}
# Taken out, these leave a live part behind them: "Pull the fuse, then touch it" means what the fuse sat in. Named
# anywhere in the text, nothing there is what the pronoun means ("remove the fuse from the plug and turn it").
_EXPOSES = {"en": frozenset({"fuse", "fuses"}), "nl": frozenset({"zekering", "zekeringen"})}
_NOUN_WORDS = 3  # the noun phrase after a determiner: at most this many words


def _plural(word: str, lang: str) -> bool:
    if lang == "nl":
        return word.endswith("en") or (word.endswith("s") and not word.endswith("ss"))
    return word.endswith("s") and not word.endswith(("ss", "us", "is"))


_PLURAL_PRONOUN = {"en": frozenset({"them", "they", "these", "those"}), "nl": frozenset({"ze", "zij", "die"})}


def _antecedent(text: str, lang: str, pronoun: str) -> bool:
    """Does `text` name a thing the pronoun can mean: a noun phrase with no tool in it, and no plural unless the
    pronoun is plural too ("spin them by hand to check that they turn freely")?"""
    norm = tx.normalise(text)
    plural = pronoun in _PLURAL_PRONOUN[lang]
    if any(w in _EXPOSES[lang] for w in norm.split()):
        return False
    for m in _NOUN[lang].finditer(norm):
        words = norm[m.start():].split()[1:1 + _NOUN_WORDS]
        if not any((_plural(w, lang) and not plural) or w in _TOOLS[lang] for w in words):
            return True
    return False


def _verb_in(clause: str, lang: str, verbs: dict[str, tuple[str, ...]]) -> bool:
    return any(tx.verb_form(w, v, lang) for w in tx.normalise(clause).split() for v in verbs.get(lang, ()))


def _carries(i: int, clauses: list[str], lang: str) -> bool:
    """Does clause i open or work on what a pronoun points back to, and not on a thing named before it?"""
    k = clauses[i]
    if not ((compose.opens(k, lang) or _verb_in(k, lang, _INTRUDE)) and reading.points_back(k, lang)
            and not reading.cued(k, lang)):
        return False
    # Where the pronoun is the work site ("reach into it", "the wires behind it", "druk erop") it carries whatever was
    # named before it: safety wins. Otherwise a thing named before it is what it means ("open the filter flap and pull
    # it out"), unless an earlier clause names one it cannot mean: a plural or a tool ("With the pliers, twist it").
    if _INSIDE_IT[lang].search(tx.normalise(k)):
        return True
    m = _PRONOUN_AT[lang].search(k)
    earlier_own = _ITS_VERB[lang].sub(" ", " ".join(clauses[:i]))
    here_own = _ITS_VERB[lang].sub(" ", k[:m.start() if m else len(k)])
    pronoun = tx.normalise(m.group(0)).strip() if m else ""
    return not (_antecedent(earlier_own, lang, pronoun) or _NOUN[lang].search(here_own))


def _carried_over(earlier: list[str], clauses: list[str], lang: str, rules: list[Rule]) -> list[str]:
    """A part named in the sentence before and opened or worked on by a pronoun in this one: "Find the gas pipe.
    Loosen it.", "I can see the housing. Drill through it." Not when this sentence names something of its own first
    ("open the filter flap and pull it out"), unless the pronoun is where the work happens ("the wires behind it")."""
    if not earlier or lang not in _NOUN:
        return []
    if not any(_carries(i, clauses, lang) for i in range(len(clauses))):
        return []
    for k in reversed(earlier):
        zone = compose.named(k, lang) or compose.thing(k, lang) or next(
            (_zone(r.id) for r in rules if r.action == "STOP" and _zone(r.id) not in EMERGENCY_ZONES
             and any(tx.find_part(k, p, lang) >= 0 for p in _lang_terms(r, lang, "parts"))), None)
        if zone:
            return [zone]
    return []


def _zone(rule_id: str) -> str:
    return rule_id.split(".", 1)[1] if "." in rule_id else rule_id


def _masked(norm: str, allowed: list[str]) -> str:
    for p in allowed:
        norm = tx.mask(norm, p)
    return norm


def _names_part(clause: str, rules: list[Rule], lang: str, red: list[tuple[str, str]]) -> bool:
    """Does this clause name a held part (any STOP or HOLD part, verb or not, a composed cover or a card red zone)?"""
    return (any(tx.find_part(clause, p, lang) >= 0 for r in rules if r.action != "ALLOW"
                for p in _lang_terms(r, lang, "parts"))
            or compose.composed(clause, lang) is not None
            or any(tx.find_part(clause, name, lang) >= 0 for name, _ in red))


def _table_verbs(c: Country, lang: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(v for r in c.rules.rules for v in _lang_terms(r, lang, "verbs")))


_CLAUSE_BREAK = re.compile(r"[,;:.!?()]|\b(?:and|then|or|but|while|before|after|en|dan|maar|daarna)\b",
                           re.IGNORECASE)
_OWN = {"en": "its (?:housing|cover|casing)", "nl": "(?:zijn|haar) (?:behuizing|deksel|afdekking)"}
_OWN_GAP_WORDS = 6


def _own_housings(text: str, lang: str, allowed: list[str]) -> list[str]:
    """Normalised spans "<allowed part> ... its housing" inside one clause, for masking (step 4)."""
    own = _OWN.get(lang)
    if own is None:
        return []
    out = []
    for clause in _CLAUSE_BREAK.split(text):
        cn = tx.normalise(clause)
        for p in allowed:
            pn = tx.normalise(p).strip()
            i = cn.find(pn)
            if not pn or i < 0:
                continue
            after = cn[i + len(pn):]
            m = re.match(rf" (?:\S+ ){{0,{_OWN_GAP_WORDS}}}?{own}(?= )", after)
            if m:
                out.append(pn + after[:m.end()])
    return out


@cache
def _own_lines(code: str | None, lang: str) -> frozenset[str]:
    out = {no_country_line(lang), no_language_line(lang)}
    c = load_country(code) if code else None
    if c is not None:
        out.add(c.emergency(lang))
        for r in c.rules.rules:
            out.update(t for t in (r.line.root.get(lang), r.script.root.get(lang)) if t)
    return frozenset(t.strip() for t in out)


def _own_line(text: str, lang: str, c: Country | None) -> bool:
    """Is this exactly a line the rules lane wrote, with at most the follow-on (only after a line that may have one)
    and its card citation after it (`warning_for`)?"""
    cite = re.escape(disclaimer("step_citation", lang, page=0)).replace("0", r"[1-9][0-9]{0,3}")
    bare = re.sub(rf" \({cite}\)$", "", text.strip())
    follow = f" {disclaimer('held_follow_on', lang)}"
    if bare.endswith(follow) and c is not None:
        line = bare[:-len(follow)]
        return any(_line(r, lang) == line and _follows_on(r, lang) for r in c.rules.rules)
    return bare in _own_lines(c.code if c else None, lang)


# A line that sends the user away, to a phone or a utility, or stops the guide is the whole answer: no follow-on keeps
# someone near a gas problem, an alarm, water at the plug or a broken meter seal. Any gas or CO rule, an emergency, a
# phone number, "go outside", "leave" (not "leave it alone"), "I won't go on", a meter seal.
_NO_FOLLOW_ON_FAMILIES = frozenset({"gas", "co"})
_URGENT = re.compile(r"\b(?:1\d\d|9\d\d|0\d{3}[ -]?\d{3})|\bemergency\b|\bleave\b(?!.*\balone\b)|go outside|"
                     r"get everyone out|won't go on|naar buiten|niet verder|\bnood|\bseal\b|zegel", re.IGNORECASE)


def _follows_on(rule: Rule, lang: str) -> bool:
    """May this rule's line be followed by the fixed follow-on (disclaimer `held_follow_on`)?"""
    return (rule.action != "ALLOW" and rule.family not in _NO_FOLLOW_ON_FAMILIES
            and _zone(rule.id) not in EMERGENCY_ZONES and not _URGENT.search(_line(rule, lang)))


def warning_for(hold: Hold, lang: str, card: Card | None, *, country: str | Country) -> str:
    """What to say in place of a held sentence: the hold's own line, then the fixed follow-on when the rule allows one
    (`_follows_on`), and the manual page when the card cites a red zone under the same rule. It always passes G0, so a
    struck warning still leaves a warning."""
    c = country if isinstance(country, Country) else load_country(country)
    said = hold.line
    rule = c.rule(hold.rule_id) if c is not None else None
    if rule is not None and hold.line == _line(rule, lang) and _follows_on(rule, lang):
        said = f"{said} {disclaimer('held_follow_on', lang)}"
    if card is not None and c is not None:
        for z in card.red_zones:
            r = resolve(c, z.rule_id)
            if r is not None and r.id == hold.rule_id:
                return f"{said} ({disclaimer('step_citation', lang, page=z.page)})"
    return said


def resolve(c: Country, rule_id: str) -> Rule | None:
    """A card cites <country>.<family>.<zone>; the user's country file answers for the same family and zone."""
    family_zone = rule_id.split(".", 1)[1] if "." in rule_id else rule_id
    return c.rule(f"{c.code}.{family_zone}")


def _no_country(text: str, lang: str, code: str, words: int) -> Result:
    norm = tx.normalise(text)
    tables = [oc for oc in (load_country(o) for o in shipped()) if oc is not None]
    if not any(lang in oc.languages for oc in tables):
        return Hold(f"{code}.language.{lang}" if code.isalpha() and lang.isalpha() else "zz.language.unknown",
                    no_language_line(lang), words, matched=("language",))
    for oc in tables:
        for rule in oc.rules.rules:
            if rule.family not in FAIL_CLOSED_FAMILIES or rule.action == "ALLOW":
                continue
            for check_lang in {lang, "en"}:
                for p in _lang_terms(rule, check_lang, "parts"):
                    if tx.find_part(norm, p, check_lang) >= 0:
                        rid = f"{code}.{rule.family}.no_rules_file" if code.isalpha() and len(code) == 2 \
                            else f"zz.{rule.family}.no_rules_file"
                        return Hold(rid, no_country_line(lang), words, matched=(p,))
    return Pass()
