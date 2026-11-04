"""A pronoun that works on a held part named in the sentence before, without opening it in so many words, holds:
"I can see the housing." then "Drill through it." (review of 684e114: these passed on main). A twist counts on any
held part. Safe lines after a held part still pass."""

from __future__ import annotations

import pytest

from rules import Hold, Pass, check_sentence

COUNTRIES = ["nl", "uk", "us", "de"]
BEFORE = "I can see the housing."
MUST_HOLD = [
    "Reach into it with a screwdriver.", "Drill through it.", "Break it apart.", "Press on it until it gives way.",
    "Unplug the connector behind it.", "Poke the terminals in it.", "Then you can reach the wires behind it.",
    "Once it's removed, the terminals are bare.", "Give it a hard twist anticlockwise.",
    "You took it out, so give it a hard twist anticlockwise.", "Push on it until it cracks.",
    "Stick a screwdriver into it.",
]
SAFE_AFTER_HELD = [
    "Lift the filter out and rinse it under the tap.", "Push the basket back in.",
    "Press the start button and hold it for 3 seconds.", "Leave it alone and call a technician.",
]
NL_BEFORE = "Ik zie de behuizing."
NL_MUST_HOLD = ["Boor erdoor.", "Boor er doorheen.", "Druk erop tot hij meegeeft.", "Steek de schroevendraaier erin.",
                "Pak de draden erachter.", "Reik erin met een schroevendraaier."]
NL_SAFE = ["Spoel de zeef af en zet hem terug.", "Kijk eronder of er water staat."]


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", MUST_HOLD)
def test_working_on_a_held_part_by_pronoun_holds(country: str, text: str) -> None:
    r = check_sentence(text, "en", None, None, country=country, before=BEFORE)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.mains.casing", text


@pytest.mark.parametrize("country", COUNTRIES)
def test_wires_inside_it_hold_as_wiring(country: str) -> None:
    r = check_sentence("Touch the wires inside it.", "en", None, None, country=country, before=BEFORE)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.mains.wiring"


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", MUST_HOLD)
def test_with_nothing_held_before_it_passes(country: str, text: str) -> None:
    r = check_sentence(text, "en", None, None, country=country, before="Lift out the coarse filter.")
    assert isinstance(r, Pass) or not r.rule_id.endswith("mains.casing"), text


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", SAFE_AFTER_HELD)
def test_a_safe_line_after_a_held_part_passes(country: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country, before=BEFORE), Pass), text


TWISTS = [("Find the gas pipe.", "Give it a hard twist.", "gas.pipe"),
          ("That is the back panel.", "Give it a hard twist.", "mains.casing"),
          ("There is the gas pipe.", "Wrench it to the left.", "gas.pipe"),
          ("I can see the back panel.", "Twist it until it comes loose.", "mains.casing")]


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize(("before", "text", "zone"), TWISTS)
def test_a_twist_on_any_held_part_holds(country: str, before: str, text: str, zone: str) -> None:
    r = check_sentence(text, "en", None, None, country=country, before=before)
    assert isinstance(r, Hold) and r.rule_id.split(".", 1)[1].startswith(zone.split(".")[0]), (before, text, r)


@pytest.mark.parametrize("country", COUNTRIES)
def test_a_twist_after_no_held_part_passes(country: str) -> None:
    r = check_sentence("Give it a hard twist.", "en", None, None, country=country, before="Lift out the coarse filter.")
    assert isinstance(r, Pass)


@pytest.mark.parametrize("text", NL_MUST_HOLD)
def test_dutch_holds(text: str) -> None:
    r = check_sentence(text, "nl", None, None, country="nl", before=NL_BEFORE)
    assert isinstance(r, Hold) and r.rule_id == "nl.mains.casing", text


@pytest.mark.parametrize("text", NL_SAFE)
def test_dutch_safe_passes(text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl", before=NL_BEFORE), Pass), text


# Second review round (pre-existing gaps on 2cab007).
ROUND2_HOLD = [
    ("I can see the housing.", t) for t in (
        "Yank it hard.", "Rip it off.", "Tug on it.", "Wiggle it loose.", "Jiggle it until it moves.", "Rotate it.",
        "Turn it a quarter.", "Tap it with a hammer.", "Pull it.", "Puncture it.", "Put your hand inside it.",
        "Give it a good yank.", "Push that in.", "Press this hard.", "Take the back of it off.",
        "Remove the back of it.", "Unscrew that one.", "Remove the one on the left.")
] + [("Find the gas valve.", "Push and turn it."), ("Find the gas valve.", "Turn it a quarter turn.")]
ROUND2_PASS = [
    ("Lift out the coarse filter.", "Turn it clockwise to lock."), ("Lift out the coarse filter.", "Pull it out."),
    ("Pull out the upper rack.", "Pull it out."), ("Slide the lower rack back in.", "Turn it clockwise to lock."),
    ("Lift out the coarse filter.", "Push that in."), ("Take the filter out.", "Twist the filter and pull it out."),
]
NL_ROUND2_HOLD = ["Steek er een schroevendraaier in.", "Boor er een gat in.", "Duw ertegen.",
                  "Draai hem een kwartslag."]
NL_ROUND2_PASS = [
    ("Haal de grove zeef eruit.", "Draai hem een kwartslag."), ("Haal de zeef eruit.", "Steek hem er weer in."),
]


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize(("before", "text"), ROUND2_HOLD)
def test_round2_holds(country: str, before: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country, before=before), Hold), (before, text)


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize(("before", "text"), ROUND2_PASS)
def test_round2_user_parts_pass(country: str, before: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country, before=before), Pass), (before, text)


