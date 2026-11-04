"""Is a model sentence that names a dangerous part a warning about it, or an instruction to work on it?

"Don't touch the back panel; anything behind it is for a technician" names the back panel, and G0 used to hold it,
replacing a good warning with the rule's line. This reads the sentence clause by clause. It passes only when:

1. some clause carries a prohibition or a hand-off to a professional, from a closed list (a negation that governs an
   action verb, "leave it alone", "that's for an engineer", "call your installer", "laat het zitten", ...);
2. every clause that has an action verb and names a part, or points back at one ("it", "hem"), carries such a cue
   itself, so "leave the panel alone, then unscrew it" still holds;
3. no cue is voided: an anti-cue in its clause ("don't worry", "no need", "vergeet niet", "it's fine to") or a limit
   after it ("don't remove it while it's plugged in" says it may come off later).

G0 asks this only for a model sentence, never for an emergency rule, never in STOP, and never for the user's own words
(the engine and check_step do not call it). It cannot raise or clear a stop. A sentence it does not understand holds.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import cache

from rules import text as tx

# Clause breaks: punctuation, then conjunctions that start a new instruction.
_PUNCT_BREAK = re.compile(r"[,;:!?()\[\]—–]|\s-\s|\.(?=\s)")
CONJ = {"en": ("and", "but", "so", "or", "then", "if", "because", "when", "while", "until", "till", "unless",
               "after", "before", "once", "whether", "though", "although", "since", "without", "otherwise",
               "except", "than", "instead", "first", "which"),
        "nl": ("en", "maar", "dus", "of", "als", "want", "omdat", "wanneer", "totdat", "terwijl", "zodat", "voordat",
               "nadat", "zolang", "tenzij", "anders", "zonder", "behalve", "daarna")}
# A Dutch "als" opens a clause only before its subject ("als de ketel aan staat"), not in "als bewoner".
_SUBJECT = ("de", "het", "een", "je", "jij", "u", "er", "hij", "zij", "ze", "we", "wij", "dat", "die", "dit", "deze")
# A clause that starts with one of these, after a prohibition, limits it: the act is fine once the limit is passed.
# "if" and "when" limit a negation ("don't remove it if it's plugged in") but not a hand-off ("call an electrician if
# it trips again").
LIMITS = {"en": ("while", "until", "till", "unless", "before", "without", "except", "once"),
          "nl": ("zolang", "totdat", "tot", "tenzij", "voordat", "zonder", "behalve", "terwijl")}
IF_LIMITS = {"en": ("if", "when", "whenever"), "nl": ("als", "wanneer", "indien")}
# "never cover or block the pipe": the negation reaches over "or".
OR = {"en": ("or", "nor"), "nl": ("of", "noch")}

PRO = {"en": r"(?:(?:qualified|registered|certified|gas safe|heating|service|local|landlords) )*"
             r"(?:engineer|electrician|technician|installer|plumber|professional|pro|specialist|landlord|expert|"
             r"gas safe|repairer|repair person|service agent|fitter|service team|manufacturer|manufacturers|"
             r"supplier|gas supplier|network operator|dno|service centre|service center|support team)s?",
       "nl": r"(?:(?:erkende?|gecertificeerde?|eigen|cv|gas) )*"
             r"(?:monteur|installateur|elektricien|vakman|vakmensen|loodgieter|specialist|verhuurder|"
             r"woningcorporatie|servicemonteur|gasfitter|installatiebedrijf|professional|servicedienst|fabrikant|"
             r"leverancier|netbeheerder|klantenservice)s?"}
_A = {"en": r"(?:a|an|the|your|our)", "nl": r"(?:een|de|je|jouw|uw)"}

# The verbs a prohibition is about. A negation counts only when the next content word is one of these, so "don't
# worry" and "never mind" are not prohibitions. Matched at the start of a word ("unscrewing", "verwijderen").
ACTION = {"en": ("touch", "remove", "open", "take", "unscrew", "undo", "loosen", "tighten", "adjust", "turn",
                 "twist", "move", "cut", "disconnect", "reconnect", "unclip", "prise", "pry", "lift", "pull", "bleed",
                 "tamper", "go near", "reach", "put", "poke", "force", "break", "strip", "join",
                 "fiddle", "mess", "dismantle", "detach", "bypass", "release", "operate", "fix",
                 "repair", "replace", "work on", "do", "get", "pop", "ease", "slide", "crack", "push",
                 "flip", "reset", "check", "peel", "knock", "drill", "rewire",
                 "bend", "squeeze", "clean", "wipe", "spray", "pour", "submerge", "pierce",
                 "puncture", "regas", "top up", "fill", "close", "shut", "unbolt", "use"),
          "nl": ("aanraak", "aanrak", "raak", "aankom", "kom aan", "verwijder", "open", "maak", "doe", "haal",
                 "schroef", "draai", "verstel", "stel", "snijd", "knip", "koppel", "wrik", "til", "trek", "duw",
                 "forceer", "demonteer", "repareer", "vervang", "sloop", "peuter", "pak", "grijp", "zet",
                 "klik", "ontlucht", "dek", "afdek", "blokkeer", "prik", "boor", "buig", "knijp", "spuit",
                 "giet", "dompel", "omzeil", "vul", "sluit", "verbreek", "breek", "aansluit", "sluit aan", "ga aan",
                 "werk", "voel", "rommel", "friemel", "prutsel", "klooi", "pruts")}
# Skipped between a negation and its verb: "don't ever try to remove", "never be blocked", "niet zelf".
SKIP = {"en": ("just", "ever", "even", "really", "try", "to", "attempt", "go", "and", "yourself", "be", "you",
               "please", "at", "all", "anything", "any", "of", "the", "this", "that", "it", "them", "yet"),
        "nl": ("zelf", "ooit", "even", "proberen", "probeer", "te", "je", "jij", "u", "het", "hem", "ze", "de",
               "zomaar", "zo", "maar", "eens", "er", "hier", "daar", "gewoon", "alsjeblieft", "aan", "met", "nog")}
_NEG = {"en": ("dont", "do not", "does not", "doesnt", "never", "must not", "mustnt", "should not", "shouldnt",
               "cannot", "cant", "can not", "may not", "will not", "wont", "please dont", "no one should",
               "nobody should", "not"),
        "nl": ("niet", "nooit", "nergens")}

CUES = {
    "en": (
        r"\bleave (?:\w+ ){0,6}?(?:alone|be|off|as (?:it|they) (?:is|are)|where (?:it|they) (?:is|are)|in place|"
        rf"untouched|well alone|on|closed|shut|sealed|to {_A['en']} {PRO['en']}|for {_A['en']} {PRO['en']})\b",
        rf"\b(?:is|are|its|thats|this is|that is|it is|they are|theyre|stays|remains) (?:\w+ ){{0,3}}?(?:for|to) "
        rf"(?:{_A['en']} )?{PRO['en']}\b",
        rf"\b(?:job|work|one|task|thing) (?:\w+ )?for (?:{_A['en']} )?(?:\w+ ){{0,2}}?{PRO['en']}\b",
        rf"\b(?:best |better )?left to (?:{_A['en']} )?{PRO['en']}\b",
        rf"\b(?:call|get|ring|phone|contact|ask|book|have|need|needs|wait for) (?:in )?(?:out )?"
        rf"(?:{_A['en']} )?(?:\w+ )?{PRO['en']}\b",
        rf"\b{PRO['en']} (?:will|can|should|must|needs to|has to|is going to|checks|looks|replaces|fixes|"
        r"deals|handles|services|tests|inspects)\b",
        rf"\b(?:only|just) (?:\w+ ){{0,3}}?(?:{_A['en']} )?(?:\w+ ){{0,3}}?{PRO['en']}\b",
        rf"\bby (?:{_A['en']} )?(?:\w+ ){{0,3}}?{PRO['en']}\b",
        rf"\b(?:no one|nobody|noone) (?:but|except|other than|apart from) (?:{_A['en']} )?(?:\w+ )?{PRO['en']}\b",
        r"\bnot (?:something|anything|a thing|one) (?:to|you|for you|we)\b",
        r"\bnothing (?:for you )?(?:to do|you need to do|for you)\b",
        r"\b(?:keep|stay|hands|stand) (?:\w+ ){0,4}?(?:away from|off|clear of|out of|back from)\b",
        r"\bkeep (?:\w+ ){0,4}?(?:on|closed|shut|in place|sealed|covered)$",
        r"\bhands off\b",
        r"\b(?:must|should|has to|needs to|will|is to|are to) (?:\w+ )?(?:stay|remain|be kept|be left|stay)"
        r" (?:\w+ )?(?:sealed|closed|shut|on|in place|covered|where it is|alone|untouched|clear)\b",
        r"\b(?:stays|remains|is staying) (?:sealed|closed|shut|on|in place|covered|put)\b",
        r"\bi (?:wont|will not|cant|cannot|can not|dont|do not|am not going to|m not going to) "
        r"(?:\w+ ){0,2}?(?:guide|help|walk|take you|talk you|show)\b",
        r"\b(?:ill|i will|i ll|let me) stop here\b",
        r"\bnot (?:safe|something) (?:for you )?to\b",
        r"\b(?:is|are) (?:off limits|out of bounds|no go|not user serviceable|not for (?:you|us|users))\b",
        r"^(?:not|never) (?:the|a|an|your) \w+",  # a denial: "a water tap, not the gas tap"
        r"\b(?:isnt|is not|arent|are not|not|never|wouldnt be|would not be) (?:\w+ )?safe\b",
        r"\b(?:isnt|arent|is not|are not) (?:something|anything|a thing|one) (?:to|you|for you|we)\b",
        rf"\b(?:{_A['en']} )?{PRO['en']} (?:job|task|thing|work|only|territory)\b",
        rf"\bin the hands of (?:{_A['en']} )?(?:\w+ ){{0,3}}?{PRO['en']}\b",
        rf"\bhand (?:it |this |that )?over to (?:{_A['en']} )?(?:\w+ ){{0,3}}?{PRO['en']}\b",
        r"\b(?:book|arrange|schedule|call for|ask for|get) (?:a |an |the )?(?:repair|service|engineer|technician|"
        r"callout|call out|electrician|professional)(?: visit| call| appointment)?\b",
        r"\b(?:can|could|would|will|may|might) (?:\w+ ){0,2}?(?:expose|void|shock|electrocute|kill|burn|scald|injure|"
        r"hurt|explode|let (?:\w+ ){0,2}?(?:out|escape|leak)|release (?:scalding|hot|gas|carbon|fumes|refrigerant))\b",
        r"\b(?:sealed|closed|locked) for a reason\b",
        r"^(?:so |and |but )?(?:please )?(?:dont|do not|never)(?: please)?$",  # "..., so please don't."
        r"^than (?:\w+ )?\w+ing\b",  # "replace the hose rather than opening its safety box"
        r"^without \w+ing\b",  # "wipe the cover without opening it"
        r"\b(?:id|i would|i d) rather not\b",
        r"\bi (?:cant|cannot|can not|wouldnt|would not|wont|dont|do not) (?:\w+ )?(?:advise|recommend|suggest)\b",
        r"\bnot (?:recommended|advised|advisable)\b",
        r"\b(?:im|i am|we are|were) not able to (?:\w+ )?(?:guide|help|walk|talk|take)\b",
        r"\boutside (?:of )?what (?:i|we) can (?:help|do|guide)\b",
        rf"\bhand (?:you |it |this |that )?over to (?:{_A['en']} )?(?:\w+ ){{0,3}}?{PRO['en']}\b",
        rf"\b{PRO['en']} (?:has|have|had|visits|comes|is (?:responsible|the one)|are responsible)\b",
        r"\b(?:is|are) responsible for\b",
        r"\b(?:releases|exposes|lets out|leaks|vents) (?:\w+ ){0,2}?(?:refrigerant|gas|carbon monoxide|co|live|mains|"
        r"scalding|hot water|fumes|high voltage)\b",
        r"\bvoids? (?:the |your )?(?:warranty|guarantee)\b",
        r"\b(?:can|could|would|will|may|might) (?:\w+ ){0,2}?(?:crack|damage|break|rupture|split)\b",
        r"^instead of \w+ing\b",
    ),
    "nl": (
        r"\blaat (?:\w+ ){0,6}?(?:zitten|met rust|zoals (?:hij|het|ze|die) (?:is|zijn|zit|zitten)|over aan|"
        rf"dicht|erop|aan {_A['nl']} {PRO['nl']}|voor {_A['nl']} {PRO['nl']}|liggen voor {_A['nl']} {PRO['nl']})\b",
        rf"\b(?:is|zijn|blijft|blijven) (?:\w+ ){{0,3}}?voor (?:{_A['nl']} )?{PRO['nl']}\b",
        rf"\b(?:werk|klus|iets|taak|een klus) (?:\w+ )?voor (?:{_A['nl']} )?{PRO['nl']}\b",
        rf"\b(?:bel|haal|vraag|laat|wacht op|schakel) (?:\w+ ){{0,3}}?(?:{_A['nl']} )?{PRO['nl']}\b",
        rf"\b(?:moet|mag|kan|zal|laat) (?:\w+ )?(?:{_A['nl']} )?{PRO['nl']}\b",
        r"\braad (?:\w+ ){0,3}?(?:niet aan|af)\b",
        r"\b(?:is|zijn) verantwoordelijk\b",
        r"^zonder\b",  # "zonder het open te maken"
        rf"\b{PRO['nl']} (?:kijkt|controleert|doet|vervangt|komt|moet|mag|zal|kan|regelt|repareert|test|"
        r"onderhoudt|lost)\b",
        rf"\b(?:alleen|enkel|uitsluitend) (?:{_A['nl']} )?(?:\w+ )?{PRO['nl']}\b",
        rf"\bdoor (?:{_A['nl']} )?(?:\w+ )?{PRO['nl']}\b",
        r"\b(?:hou|houd|blijf|blijven) (?:\w+ ){0,4}?(?:weg|uit de buurt|af) (?:bij|van)\b",
        r"\bhanden (?:weg|eraf|thuis)\b",
        r"\b(?:moet|moeten|blijft|blijven|mag|mogen) (?:\w+ ){0,3}?(?:erop|dicht|zitten|gesloten|op zijn plek|"
        r"op zijn plaats|met rust|verzegeld) (?:blijven|zitten|gelaten worden)?",
        r"\b(?:blijft|blijven) (?:\w+ )?(?:erop|dicht|zitten|gesloten|verzegeld)\b",
        r"\bhoef (?:jij |je |u )?(?:\w+ ){0,3}?niets\b",
        r"\bniets (?:\w+ )?(?:aan )?te doen\b",
        r"\bniet (?:iets|een klus|werk) (?:om|voor)\b",
        r"\bhier stop ik\b",
        r"\bik (?:\w+ ){0,3}?(?:help|begeleid|leid) (?:\w+ ){0,3}?niet\b",
        r"\bniet voor (?:ons|jou|u|zelf)\b",
        r"^(?:geen|niet (?:de|het|een)) \w+",  # a denial: "een waterkraan, geen gaskraan"
        rf"\b(?:doet|regelt|vervangt|controleert|repareert|onderhoudt) (?:\w+ )?(?:{_A['nl']} )?{PRO['nl']}\b",
        r"\b(?:kan|wil|ga|mag) ik (?:\w+ ){0,3}?niet (?:\w+ )?(?:helpen|begeleiden|verder)\b",
        r"\b(?:is|zijn) (?:\w+ )?niet veilig\b",
        r"\b(?:kan|kunnen|zou|zouden) (?:\w+ ){0,3}?(?:schokken|elektrocuteren|verbranden|ontploffen|vrijkomen|"
        r"lekken|ontsnappen)\b",
        rf"\b(?:geef|draag) (?:het |dit )?over aan (?:{_A['nl']} )?{PRO['nl']}\b",
        r"^(?:dus |en |maar )?(?:alsjeblieft )?(?:niet|nooit) (?:doen)?$",
    ),
}
# A cue in the same clause as one of these is void.
ANTI = {
    "en": (r"\b(?:dont|do not|never|no need to|not) (?:\w+ )?(?:worry|panic|hesitate|forget|mind|bother|wait|"
           r"be afraid|be scared|be shy|stop|call|leave|need|have to|think twice)\b",
           r"\b(?:no need|not needed|isnt needed|not necessary|isnt necessary|needn t|neednt|dont need|doesnt need|"
           r"wont need|shouldnt need|dont have to|doesnt have to|no reason)\b",
           r"\b(?:not|isnt|its not|it s not) (?:that |too |very |at all )?(?:hard|difficult|tricky|dangerous|"
           r"risky|scary|complicated)\b",
           r"\bnot (?:only|just)\b", r"\bdont just\b",
           r"\b(?:fine|safe|ok|okay|alright|easy|free|allowed|simple) (?:for you )?to\b",
           r"\b(?:tell|told|tells|says?|said|show|shows|showed|want|wants|ask|asks|recommend|recommends|suggest|"
           r"suggests) (?:\w+ ){0,2}?(?:to|you can|you could|you may|its|it s|that)\b",
           r"\b(?:rather than|instead of|without) (?:calling|getting|asking|waiting|paying|booking)\b",
           r"\bno (?:engineer|electrician|technician|installer|professional|tools?)\b",
           r"^(?!.*\b(?:dont|do not|never|not|must not|mustnt|shouldnt|should not|isnt|arent|cant|cannot|wont|"
           r"wouldnt)\b).*\b(?:yourself|on your own)\b",
           r"\b(?:usually|normally|would normally|too expensive|expensive)\b",
           r"\bisnt (?:for|a job for)\b",
           r"\b(?:would|will) just\b", r"\byou can do (?:it|this|that|the same|exactly)\b", r"\bthe same\b",
           r"\bnothing dangerous\b", r"\bjust (?:comes|lifts|slides|pops|unclips)\b", r"\bonly if\b",
           r"\bwithout\b.*\b(?:not|never|cant|cannot|wont|dont|no way)\b",
           r"\b(?:not|never|cant|cannot|wont|dont|no way)\b.*\bwithout\b",
           r"^if (?:you |we )?(?:cant|cannot|can not|are unable|re unable|dont manage|fail)\b"),
    "nl": (r"\b(?:vergeet|vergeten|aarzel|twijfel|schroom|wacht|wees|ben|zijn) (?:\w+ )?niet\b",
           r"\bniet (?:\w+ )?(?:vergeten|bang|moeilijk|lastig|eng|gevaarlijk|nodig|alleen|zo moeilijk|ingewikkeld|"
           r"wachten|aarzelen|twijfelen)\b",
           r"\bgeen (?:zorgen|probleem|paniek|nood|monteur|elektricien|installateur|vakman|gereedschap|werk voor|"
           r"klus voor|moeite)\b",
           r"\b(?:hoeft|hoef|hoeven) (?:\w+ ){0,3}?(?:geen|niet)\b(?! (?:\w+ ){0,3}?niets)",
           r"\b(?:mag|mogen|kun|kunt|kan|kunnen) (?:je |jij |u )?(?:\w+ ){0,3}?(?:zelf|gewoon|prima|rustig)\b",
           r"\b(?:prima|veilig|makkelijk|geen probleem|oke|ok) (?:om|te)\b",
           r"\b(?:zegt|zei|zeggen|vertelt|raadt|adviseert|wil) (?:\w+ ){0,2}?(?:dat|om|je)\b",
           r"\bin plaats van\b", r"\b(?:meestal|normaal|normaliter|te duur|duur)\b",
           r"\bniet nodig\b", r"\bniets aan de hand\b", r"\bhetzelfde\b", r"\bniets gevaarlijks\b",
           r"\bzonder\b.*\b(?:niet|nooit|geen)\b", r"\b(?:niet|nooit)\b.*\bzonder\b",  # a double negative
           r"\balleen als\b", r"^als (?:je|jij|u) (?:\w+ ){0,4}?niet (?:\w+ )?(?:kunt|kan|lukt)\b"),
}
_PRONOUN = {"en": re.compile(r"\b(?:it|them|they|its|theirs)\b|\b(?:this|that|these|those) (?:off|out|open|up|away|"
                             r"apart|loose|in|down|back|through|round|around|hard|harder|firmly)\b|"
                             r"\b(?:this|that|these|those)$|\b(?:this|that|the) one\b"),
            "nl": re.compile(r"\b(?:hem|ze|zij|eraf|erop|eruit|erin|eraan|ervan|erachter|erbij|erdoor|eronder|"
                             r"erdoorheen|doorheen|ertegen|ertegenaan)\b|"
                             r"\ber (?:\w+ ){0,3}?(?:in|door|doorheen|achter|onder|tegen|tegenaan|op|af|uit|los)\b|"
                             r"\b(?:het|die|dat|deze|dit) (?:los|open|eraf|af|dicht|uit|aan|vast|weg|eruit|kapot)\b|"
                             r"\b(?:het|die|dat|deze|dit) (?:nu|helemaal|even|maar|dan|weer|goed|snel|zachtjes|"
                             r"voorzichtig) (?:los|open|eraf|af|dicht|uit|vast|weg|eruit|kapot)\b|"
                             r"\b(?:die|dat|deze|dit) (?:\w+ ){1,2}"
                             r"(?:los|open|eraf|af|dicht|uit|vast|weg|eruit|kapot)\b|"
                             r"\bgeef (?:het|die|dat|deze|dit) een\b|\ber (?:\w+ ){0,3}?aan\b|"
                             r"\b(?:het|die|dat|deze|dit)$")}


def _split(text: str, lang: str) -> list[tuple[str, bool]]:
    """(clause, opened by a conjunction rather than punctuation), each clause normalised."""
    out: list[tuple[str, bool]] = []
    for chunk in _PUNCT_BREAK.split(text):
        words: list[str] = []
        by_conj = False
        tokens = tx.normalise(chunk).split()
        for i, w in enumerate(tokens):
            if lang == "nl" and w == "als" and (i + 1 >= len(tokens) or tokens[i + 1] not in _SUBJECT):
                words.append(w)
                continue
            if w in CONJ.get(lang, ()) and words:
                out.append((" ".join(words), by_conj))
                words, by_conj = [], True
            words.append(w)
        if words:
            out.append((" ".join(words), by_conj))
    return out


def cued(clause: str, lang: str) -> bool:
    """Is a normalised clause a prohibition or a hand-off on its own ("without opening it")?"""
    return lang in CUES and bool(_cue(clause, lang, ()))


def points_back(clause: str, lang: str) -> bool:
    """Does a normalised clause refer back to something named before it ("prise it loose", "haal hem eraf")?"""
    return lang in _PRONOUN and _PRONOUN[lang].search(clause) is not None


def clauses(text: str, lang: str) -> list[str]:
    """The sentence's clauses, each normalised; a conjunction opens the clause it starts."""
    return [c for c, _ in _split(text, lang)]


