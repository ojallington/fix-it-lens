"""Text normalisation and phrase matching for the rule table, shared by G0 and check_step.

Part names match at the start of a word, so plurals and endings still match ("back panels"). In languages that write
compounds as one word (Dutch, German) they match anywhere, so "hoofdgaskraan" holds on "gaskraan": a missed hold is
worse than a false one. Verbs match at the start of a word, so "unscrew" also catches "unscrewing".
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_SEP = re.compile(r"[\s\-_/·]+")
_PUNCT = re.compile(r"[^\w\s]")


def normalise(text: str) -> str:
    """Lower case, accents folded, punctuation dropped, hyphens and runs of space as one space, padded with spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("’", "'").replace("'", "")
    text = _SEP.sub(" ", _PUNCT.sub(" ", text))
    return f" {' '.join(text.split())} "


COMPOUND_LANGS = frozenset({"nl", "de"})


_NL_LONG = re.compile(r"([aeou])\1([^aeiou\s])$")


def find_part(text: str, part: str, lang: str) -> int:
    """Index of the part name in normalised text, or -1. In Dutch a plural may shorten the vowel ("zijpaneel",
    "zijpanelen"; "draad", "draden"), so that form is looked for too."""
    needle = normalise(part).strip()
    if lang not in COMPOUND_LANGS:
        return text.find(f" {needle}")
    at = text.find(needle)
    if at < 0 and _NL_LONG.search(needle):
        at = text.find(_NL_LONG.sub(r"\1\2en", needle))
    return at


def has_verb(text: str, verb: str) -> bool:
    return f" {normalise(verb).strip()}" in text


def mask(text: str, phrase: str) -> str:
    """Blank out every occurrence of an allowed phrase so broader rules cannot match inside it."""
    needle = normalise(phrase).strip()
    return text.replace(needle, " " * len(needle)) if needle else text


def line_hash(line: str) -> str:
    """The look-only exemption hash: sha256 of the line exactly as written, outer whitespace stripped."""
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()


def word_count(text: str) -> int:
    return len(text.split())


# Verb endings a stem takes ("unscrewing", "removed"; "verwijderen", "draait"), and the Dutch separable particles and
# past-participle "ge-" a stem may carry ("losgeschroefd", "afgedekt").
_EN_END = re.compile(r"^(?:|s|es|ed|d|ing|n|en|[bdgklmnprt](?:ing|ed))$")
_NL_END = re.compile(r"^(?:|t|en|e|d|de|te|den|ten|n|[bdfgklmnprst](?:en|e|end))$")
_NL_PARTICLES = ("eraf", "af", "aan", "los", "open", "uit", "weg", "dicht", "op", "vast", "door", "bij", "er")


def _stems(v: str, lang: str) -> tuple[str, ...]:
    if lang != "nl":
        return (v,)
    out = {v}
    for a, b in (("aa", "a"), ("ee", "e"), ("oo", "o"), ("uu", "u")):
        if a in v[-3:]:
            out.add(v[:-3] + v[-3:].replace(a, b))
    out |= {x[:-1] + {"f": "v", "s": "z"}[x[-1]] for x in list(out) if x[-1] in "fs"}
    return tuple(out)


def verb_form(word: str, v: str, lang: str) -> bool:
    """Is `word` a form of verb stem `v`: "unscrewing" of "unscrew", "losgeschroefd" of "schroef"? Not "doek" of
    "doe" or "pressure" of "press"."""
    if lang != "nl":
        if word.startswith(v) and _EN_END.match(word[len(v):]):
            return True
        # "removing", "prised": a final e drops before -ing and -ed
        return v.endswith("e") and word.startswith(v[:-1]) and word[len(v) - 1:] in ("ing", "ed", "es")
    forms = [word]
    for p in _NL_PARTICLES:
        if word.startswith(p) and len(word) > len(p) + 2:
            forms.append(word[len(p):])
    forms += [f[2:] for f in forms if f.startswith("ge")]
    return any(f.startswith(st) and _NL_END.match(f[len(st):]) for f in forms for st in _stems(v, lang))