@pytest.mark.parametrize("text", NL_ROUND2_HOLD)
def test_round2_dutch_split_forms_hold(text: str) -> None:
    r = check_sentence(text, "nl", None, None, country="nl", before=NL_BEFORE)
    assert isinstance(r, Hold) and r.rule_id == "nl.mains.casing", text


@pytest.mark.parametrize(("before", "text"), NL_ROUND2_PASS)
def test_round2_dutch_pass(before: str, text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl", before=before), Pass), (before, text)


# Third review round: a plural or a tool named in an earlier clause is not what "it" means, so the carry still holds.
HELD_BEFORE = ["I can see the housing.", "Find the gas pipe.", "Find the gas valve.", "That is the back panel."]
ROUND3_HOLD = [
    "Unscrew the screws, then drill through it.", "Remove the screws and reach into it.",
    "Take the screws out and put your hand in it.", "Take the screwdriver and stick it in it.",
    "Undo the clips and pull it off.", "With the pliers, twist it.", "Using a screwdriver, pry it open.",
]
NL_HELD_BEFORE = ["Ik zie de behuizing.", "Zoek de gasleiding.", "Dat is het achterpaneel."]
NL_ROUND3_HOLD = ["Draai de schroeven los en boor erdoor.", "Pak een schroevendraaier en steek hem erin."]
# A singular part named in an earlier clause is what the pronoun means.
ROUND3_PASS = [("I can see the back panel.", "Open the filter flap and pull it out.", "en"),
               # "they" and "them" can mean a plural named earlier
               ("I can see the housing.", "Clip the spray arms back on and spin them by hand to check that they turn "
                "freely.", "en"),
               ("That is the back panel.", "The debris filter catches small objects before they reach the pump.", "en"),
               ("Find the gas pipe.", "Undo the clips and pull them off.", "en")]


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("before", HELD_BEFORE)
@pytest.mark.parametrize("text", ROUND3_HOLD)
def test_round3_a_plural_or_tool_before_the_pronoun_still_holds(country: str, before: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country, before=before), Hold), (before, text)


@pytest.mark.parametrize("before", NL_HELD_BEFORE)
@pytest.mark.parametrize("text", NL_ROUND3_HOLD)
def test_round3_dutch_holds(before: str, text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl", before=before), Hold), (before, text)


@pytest.mark.parametrize(("before", "text", "lang"), ROUND3_PASS)
def test_round3_a_part_named_earlier_takes_the_pronoun(before: str, text: str, lang: str) -> None:
    assert isinstance(check_sentence(text, lang, None, None, country="nl", before=before), Pass), (before, text)


# Fourth review round: where the pronoun is the work site, a thing named in an earlier clause does not take it; safety
# wins. Every clause is checked, not only the first that opens something. More tools.
ROUND4_HOLD = [
    "Remove the cover and reach into it.", "Remove the cover and reach behind it.",
    "Undo the clip and drill through it.",
    "Take off the lid and drill through it.", "Remove the glass and reach into it.",
    "Remove the insulation and reach into it.", "Find the hole and stick your finger in it.",
    "Take off the bracket and reach into it.", "Use a chisel and break it.", "Take a crowbar and break it.",
    "Get a pry bar and push it.", "Use a flathead and poke it.",
]
NL_ROUND4_HOLD = ["Zoek de rand en druk erop.", "Neem de beitel en breek hem open.",
                  "Pak een breekijzer en breek hem open.",
                  "Pak de inbussleutel en draai hem.", "Haal de schroeven eruit en reik erin.",
                  "Haal de afdekking eraf en reik erin.", "Pak het mes en snijd erin."]
FILTERKLEPJE = "Het filterklepje zit onderaan de voorkant en gaat open als je er met een muntje op drukt."


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("before", HELD_BEFORE)
@pytest.mark.parametrize("text", ROUND4_HOLD)
def test_round4_a_work_site_pronoun_holds_after_a_named_thing(country: str, before: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country, before=before), Hold), (before, text)