@cache
def _compiled(lang: str) -> tuple[tuple[re.Pattern[str], ...], tuple[re.Pattern[str], ...]]:
    return tuple(re.compile(c) for c in CUES[lang]), tuple(re.compile(a) for a in ANTI[lang])


# Verbs that act only under a negation ("kom niet aan", "zit er niet aan"); alone they describe ("de brander zit
# achter het paneel"). Table verbs that describe as often as they act are not counted ("the pipe holds refrigerant").
NEG_ONLY = {"en": ("cover", "switch", "service", "vent", "drain", "handle", "lever", "work", "top", "seal", "press",
                  "block"),
            "nl": ("kom", "ga", "zit", "druk")}
STATIVE = frozenset({"hold", "feel", "voel", "reach"})


def _is_action(word: str, lang: str, extra: tuple[str, ...], negated: bool = False) -> bool:
    verbs = ACTION[lang] + tuple(v for v in extra if v not in STATIVE) + (NEG_ONLY[lang] if negated else ())
    return any(tx.verb_form(word, v, lang) for v in verbs if " " not in v)


_ARTICLE = {"en": ("a", "an", "the", "this", "that", "your", "its"), "nl": ("een", "de", "het", "je", "die", "dat")}


def _adjective(words: list[str], i: int, lang: str) -> bool:
    """A participle after an article describes, it does not act: "a cracked heat exchanger", "de losgeschroefde"."""
    w = words[i]
    participle = w.endswith(("ed", "en")) if lang == "en" else w.startswith(("ge", "afge", "losge", "opge"))
    return participle and i > 0 and words[i - 1] in _ARTICLE.get(lang, ())


