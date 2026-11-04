"""fixit-rules: the rule engine, G0 (the sentence filter), the safety state machine, the stop scripts, the country
files and the G1 constants. Imports nothing from app/, ingest/, eval/ or calls/.

    from rules import check_sentence, check_step, SafetyEngine, stop_script, load_country
    check_sentence("Remove the back panel.", "en", card, state, country="nl")  # Hold(rule_id="nl.mains.casing", ...)

Safety stays code and stays on: there is no flag, env var or setting here that turns a gate off.
"""

from rules.cards import check_card
from rules.country import Country, RulesError, load_country
from rules.disclaimer import disclaimer
from rules.engine import SafetyEngine, SafetyError, stop_script
from rules.g0 import Hold, Pass, check_sentence, warning_for
from rules.landlord import landlord_jobs
from rules.steps import Evidence, StepHold, StepOk, StepStop, check_step, guarded

__all__ = [
    "Country",
    "Evidence",
    "Hold",
    "Pass",
    "RulesError",
    "SafetyEngine",
    "SafetyError",
    "StepHold",
    "StepOk",
    "StepStop",
    "check_card",
    "check_sentence",
    "check_step",
    "disclaimer",
    "guarded",
    "landlord_jobs",
    "load_country",
    "stop_script",
    "warning_for",
]