@pytest.mark.parametrize("before", NL_HELD_BEFORE)
@pytest.mark.parametrize("text", NL_ROUND4_HOLD)
def test_round4_dutch_holds(before: str, text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl", before=before), Hold), (before, text)


@pytest.mark.parametrize("before", ["", "Haal de zeef eruit.", "Open de deur van de vaatwasser."])
def test_the_filter_flap_line_passes_on_its_own_and_after_a_user_part(before: str) -> None:
    # After a held part it holds: "er ... op" is a work site, and that over-hold is accepted.
    assert isinstance(check_sentence(FILTERKLEPJE, "nl", None, None, country="nl", before=before), Pass), before


def test_the_filter_flap_line_holds_after_a_held_part() -> None:
    assert isinstance(check_sentence(FILTERKLEPJE, "nl", None, None, country="nl", before=NL_BEFORE), Hold)


# Additions on 0e734e8 (review 8). Touching terminals is wiring; a battery's own are not, unless the sentence names a
# mains place too. A fuse is never what a pronoun means. Dutch demonstratives, "geef het een", "er ... aan" and "grijp"
# carry. The user-part exception is parked until after submit, so the over-holds it would lift still hold.
FIVE_HELD = ["I can see the housing.", "Find the gas pipe.", "Find the gas valve.", "That is the back panel.",
             "Look at the mains casing."]
ADD_HOLD = ["Pull the fuse, then touch it.", "Unplug the fuse and grab it.", "Remove the fuse and touch it.",
            "Take the fuse out and touch it."]
NL_ADD_HOLD = ["Draai die kraan open.", "Draai die nu los.", "Draai die helemaal open.", "Draai het helemaal los.",
               "Geef het een tik.", "Geef het een ruk.", "Geef er een ruk aan.",
               "Haal de zekering eruit en raak hem aan.",
               "Haal de lade eruit en grijp erachter."]
STILL_HELD = [("Find the gas valve.", "Lift the filter upwards and turn it and that.", "en"),
              ("Ik zie de behuizing.", "Haal het rek eruit en draai het ding los.", "nl"),
              ("I can see the housing.", "Pull the rack towards you and push it back.", "en")]
TERMINALS_HOLD = ["Touch the terminals.", "Pull the fuse and touch the terminals.",
                  "Remove the battery and touch the terminals.", "Touch the battery terminals inside the mains box.",
                  "Touch the terminals of the battery inside the mains box.",
                  "Touch the battery terminals and the terminals behind them."]
TERMINALS_PASS = ["Touch the battery terminals.", "Clean the battery terminals.", "Wipe the terminals of the battery.",
                  "Touch the terminals of the battery.", "Don't touch the terminals."]


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("before", FIVE_HELD)
@pytest.mark.parametrize("text", ADD_HOLD)
def test_a_fuse_is_never_what_the_pronoun_means(country: str, before: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country, before=before), Hold), (before, text)


@pytest.mark.parametrize("before", ["Ik zie de behuizing.", "Zoek de gasleiding."])
@pytest.mark.parametrize("text", NL_ADD_HOLD)
def test_dutch_additions_hold(before: str, text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl", before=before), Hold), (before, text)


@pytest.mark.parametrize(("before", "text", "lang"), STILL_HELD)
def test_the_user_part_exception_is_parked(before: str, text: str, lang: str) -> None:
    assert isinstance(check_sentence(text, lang, None, None, country="nl", before=before), Hold), (before, text)


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", TERMINALS_HOLD)
def test_touching_terminals_is_wiring(country: str, text: str) -> None:
    r = check_sentence(text, "en", None, None, country=country)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.mains.wiring", text


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", TERMINALS_PASS)
def test_a_batterys_own_terminals_pass(country: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country), Pass), text


# Review 9: the zone a pronoun carries comes first, a joined conductor or touched terminal after it, never in its place.
DISPLACED = [("Find the gas valve.", "Turn it and touch the terminals.", "gas.tap"),
             ("Find the gas pipe.", "Loosen it and touch the terminals.", "gas.tap"),
             ("That is the back panel.", "Pull it off and touch the terminals.", "mains.casing"),
             ("", "Find the gas valve, turn it and touch the terminals.", "gas.tap"),
             ("", "Remove the back panel, then reach in and touch the terminals.", "mains.casing")]
FUSE_IN_PLUG = ["Remove the fuse from the plug and turn it.", "Replace the fuse in the plug, then turn it a quarter."]


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize(("before", "text", "zone"), DISPLACED)
def test_the_carried_zone_comes_before_the_terminals(country: str, before: str, text: str, zone: str) -> None:
    r = check_sentence(text, "en", None, None, country=country, before=before)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.{zone}", (before, text)


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", FUSE_IN_PLUG)
def test_a_fuse_named_anywhere_keeps_the_carry(country: str, text: str) -> None:
    r = check_sentence(text, "en", None, None, country=country, before="Find the gas valve.")
    assert isinstance(r, Hold) and r.rule_id == f"{country}.gas.tap", text


@pytest.mark.parametrize("text", ["Draai die kraan helemaal open.", "Draai die kraan nu los."])
def test_two_words_before_the_particle_hold(text: str) -> None:
    r = check_sentence(text, "nl", None, None, country="nl", before="Zoek de gaskraan.")
    assert isinstance(r, Hold) and r.rule_id == "nl.gas.tap", text