def has_action(clause: str, lang: str, extra: tuple[str, ...] = ()) -> bool:
    padded = f" {clause} "
    words = clause.split()
    return any(_is_action(w, lang, extra) and not _adjective(words, i, lang) for i, w in enumerate(words)) or any(
        f" {v} " in padded for v in ACTION[lang] + extra if " " in v)


def _negates(clause: str, lang: str, extra: tuple[str, ...]) -> bool:
    """A negation whose next content word is an action verb ("never take", "must never be blocked")."""
    words = clause.split()
    joined = f" {clause} "
    for neg in _NEG[lang]:
        start = 0
        while (i := joined.find(f" {neg} ", start)) >= 0:
            start = i + 1
            rest = joined[i + len(neg) + 2:].split()
            if lang == "nl":
                # Dutch puts the verb anywhere: an action verb in the clause, and the clause is a command or has a
                # modal ("haal de kap er nooit af", "de afvoer mag nooit afgedekt worden").
                # Dutch puts the verb anywhere ("haal de kap er nooit af", "aan de brander zit je niet"): a negation and
                # an action verb in the same clause, the anti-cues already ruled out.
                if any(_is_action(w, lang, extra, negated=True) for w in words):
                    return True
                continue
            for w in rest:
                if w in SKIP[lang]:
                    continue
                tail = f" {' '.join(rest)} "
                if _is_action(w, lang, extra, negated=True) or any(f" {v} " in tail for v in ACTION[lang]
                                                                   if " " in v and v.startswith(w)):
                    return True
                break
    return False


