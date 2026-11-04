"""Composed matches: a cover word, a thing it covers and an opening verb, in any wording.

The rule table lists part names, and a model that avoids them says "get the top off the washing machine" or "doe de
voorkant van de cv-ketel open". Rather than add each wording as a phrase, this reads the shape: a cover word (top,
back, plate, achterkant, kap) joined by a preposition or a possessive to a thing (an appliance, the boiler, the
consumer unit, a socket, the flue gases), and a verb that opens or removes, in the same clause. Each shape names the
zone whose rule answers, in the user's country file. It only adds holds: nothing here lets a sentence through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from rules import text as tx

ARTICLES = {"en": r"(?:the|a|an|your|this|that|its|their)", "nl": r"(?:de|het|een|je|jouw|die|dat|deze)"}
ADJ = {"en": r"(?:outer|metal|plastic|rear|front|side|top|bottom|little|small|lower|upper|whole|grey|white)",
       "nl": r"(?:hele|metalen|plastic|kleine|onderste|bovenste|witte|grijze)"}
# A preposition that ties a cover to what it covers.
LINKS = {"en": r"(?:of|off|from|on|under|behind|at|to|in|inside|over|round|around)",
         "nl": r"(?:van|op|aan|onder|achter|bij|in|om|rond)"}

# Opening verbs: strong ones open on their own; a weak one ("take", "haal") needs a particle in the clause, or the
# "off"/"from"/"van" link itself.
STRONG = {"en": ("remove", "unscrew", "undo", "unclip", "unbolt", "prise", "prize", "pry", "detach", "dismantle",
                 "open", "crack", "loosen", "disconnect", "strip", "peel", "lever", "force", "swap", "rewire",
                 "reconnect"),
          "nl": ("verwijder", "wrik", "demonteer", "sloop", "schroef", "open", "koppel", "peuter", "forceer", "klik",
                 "strip", "omwissel", "verwissel", "wissel")}
WEAK = {"en": ("take", "took", "get", "got", "lift", "pull", "slide", "ease", "pop", "draw", "knock", "snap", "move",
               "work", "come", "came", "drop", "swing", "hinge", "fold"),
        "nl": ("haal", "halen", "doe", "maak", "til", "trek", "draai", "schuif", "duw", "wip", "kom", "klap", "val",
               "ga", "gaat", "gaan")}
PARTICLES = {"en": ("off", "away", "open", "apart", "out", "loose", "free", "up"),
             "nl": ("eraf", "af", "weg", "los", "open", "eruit", "uit", "uitelkaar", "eraan")}
OFF_LINKS = {"en": ("off", "from"), "nl": ("van",)}
# A lid opens as often as it comes off (a top-loader's lid): it counts only when it comes off.
LIDS = {"en": ("lid",), "nl": ("deksel",)}
OFF_WORDS = {"en": ("off", "away", "remove", "removed", "removing", "unscrew", "unscrewed", "unscrewing"),
             "nl": ("eraf", "af", "weg", "verwijder", "verwijderen", "los", "losschroeven")}
# A side named alone is a direction as often as a cover ("pull the dishwasher back"): owned by a movable appliance,
# it needs a cover noun. A boiler hangs on the wall, so "the boiler front" is its cover.
DIRECTIONS = {"en": ("top", "back", "front", "side", "sides", "bottom", "base", "rear", "outside", "underside"),
              "nl": ()}
# Parts the user takes out themselves: a clause that takes one out "of the front of the machine" opens nothing.
USER_PARTS = {"en": ("drawer", "filter", "basket", "tray", "door", "hose", "seal", "gasket", "plug", "dispenser",
                     "rack", "shelf", "lint", "fluff", "trap", "bulb", "knob", "salt", "rinse", "container", "cap",
                     "arm", "cutlery", "detergent", "softener", "drum", "paddle", "lamp", "light", "flap", "hatch",
                     "condenser", "battery", "batteries", "remote", "button", "display", "label", "sticker"),
              "nl": ("lade", "laatje", "bakje", "zeepbakje", "filter", "korf", "rek", "deur", "slang", "rubber",
                     "stekker", "pluizen", "zeef", "lampje", "knop", "plank", "zout", "dop", "sproeiarm", "bestek",
                     "glansmiddel", "trommel", "lamp", "klep", "klepje", "luik", "luikje", "condensor", "batterij",
                     "afstandsbediening", "knopje", "display", "sticker")}


@dataclass(frozen=True)
class Shape:
    zone: str                     # family.zone; the user's country file answers for it
    things: tuple[str, ...]       # what is covered
    covers: tuple[str, ...]       # what covers it
    alone: tuple[str, ...] = ()   # the thing itself opened, "open the boiler up": regexes on the clause
    any_verb: bool = False        # the shape alone is the hazard (a STOP rule's part), whatever the verb


SHAPES: dict[str, tuple[Shape, ...]] = {
    "en": (
        Shape("gas.casing", ("boiler", "combi boiler", "combi", "water heater"),
              ("top", "back", "front", "side", "sides", "bottom", "base", "rear", "plate", "panel", "cover", "shell",
               "skin", "case", "casing", "housing", "outside", "underside", "fascia", "lid", "hood", "jacket"),
              alone=(r"\bboiler (?:up|open|apart)\b", r"\b(?:open|crack|prise|pry) (?:up )?(?:the |your )?boiler$",)),
        Shape("mains.casing",
              ("washing machine", "washer", "dishwasher", "dryer", "tumble dryer", "washer dryer", "oven", "cooker",
               "fridge", "freezer", "fridge freezer", "machine", "appliance"),
              ("top", "back", "front", "side", "sides", "bottom", "base", "rear", "plate", "panel", "cover", "shell",
               "skin", "case", "casing", "housing", "outside", "underside", "worktop", "lid", "sheet")),
        Shape("chemical.refrigerant", ("fridge", "freezer", "fridge freezer", "chest freezer"),
              ("copper line", "copper pipe", "copper tube", "thin pipe", "thin line", "metal pipe", "pipe", "line",
               "tube", "gas line"), any_verb=True),
        Shape("mains.consumer_unit",
              ("consumer unit", "fuse box", "fuse board", "fuse panel", "breaker box", "breaker panel",
               "electrical panel", "electrics box", "electric box", "electricity box", "distribution board",
               "meter box", "box with the fuses", "box with the breakers", "box of fuses", "fuses", "groepenkast",
               "meterkast", "zekeringkast"),
              ("cover", "lid", "plate", "panel", "front", "faceplate", "casing", "box", "back", "inside", "guts",
               "inner cover", "protective cover", "shield"),
              alone=(r"\bopen (?:up )?(?:the |your )?(?:consumer unit|fuse box|fuse board|groepenkast)\b(?! door)",)),
        Shape("mains.wiring", ("socket", "wall socket", "plug socket", "outlet", "wall outlet", "power point",
                               "light switch", "switch plate", "spur"),
              ("faceplate", "face plate", "plate", "cover", "front", "back", "fascia", "inside", "wires", "wiring",
               "screws", "screw", "terminals", "terminal", "back box"),
              alone=(r"\b(?:socket|outlet|power point) (?:out|off|away) (?:of|from) (?:the )?wall\b",
                     r"\bfrom (?:the|its) (?:back box|wall box|pattress)\b",
                     r"\b(?:socket|outlet|power point) (?:out|off|loose|up|open)\b(?! of the (?:plug|cable))",
                     r"\bopen (?:up )?(?:the |your |that )?(?:wall )?(?:socket|outlet|power point)\b")),
        Shape("mains.wiring", ("socket", "wall socket", "plug socket", "outlet", "wall outlet", "power point",
                               "light switch", "back box"),
              ("wires", "wire", "wiring", "terminals", "cables", "live", "neutral", "earth"), any_verb=True),
        Shape("gas.flue", ("fumes", "flue gases", "exhaust gases", "combustion gases", "boiler", "boilers"),
              ("exhaust", "flue", "pipe", "duct", "vent", "outlet", "chimney")),
        Shape("pressure.relief_valve", ("boiler", "boilers", "heating", "pipework"),
              ("red valve", "little red valve", "red tap", "red knob", "red lever", "red screw"), any_verb=True),
    ),
    "nl": (
        Shape("gas.casing",
              ("ketel", "cv ketel", "cvketel", "cv", "combiketel", "boiler", "geiser", "verwarmingsketel"),
              ("bovenkant", "achterkant", "voorkant", "zijkant", "onderkant", "buitenkant", "voorplaat", "plaat",
               "paneel", "kap", "mantel", "behuizing", "omkasting", "deksel", "voorpaneel", "front", "afdekking",
               "kast", "voorkap", "frontplaat", "voorklep", "afdekkap"),
              alone=(r"\b(?:ketel|cv ketel|combiketel|geiser) (?:open|uit elkaar)\b",)),
        Shape("mains.casing",
              ("wasmachine", "vaatwasser", "vaatwasmachine", "droger", "wasdroger", "oven", "fornuis", "koelkast",
               "vriezer", "machine", "apparaat"),
              ("bovenkant", "achterkant", "voorkant", "zijkant", "onderkant", "buitenkant", "voorplaat", "achterplaat",
               "zijplaat", "onderplaat", "plaat", "paneel", "kap", "mantel", "behuizing", "omkasting", "bovenblad",
               "werkblad", "afdekking", "plint", "omhulsel", "ommanteling", "deksel")),
        Shape("chemical.refrigerant", ("koelkast", "vriezer", "diepvries", "koel vriescombinatie"),
              ("koperen leiding", "koperen buis", "koperen buisje", "leiding", "leidinkje", "buis", "buisje", "pijp",
               "pijpje"), any_verb=True),
        Shape("mains.consumer_unit",
              ("groepenkast", "meterkast", "zekeringkast", "stoppenkast", "verdeelkast", "elektriciteitskast",
               "kast met de zekeringen", "kast met de groepen", "kast met de stoppen"),
              ("kap", "deksel", "afdekkap", "afdekplaat", "plaat", "voorkant", "binnenkant", "paneel", "afdekking",
               "beschermkap", "binnenkap", "afscherming"),
              alone=(r"\b(?:maak|doe) (?:de |je )?(?:groepenkast|zekeringkast|stoppenkast|verdeelkast) open\b",
                     r"\b(?:groepenkast|zekeringkast|stoppenkast|verdeelkast) open(?:maken|schroeven)?\b")),
        Shape("mains.wiring", ("stopcontact", "wandcontactdoos", "lichtschakelaar", "schakelaar", "contactdoos"),
              ("afdekplaat", "afdekraam", "raam", "plaat", "plaatje", "kapje", "voorkant", "binnenkant", "draden",
               "draadjes", "bedrading", "schroefjes", "schroeven", "schroef", "aansluitingen", "inbouwdoos"),
              alone=(r"\b(?:stopcontact|wandcontactdoos|contactdoos) (?:uit|van|los van) de muur\b",
                     r"\b(?:stopcontact|wandcontactdoos|contactdoos) (?:los|eruit|eraf|open)\b")),
        Shape("mains.wiring", ("stopcontact", "wandcontactdoos", "contactdoos", "lichtschakelaar", "inbouwdoos"),
              ("draden", "draadjes", "draad", "bedrading", "aansluitingen", "fase", "nul", "aarde"), any_verb=True),
        Shape("gas.flue", ("rookgassen", "rookgas", "uitlaatgassen", "verbrandingsgassen", "ketel", "cv ketel"),
              ("afvoer", "afvoerpijp", "pijp", "buis", "leiding", "uitlaat", "schoorsteen")),
        Shape("pressure.relief_valve", ("ketel", "cv ketel", "cv", "verwarming", "leidingen"),
              ("rode ventiel", "rood ventiel", "rode kraan", "rode kraantje", "rood kraantje", "rode knop",
               "rode hendel", "rode schroef"), any_verb=True),
    ),
}


# A cover with no thing named, where the clause still opens it: "unscrew the top", "the back comes off with a
# screwdriver", "the panel at the back slides out", "haal de plaat aan de zijkant eraf". Lid, cover and flap are not
# here: those are the user's own as often as not.
BARE = {
    "en": re.compile(r"\b(?:the|that|this|its) (?:outer |metal |whole )?(?:back|top|side|rear|bottom|base|underside|"
                     r"back panel|side panel|top panel|rear panel|rear sheet|back sheet|panel|plate|casing|shell|skin|"
                     r"housing|outer case)"
                     r"(?: at the (?:back|side|top|rear|bottom))?(?= (?:off|away|out|loose|free|comes|slides|lifts|"
                     r"drops|pops|unclips|apart|alone|just|should|will|can|with|by)\b|$)"),
    "nl": re.compile(r"\b(?:de|het|die|dat) (?:hele |metalen )?(?:achterkant|bovenkant|zijkant|onderkant|buitenkant|"
                     r"achterplaat|bovenplaat|zijplaat|onderplaat|voorplaat|achterwand|zijwand|plaat|paneel|mantel|"
                     r"behuizing|omhulsel)(?: (?:aan|op) de (?:achterkant|zijkant|bovenkant|onderkant))?"
                     r"(?= (?:eraf|af|los|weg|eruit|open|komt|gaat|schuift|klikt|zit|zitten|met|kan|moet)\b|$)"),
}
BARE_ZONE = "mains.casing"


def _ordered(shapes: tuple[Shape, ...]) -> tuple[Shape, ...]:
    """The narrow shapes first, so "het rode ventiel op de ketel" answers as the relief valve, not the casing."""
    return tuple(sorted(shapes, key=lambda s: s.zone not in ("pressure.relief_valve", "gas.flue")))


def _alt(words: tuple[str, ...]) -> str:
    return "(?:" + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)) + ")"


@cache
def _patterns(lang: str) -> tuple[tuple[Shape, re.Pattern[str], re.Pattern[str], tuple[re.Pattern[str], ...]], ...]:
    art, adj, link = ARTICLES[lang], ADJ[lang], LINKS[lang]
    out = []
    for s in _ordered(SHAPES[lang]):
        thing, cover = _alt(s.things), _alt(s.covers)
        movable = s.zone != "gas.casing"
        noun = _alt(tuple(c for c in s.covers if not movable or c not in DIRECTIONS[lang]))
        sided = rf"{_alt(DIRECTIONS[lang])} (?:panel|plate|cover|casing)" if DIRECTIONS[lang] else noun
        # "the top off the washing machine", "de voorkant van de cv ketel", "the plate under the dishwasher door"
        linked = re.compile(rf"\b{cover}(?:s|en)?(?: (?:panel|plate|cover|screws|paneel|plaat|kap|schroefjes))? "
                            rf"(?P<link>{link}) (?:{art} )?(?:{adj} )?(?:\w+ ){{0,3}}?{thing}\b")
        # "the boilers front cover", "the dishwasher back panel"; in Dutch also one word, "ketelkap"
        owned = re.compile(rf"\b{thing}s? (?:{adj} )?(?:{noun}|{sided})\b"
                           + (rf"|\b{thing}{cover}\b" if lang == "nl" else ""))
        out.append((s, linked, owned, tuple(re.compile(a) for a in s.alone)))
    return tuple(out)


def _opens(clause: str, lang: str, link: str | None) -> bool:
    words = clause.split()
    if any(tx.verb_form(w, v, lang) for w in words for v in STRONG[lang]):
        return True
    if not any(tx.verb_form(w, v, lang) for w in words for v in WEAK[lang]):
        return False
    return link in OFF_LINKS[lang] or any(w in PARTICLES[lang] for w in words) or (
        lang == "nl" and any(w.endswith(p) for w in words for p in ("eraf", "los", "open", "weg")))


def named(clause: str, lang: str) -> str | None:
    """The zone of a cover a normalised clause names, opened or not ("the metal skin on the side of the dryer")."""
    if lang not in SHAPES:
        return None
    text = clause.strip()
    for s, linked, owned, _ in _patterns(lang):
        m = linked.search(text) or owned.search(text)
        if m is not None and not _users_own(text[:m.start()], lang):
            return s.zone
    m = BARE[lang].search(text)
    return BARE_ZONE if m is not None and not _users_own(text[:m.start()], lang) else None


# Things that are the hazard themselves when opened: "if the socket feels warm, open it up".
HAZARD_THINGS = ("gas.casing", "mains.consumer_unit", "mains.wiring")


def thing(clause: str, lang: str) -> str | None:
    """The zone of a hazard thing (a boiler, a consumer unit, a socket) a normalised clause names, or None."""
    if lang not in SHAPES:
        return None
    for s in SHAPES[lang]:
        if s.zone in HAZARD_THINGS and re.search(rf"\b{_alt(s.things)}\b(?! (?:door|display|screen|app|cupboard|"
                                                  rf"kast|deur|scherm|klep))", clause):
            return s.zone
    return None


def opens(clause: str, lang: str) -> bool:
    """Does a normalised clause open or remove something ("and prise it loose")?"""
    return lang in SHAPES and _opens(clause.strip(), lang, None)


# A conductor taken off its connection: "remove the mains cable from its terminal", "disconnect the brown wire", "maak
# de voedingskabel los". Unplugging is not ("pull the cord out of the socket", "haal het snoer uit het stopcontact"),
# nor a cable tie, a wire shelf, a hose, or a cable that is not the mains: a word excuses only the cable it names
# ("the USB cable", "the cord rewind", "de oplaadkabel"), not every cable in the clause ("the supply flex from the data
# plate bracket"). A bare cord or lead is the mains when it is named so ("the power cord") or the clause names a
# mains part or the supply ("pull the cord off the motor terminals"); "take the lead out of the box" is not.
_COLOUR = r"(?:brown|blue|black|red|green|yellow|white|grey|gray)"
_COLOURS = rf"{_COLOUR}(?:(?: and | or | ?/ ?|-| ){_COLOUR})*"
_KLEUR = r"(?:bruine?|blauwe?|zwarte?|rode?|groene?|gele?|grijze?|witte?)"
_KLEUREN = rf"{_KLEUR}(?:(?: en | of | ?/ ?|-| ){_KLEUR})*"
_POLES = r"(?:live|neutral|earth|phase)"
_POLEN = r"(?:fase|nul|aarde)"
CABLES = {
    "en": re.compile(r"\b(?:cables?|wires?|wiring|flex|conductors?|cores?|loom|harness|connectors?|spades?|leads?|"
                     r"cords?)\b(?! (?:ties?|tie wraps?|clips?|shelf|shelves|rack|racks|basket|brush|wool|tidy|guide|"
                     r"holder|hook|clamp)\b)"),
    "nl": re.compile(r"\b(?:\w*kabels?|\w*snoer(?:en|tje)?|\w*draad|\w*draden|\w*draadjes?|aders?|kabelboom|"
                     r"connectors?|\w*stekkerverbinding(?:en)?|\w*kabelschoen(?:en)?|stekkertjes?)\b"),
}
# Always the mains, whatever is near: a pole by name, L and N, colours.
POLES = {
    "en": re.compile(rf"\b(?:the|its|that|a|{_COLOURS}) {_POLES}\b|\b{_POLES}(?: and | or | ?/ ?| & ){_POLES}\b"
                     r"|\bl (?:and |& |/ ?)?n\b"
                     r"|\b(?:detach|disconnect|remove|unscrew|undo|cut|free|loosen|unclip|swap|withdraw)\w* "
                     rf"{_POLES}\b"),
    "nl": re.compile(rf"\b(?:de|het|{_KLEUREN}) (?:{_POLEN}|fasedraad|nuldraad|aardedraad)\b"
                     rf"|\b(?:haal|maak|koppel|trek|knip|verwijder|ontkoppel)\w* {_POLEN}\b"
                     rf"|\b{_POLEN}(?: en | of | ?/ ?){_POLEN}\b|\bl (?:en |& |/ ?)?n\b"),
}
# Words that make the cable they name not the mains, before it ("the usb charging cable") or after it ("the cord
# rewind", "het snoer van de haspel"); in Dutch also inside it ("de oplaadkabel").
NOT_MAINS_BEFORE = {
    "en": frozenset({"usb", "hdmi", "charging", "charger", "data", "network", "ethernet", "lan", "aerial", "antenna",
                     "coax", "coaxial", "speaker", "headphone", "headphones", "audio", "phone", "telephone",
                     "extension", "battery", "pull", "remote", "tv", "camera", "doorbell", "bell"}),
    "nl": frozenset({"usb", "hdmi", "oplaad", "oplader", "data", "netwerk", "antenne", "coax", "luidspreker",
                     "telefoon", "verleng", "batterij", "afstandsbediening", "tv"}),
}
NOT_MAINS_AFTER = {"en": re.compile(r"^ (?:\w+ )?(?:rewind|reel|retract\w*|storage)\b"),
                   "nl": re.compile(r"^ (?:\w+ ){0,3}(?:\w*haspel|oprol\w*)\b")}
NOT_MAINS_INSIDE = re.compile(r"^(?:usb|hdmi|oplaad|oplader|data|netwerk|antenne|coax|luidspreker|telefoon|verleng|"
                              r"batterij|tv|naai|borduur|prikkel|ijzer|bind|vis|tand|kant)")
# A draadje is a thread ("knip de losse draadjes af", "het draadje van het etiket") unless a mains part or a colour
# is beside it.
DRAADJE_MAINS = re.compile(rf"\b(?:{_KLEUREN}|element\w*|\w*element|motor|aansluit\w*|klem\w*|print\w*|pomp|"
                           r"thermostaat|kroonsteen|fase|nul|aarde|netsnoer|voeding\w*)\b")
# A bare cord or lead with this near it is the mains.
QUALIFIED = re.compile(rf"\b(?:mains|power|supply|electric|electrical|appliance|live|neutral|earth|{_COLOURS}) $")
MAINS_NEAR = re.compile(r"\b(?:motor|thermostat|element|terminals?|pump|heater|pcb|control board|circuit board|"
                        r"mains|supply|live|neutral|earth|input|appliance|plug|machine)\b")
TO_DO = re.compile(r"^ to (?!the |its |a |an |your |that )\w+")  # "pull the cord to release the lid"
# A cable kept or routed somewhere: "take the power cord out of the storage compartment", "unclip the cable from the
# cable clip", "klik de kabel in de kabelclip".
KEPT = {
    "en": re.compile(r"\b(?:out of|from|in|into|onto) (?:the |its |a )?(?:storage |accessory |cardboard )?"
                     r"(?:compartment|box|bag|packaging|storage|drawer|cupboard)\b|\b(?:out of|from|in|into|onto) "
                     r"(?:the |its |a )?(?:cable |wire |cord )?(?:clips?|holders?|guides?|channel|retainer|tidy)\b"),
    "nl": re.compile(r"\b(?:in|uit|van) (?:de |het )?(?:\w*clip|\w*houder|\w*geleider|kabelgoot)\b"),
}


_DETERMINERS = frozenset({"the", "a", "an", "its", "your", "that", "this", "de", "het", "een", "je", "uw", "zijn"})


def _modifiers(before: str) -> list[str]:
    """The (up to two) words that modify a noun: those after its determiner."""
    words: list[str] = []
    for w in reversed(before.split()[-2:]):
        if w in _DETERMINERS:
            break
        words.append(w)
    return words


def _mains_cable(clause: str, lang: str) -> bool:
    """Does a normalised clause name a mains conductor (not one a nearby word says is not the mains)?"""
    if POLES[lang].search(clause):
        return True
    for m in CABLES[lang].finditer(clause):
        noun, before = m.group(0), _modifiers(clause[:m.start()])
        if any(w in NOT_MAINS_BEFORE[lang] for w in before) or NOT_MAINS_AFTER[lang].search(clause[m.end():]):
            continue
        if lang == "nl":
            if NOT_MAINS_INSIDE.match(noun) or ("draadje" in noun and not DRAADJE_MAINS.search(clause)):
                continue
            return True
        if noun.startswith(("lead", "cord")) and (TO_DO.match(clause[m.end():]) or not (
                QUALIFIED.search(clause[:m.start()]) or MAINS_NEAR.search(clause))):
            continue
        return True
    return False


CONDUCTOR_VERBS = {"en": ("cut", "snip", "unhook", "release", "free", "separate", "yank", "tug", "wiggle", "pull",
                          "take", "withdraw", "unplug"),
                   "nl": ("knip", "ontkoppel", "snij", "verwijder")}
UNPLUGGED = {
    "en": re.compile(r"\bunplug\w*|\b(?:from|out of|at) (?:the |its |a )?(?:wall |mains |plug )?(?:socket|outlet|"
                     r"power point|wall)\b(?! (?:inside|in the machine|on the board|board))|\bcable ties?\b|"
                     r"\b(?:hose|water|tap|inlet|drain|outlet|aquastop)\w* (?:\w+ )?connectors?\b|"
                     r"\bconnectors? (?:on|of) the (?:hose|tap)\b"),
    "nl": re.compile(r"\b(?:uit|van|los van) (?:het |de )?(?:stopcontact|wandcontactdoos|contactdoos|muur)\b|"
                     r"\bkabelbinder|\bstekker\b|\b(?:tuin|water|toevoer|afvoer)slang"),
}
# Taking a hard-wired appliance off the supply, conductor named or not: "unwire the hob", "disconnect the cooker from
# the mains", "disconnect the dishwasher from its fixed supply", "koppel de kookplaat los van het lichtnet", and the
# supply itself ("disconnect the mains supply"). "Disconnect the appliance from the mains" is how a manual says unplug
# it, so off the mains only an appliance that is wired in counts; off a fixed supply or a fused spur, any does. A
# wired-in appliance disconnected with no supply named holds too ("disconnect the hob", "koppel de doorstromer los").
# A boiler off "the power supply" passes: a combi boiler comes on a plug, and a manual says unplug it so. An electric
# or water boiler is a water heater.
_WIRED_IN = r"(?:cooker|hob|oven|range|cooktop|stove|water heater|immersion heater|immersion|electric shower|shower|" \
            r"extractor fan|extractor|boiler)s?"
_INGEBOUWD = r"(?:\w*kookplaat|fornuis|\w*oven|elektrische douche|doorstromer|(?:cv |combi)?ketel|\w*boiler)"
BOILER_PLUG = re.compile(r"\b(?<!electric )(?<!water )(?:combi |gas |central heating )?boiler (?:\w+ ){0,2}?"
                         r"(?:from|off) the power supply\b")
_OFF = r"(?:disconnect\w*|detach\w*|remove\w*|take|taking|took|takes|separate\w*)"
_SUPPLY = r"(?:the |its |your |a )?(?:mains(?: supply| electricity)?|electricity(?: supply)?|electric(?:al)? supply|" \
          r"power supply|supply)"
_FIXED = r"(?:the |its |your |a )?(?:fixed (?:supply|wiring|connection)|(?:fused |switched )?spur|hard ?wired supply)"
_NET = r"(?:het (?:licht|elektriciteits|stroom)?net|de netspanning|de stroomvoorziening|de netvoeding|" \
       r"de (?:eind)?groep|de groepenkast)"
_VAST = r"(?:de vaste (?:aansluiting|bedrading|voeding))"
UNWIRED = {
    "en": re.compile(rf"\bunwir\w*|\b{_OFF} (?:(?!from\b|off\b)\w+ ){{0,4}}?{_WIRED_IN} (?:\w+ ){{0,2}}?"
                     rf"(?:from|off) {_SUPPLY}\b|\b{_OFF} (?:(?!from\b|off\b)\w+ ){{0,4}}?(?:from|off) {_FIXED}\b"
                     r"|\bdisconnect\w* (?:the |your |its )?(?:(?!from\b)\w+ ){0,2}?(?:mains|electricity|electrical|"
                     r"fixed) supply\b"
                     rf"|\bdisconnect\w* (?:the |your |its |a )?(?:\w+ ){{0,2}}?{_WIRED_IN} *$"
                     rf"|\b(?:{_OFF}|unwir\w*) (?:\w+ ){{0,3}}?hard ?wired\b"),
    "nl": re.compile(rf"\b(?:koppel|haal|maak|ontkoppel)\w* (?:\w+ ){{0,4}}?{_INGEBOUWD} (?:los |af )?van {_NET}"
                     rf"|\b(?:koppel|haal|maak|ontkoppel)\w* (?:\w+ ){{0,4}}?(?:los |af )?van {_VAST}"
                     rf"|\b(?:koppel|ontkoppel)\w* (?:de |het |je |uw )?(?:\w+ ){{0,2}}?{_INGEBOUWD}(?: los| af)? *$"),
}
_NL_OFF = ("eraf", "eruit", "weg", "los")


def conductor(clause: str, lang: str) -> bool:
    """Does a normalised clause take a mains conductor off its connection (not unplug it)?"""
    if lang not in CABLES:
        return False
    if UNWIRED[lang].search(clause) and not (lang == "en" and BOILER_PLUG.search(clause)):
        return True
    if not _mains_cable(clause, lang) or UNPLUGGED[lang].search(clause) or KEPT[lang].search(clause):
        return False
    words = clause.split()
    if any(tx.verb_form(w, v, lang) for w in words for v in CONDUCTOR_VERBS[lang] if v != "unplug"):
        return True
    if lang == "nl" and any(w in _NL_OFF for w in words):  # "neem de kabel weg", "neem de netkabel eraf"
        return True
    link = "from" if " from " in f" {clause} " else "van" if " van " in f" {clause} " else None
    return _opens(clause, lang, link)


# A conductor named across "and" or "then", which split clauses: "remove the brown and blue wires", "disconnect live
# and neutral", "disconnect l and n", "the brown wire should then be removed".
JOINED = {"en": re.compile(rf"\b{_COLOUR} (?:and|or) {_COLOURS} (?:wires?|cables?|cores?|conductors?|earth)\b"
                           rf"|\b{_POLES} (?:and|or) {_POLES}\b|\bl (?:and )?n\b"),
          "nl": re.compile(rf"\b{_KLEUR} (?:en|of) {_KLEUREN} (?:draad|draden|aders?|kabels?)\b"
                           rf"|\b{_POLEN} (?:en|of) (?:de )?{_POLEN}\b|\bl (?:en )?n\b")}
PASSIVE = {"en": re.compile(r"\b(?:be|is|are|being|been|gets?|got) (?:\w+ )?(?:removed|disconnected|detached|cut|"
                            r"unscrewed|freed|released|withdrawn|loosened|unclipped|pulled (?:off|out|free)|"
                            r"taken (?:off|out))\b"),
           "nl": re.compile(r"\b(?:wordt|worden|werd|werden|moet|moeten|kan|kunnen) (?:\w+ ){0,3}?(?:losgemaakt|"
                            r"verwijderd|losgekoppeld|ontkoppeld|afgekoppeld|doorgeknipt|losgehaald|losgedraaid|"
                            r"weggehaald)\b")}


def joined_conductor(sentence: str, lang: str) -> bool:
    """Does a whole normalised sentence take off a conductor that "and" or "then" splits across clauses?"""
    if lang not in JOINED:
        return False
    if PASSIVE[lang].search(sentence):
        return _mains_cable(sentence, lang) and not (UNPLUGGED[lang].search(sentence) or KEPT[lang].search(sentence))
    return JOINED[lang].search(sentence) is not None and conductor(sentence, lang)


def composed(clause: str, lang: str) -> str | None:
    """The zone (family.zone) a normalised clause opens, or None. A clause that only names the thing does not."""
    if lang not in SHAPES:
        return None
    text = clause.strip()
    found = _shaped(text, lang)
    if found is not None:
        return found
    if conductor(text, lang):
        return "mains.wiring"
    m = BARE[lang].search(text)
    if m is not None and not _users_own(text[:m.start()], lang) and _opens(text, lang, None):
        return BARE_ZONE
    return None


def _shaped(text: str, lang: str) -> str | None:
    for s, linked, owned, alone in _patterns(lang):
        m = linked.search(text) or owned.search(text)
        link = m.group("link") if m is not None and "link" in m.groupdict() else None
        if m is None:
            m = next((a for a in (p.search(text) for p in alone) if a is not None), None)
            link = OFF_LINKS[lang][0]  # "the socket off the wall", "de ketel open": the shape is the opening
        if m is None or _users_own(text[:m.start()], lang):
            continue
        if m.group(0).split()[0] in LIDS[lang] and not any(w in OFF_WORDS[lang] for w in text.split()):
            continue
        if s.any_verb or _opens(text, lang, link):
            return s.zone
    return None


def _users_own(before: str, lang: str) -> bool:
    """A user part before the cover: named, or blanked out by an ALLOW rule (a run of spaces in a masked clause)."""
    return "  " in before.strip() or any(w.startswith(u) for w in before.split() for u in USER_PARTS[lang])