def _cue(clause: str, lang: str, extra: tuple[str, ...]) -> str:
    """"neg" for a prohibition, "cue" for a hand-off or a keep-away, "" for neither."""
    cues, anti = _compiled(lang)
    if any(a.search(clause) for a in anti):
        return ""
    if _negates(clause, lang, extra):
        return "neg"
    return "cue" if any(c.search(clause) for c in cues) else ""


def is_warning(text: str, lang: str, names_part: Callable[[str], bool], verbs: tuple[str, ...] = ()) -> bool:
    """True when every clause that acts on a part (by name or by pronoun) is itself a prohibition or a hand-off, and
    at least one clause is. `names_part(clause)` says whether a normalised clause names a held part; `verbs` are the
    table's verbs for this language, which count as actions too."""
    if lang not in CUES:
        return False
    split = _split(text, lang)
    parts = [c for c, _ in split]
    kinds = [_cue(c, lang, verbs) for c in parts]
    for i in range(1, len(parts)):
        # "never cover or block it": the second verb is under the same negation.
        if not kinds[i] and kinds[i - 1] == "neg" and split[i][1] and parts[i].split()[0] in OR[lang]:
            kinds[i] = "neg"
    # "without opening it" is a prohibition, but next to another negation it is a double one: "never touch the drum
    # without removing the back panel", "zonder de achterwand eraf te halen kom je niet bij de pomp".
    for i, c in enumerate(parts):
        if c.split()[0] in ("without", "zonder") and "neg" in kinds[max(0, i - 1):i] + kinds[i + 1:i + 2]:
            kinds[i] = ""
            kinds[max(0, i - 1):i + 2] = ["" if k == "neg" else k for k in kinds[max(0, i - 1):i + 2]]
    cued = [bool(k) for k in kinds]
    # A limit after a prohibition ("don't remove it while it's plugged in") voids it: the act is fine later.
    for i in range(len(parts) - 1):
        first = parts[i + 1].split()[0]
        if cued[i] and not cued[i + 1] and (first in LIMITS[lang] or (first in IF_LIMITS[lang] and kinds[i] == "neg")):
            cued[i] = False
    if not any(cued):
        return False
    for c, ok in zip(parts, cued, strict=True):
        if ok:
            continue
        if has_action(c, lang, verbs) and (names_part(c) or _PRONOUN[lang].search(c)):
            return False
    return True
