"""The whole-job check on invented page text: each failure class, and the near misses it must let through."""

from __future__ import annotations

import pytest

from ingest.citation import locate, norm
from ingest.jobcheck import (
    Failure,
    Step,
    check,
    duplicate_steps,
    gate,
    leaves_state,
    own_restores,
    precondition_missing,
    refers_outside,
    restorable,
    steps_skipped,
    warnings_dropped,
)

PAGE = """Cleaning the drain filter
Unplug the machine from the socket before you start.
Open the filter flap at the bottom right.
Turn the drain filter anticlockwise and take it out.
Rinse the drain filter under running water.
Put the drain filter back and turn it clockwise.
Close the filter flap and push it until it clicks.
Put the mains plug back into the socket.
"""

EMERGENCY = """Opening the door in an emergency
Requirement: the drum has stopped and the water has drained away.
WARNING Hot water can scald you. Wait until the water has cooled down.
Pull the emergency release lever down with a coin.
The door opens and you can take out the laundry.
"""


def s(text: str, quote: str, page: int = 1) -> Step:
    return Step(text, quote, page)


WHOLE = [s("Unplug the machine.", "Unplug the machine from the socket before you start."),
         s("Open the filter flap.", "Open the filter flap at the bottom right."),
         s("Take the drain filter out.", "Turn the drain filter anticlockwise and take it out."),
         s("Rinse the filter.", "Rinse the drain filter under running water."),
         s("Put the filter back.", "Put the drain filter back and turn it clockwise."),
         s("Close the filter flap.", "Close the filter flap and push it until it clicks."),
         s("Plug the machine back in.", "Put the mains plug back into the socket.")]


def test_a_whole_job_passes() -> None:
    assert check(WHOLE, [PAGE], "washer") == []


def test_unplugged_and_never_plugged_back_in_fails_on_any_appliance() -> None:
    assert [f.detail for f in check(WHOLE[:-1], [PAGE], "washer")] == ["mains off, never back on"]


def test_switched_off_is_normal_for_a_washer_but_not_for_a_boiler() -> None:
    job = [s("Switch the appliance off.", "Unplug the machine from the socket before you start."),
           s("Read the pressure on the display.", "Rinse the drain filter under running water.")]
    assert leaves_state(job, [PAGE], "washer") == []
    pronoun = [*WHOLE[:4], s("Put it back and close the filter flap.", "Close the filter flap and push it until it "
                             "clicks."), WHOLE[-1]]
    assert leaves_state(pronoun, [PAGE], "washer") == []
    assert [f.detail for f in leaves_state(job, [PAGE], "boiler")] == ["power off, never back on"]


def test_a_tap_opened_first_starts_closed_so_closing_it_at_the_end_is_whole() -> None:
    job = [s("Open the water tap.", "x" * 30), s("Close the water tap when the pressure is 2 bar.", "y" * 30)]
    assert leaves_state(job, [PAGE], "boiler") == []
    closed = [s("Close the water tap.", "x" * 30), s("Clean the inlet filter.", "y" * 30)]
    assert [f.detail for f in leaves_state(closed, [PAGE], "washer")] == ["water_supply off, never back on"]
    back_on = [s("Turn the water tap off.", "x" * 30), s("Turn the water tap back on.", "y" * 30)]
    assert leaves_state(back_on, [PAGE], "washer") == []


def test_a_tap_the_job_first_connects_its_hose_to_starts_closed() -> None:
    job = [s("Connect the filling hose to the water tap and fill it with water.", "x" * 30),
           s("Close the water tap and the fill cock.", "y" * 30)]
    assert leaves_state(job, [PAGE], "boiler") == []
    # negative control: a hose connected elsewhere says nothing about the tap
    elsewhere = [s("Connect the drain hose to the siphon.", "x" * 30), s("Close the water tap.", "y" * 30)]
    assert [f.detail for f in leaves_state(elsewhere, [PAGE], "washer")] == ["water_supply off, never back on"]


def test_a_part_the_job_brings_and_a_removal_it_forbids_leave_nothing_out() -> None:
    job = [s("Connect the filling hose to the water tap.", "x" * 30),
           s("Close the water tap and the fill cock, but do not remove the hose yet.", "y" * 30),
           s("Bleed the radiators.", "z" * 30),
           s("Remove the filling hose.", "w" * 30)]
    assert leaves_state(job, [PAGE], "boiler") == []
    assert gate(job, [PAGE], "boiler") == []
    # negative controls: a hose the machine has, taken off and never refitted, still fails; so does a removal not
    # forbidden
    out = ["hose removed or opened at step 1; never refitted"]
    own = [s("Remove the drain hose.", "x" * 30), s("Rinse it under the tap.", "y" * 30)]
    assert [f.detail for f in leaves_state(own, [PAGE], "washer")] == out
    assert [f.detail for f in gate(own, [PAGE], "washer")] == out
    told = [s("Close the water tap, then remove the hose.", "x" * 30), s("Rinse it.", "y" * 30)]
    assert [f.detail for f in gate(told, [PAGE], "washer")] == out


def test_only_a_removal_said_not_and_only_a_part_first_connected_are_let_through() -> None:
    flap = ["flap removed or opened at step 1; never refitted"]
    # a "not" in another clause does not excuse the removal that follows it
    other = [s("Do not use hot water; remove the filter flap.", "x" * 30), s("Rinse it.", "y" * 30)]
    assert [f.detail for f in leaves_state(other, [PAGE], "washer")] == flap
    assert [f.detail for f in gate(other, [PAGE], "washer")] == flap
    # a part first pushed, fitted or reconnected is the machine's own: only connect or attach brings one
    for verb in ("Push", "Fit", "Reconnect"):
        job = [s(f"{verb} the filter flap.", "x" * 30), s("Remove the filter flap.", "y" * 30),
               s("Rinse it.", "z" * 30)]
        assert [f.detail for f in gate(job, [PAGE], "washer")] == ["flap removed or opened at step 2; never refitted"]
    # a part taken off before the job connects it again is the machine's own: taken off once more, it still fails
    again = [s("Remove the drain hose.", "x" * 30), s("Connect the drain hose.", "y" * 30),
             s("Remove the drain hose.", "z" * 30)]
    assert [f.detail for f in gate(again, [PAGE], "washer")] == ["hose removed or opened at step 3; never refitted"]
    # the tap: closed before the job connects its hose, it started open and stays closed
    tap = [s("Close the water tap.", "x" * 30), s("Connect the inlet hose to the water tap.", "y" * 30)]
    assert [f.detail for f in leaves_state(tap, [PAGE], "washer")] == ["water_supply off, never back on"]


def _refused(texts: list[str], kind: str = "washer") -> bool:
    job = [s(t, chr(97 + i) * 30) for i, t in enumerate(texts)]
    return bool(leaves_state(job, [PAGE], kind) + gate(job, [PAGE], kind))


def test_each_relaxation_holds_only_for_its_own_verb_part_and_tap() -> None:
    # a "do not" or "never" must open the clause of the very removal it forbids; a warning word elsewhere is no excuse
    for said in ("Do not spill; remove the filter.", "Never mind it, take out the filter.", "Niet vergeten: remove "
                 "the filter.", "Whether dirty or not remove the filter.", "If not, remove the filter."):
        assert _refused([said, "Rinse."]), said
    assert not _refused(["Don\u2019t remove the hose yet.", "Wipe the drum."])
    # a part is brought only by its own name, once, by a connect that is not forbidden and that comes before
    for job in (["Attach the drawer handle.", "Remove the drawer.", "Clean."],
                ["Attach the filter cover tool.", "Remove the filter cover.", "Clean."],
                ["Connect the hose to the inlet.", "Fill.", "Remove the hoses at the back.", "Wipe."],
                ["Connect the filling hose to the inlet.", "Fill.", "Remove the filling hose.",
                 "Remove the drain hose and empty it."],
                ["Connect the drain hose.", "Drain the water.", "Remove the hose and the filter.", "Wipe."],
                ["Do not connect the filling hose yet.", "Remove the filling hose.", "Rinse."]):
        assert _refused(job), job
    assert not _refused(["Attach the hose and the cover.", "Fill.", "Remove the cover.", "Wipe."])
    # the hose first connected to the tap lets only that tap close again: a valve or supply closed stays closed
    for job in (["Connect the filling hose to the water tap.", "Open the water tap.",
                 "Close the main water supply valve.", "Close the water tap.", "Disconnect the filling hose."],
                ["Connect the hose to the tap.", "Turn off the water supply.", "Bleed the radiators."],
                ["Do not connect the hose to the tap yet. Close the water supply valve.", "Bleed the radiators."],
                ["Connect the hose to the water tap.", "Close the water tap and the inlet valve.", "Wipe."]):
        assert _refused(job, "boiler"), job
    assert not _refused(["Connect the filling hose to the water tap.", "Open the water tap.",
                         "Close the water tap and the fill cock.", "Disconnect the filling hose."], "boiler")


def test_any_negation_before_a_connect_brings_nothing_and_starts_no_tap() -> None:
    # a missed "not" before a connect would let a removal or a closed tap through, so every negation cancels it
    for said in ("You must not attach", "You should not attach", "Don\u00b4t attach", "Don`t attach"):
        assert _refused([f"{said} the cover.", "Remove the cover.", "Wipe."]), said
    for said in ("You must not connect", "Don\u00b4t connect"):
        assert _refused([f"{said} the hose to the tap yet.", "Close the water tap."]), said
    # the part is brought under its name with its article: "a hose" attached is not "the hose" removed
    assert _refused(["Attach a hose to the inlet.", "Remove the hose.", "Wipe."])


def test_only_a_bare_connect_opening_its_clause_brings_a_part_or_starts_a_tap() -> None:
    # any other wording brings nothing: the check refuses rather than guess at what was meant
    for said in ("No need to attach the cover.", "Neither open the flap nor attach the cover.",
                 "Neither attach the cover nor the hose.", "Avoid attaching the cover.",
                 "Refrain from attaching the cover.", "Instead of attaching the cover, wipe it.",
                 "Without attaching the cover first.", "Geen: attach the cover.", "Zonder attach the cover.",
                 "Nog niet attach the cover.", "Attach the cover never.", "YOU MUST NOT ATTACH THE COVER.",
                 "Unattach the cover."):
        assert _refused([said, "Remove the cover.", "Wipe."]), said
    assert _refused(["Connect the hose not yet.", "Remove the hose.", "Wipe."])
    for said in ("No need to connect the hose to the tap.", "Avoid connecting the hose to the tap.",
                 "Connect the hose to the tap not yet.", "DON\u00b4T CONNECT THE HOSE TO THE TAP YET."):
        assert _refused([said, "Close the water tap."]), said
    assert _refused(["Without connecting the hose to the tap, close the water tap."])
    assert not _refused(["Connect the hose to the inlet.", "Remove the hose."])
    assert not _refused(["Connect the filling hose to the water tap.", "Open the water tap.", "Close the water tap.",
                         "Disconnect the filling hose."])


def test_a_connect_under_a_condition_or_a_question_brings_nothing() -> None:
    for said in ("Attach the cover once the drum is dry.", "Attach the cover when the drum is dry.",
                 "Attach the cover as soon as it is dry.", "Attach the cover eventually.",
                 "Optionally: attach the cover.", "Attach the cover optionally.", "Attach the cover if needed.",
                 "If needed: attach the cover.",
                 "Attach the cover only if it is missing.", "Attach the cover pas.", "Eerst: attach the cover.",
                 "Attach the cover straks.", "If the cover is missing then attach the cover.",
                 "Is the cover loose? Attach the cover.", "Attach the cover?", 'The label says "attach the cover".',
                 "The label reads: attach the cover."):
        assert _refused([said, "Remove the cover.", "Wipe."]), said
    assert _refused(["If the hose is loose, connect the hose.", "Remove the hose."])
    for said in ("Connect the hose to the tap if needed.", "Connect the hose to the tap when the boiler is cold.",
                 "Connect the hose to the tap?"):
        assert _refused([said, "Close the water tap."]), said
    # a job that points at its own steps may skip the connect
    assert _refused(["Steps 2 and 3 are not needed.", "Connect the hose.", "Fill.", "Remove the hose."])
    assert not _refused(["First, connect the hose.", "Remove the hose."])
    assert not _refused(["First connect the hose to the inlet.", "Fill.", "Remove the hose."])


def test_a_part_taken_out_and_never_refitted_fails() -> None:
    # flap open, filter out and rinsed, plugged in: the manual's next lines refit both
    cut = [*WHOLE[:4]]
    cut.append(s("Plug the machine back in.", "Rinse the drain filter under running water."))
    fails = leaves_state(cut, [PAGE], "washer")
    assert [f.code for f in fails] == ["leaves_state", "leaves_state"]
    assert {f.detail.split()[0] for f in fails} == {"flap", "filter"}
    # "the plinth flap": the part is the last part word of the phrase, so closing it counts
    job = [s("Open the plinth flap.", "x" * 30),
           s("Close the plinth flap.", "Close the filter flap and push it until it clicks.")]
    assert leaves_state(job, [PAGE], "washer") == []


def test_a_step_sending_the_person_to_another_procedure_fails() -> None:
    job = [s("Empty the drain pump, following the emergency drain procedure.", "x" * 30)]
    assert [f.code for f in refers_outside(job)] == ["refers_outside"]
    loop = [s("Repeat until all the water has drained.", "Repeat this procedure until all the water has drained.")]
    assert refers_outside(loop) == []
    repeat = [s("Bleed the radiators.", "Herhaal de procedure als er nog lucht in de installatie zit.")]
    assert [f.code for f in refers_outside(repeat)] == ["refers_outside"]
    reverse = [s("Refit them in reverse order.",
                 "Herhaal deze stappen in omgekeerde volgorde om ze terug te plaatsen.")]
    assert refers_outside(reverse) == []


def test_a_step_that_names_another_job_on_the_card_is_a_link() -> None:
    job = [s("Clean the filter combination, as described in the filter section.", "x" * 30)]
    assert refers_outside(job, ["Clean the filters"]) == []
    assert [f.code for f in refers_outside(job, ["Refill the salt"])] == ["refers_outside"]


def test_the_requirement_above_the_first_step_must_be_carried() -> None:
    bare = [s("Pull the emergency release down.", "Pull the emergency release lever down with a coin."),
            s("Open the door.", "The door opens and you can take out the laundry.")]
    assert [f.detail for f in precondition_missing(bare, [EMERGENCY])] == [
        '"requirement" above step 1 on page 1', '"warning" above step 1 on page 1']
    warned = [s("Wait until the water has cooled down.", "Hot water can scald you. Wait until the water has cooled "
                "down."), *bare]
    assert [f.detail for f in precondition_missing(warned, [EMERGENCY])] == ['"requirement" above step 1 on page 1']
    carried = [s("Check that the drum has stopped and the water has drained.", "Requirement: the drum has stopped and "
                 "the water has drained away."), *warned]
    assert precondition_missing(carried, [EMERGENCY]) == []


def test_a_warning_carried_by_its_own_sentence_under_a_short_title() -> None:
    page = ("WAARSCHUWING\nKans op letsel!\nScherpe voorwerpen kunnen de pomp blokkeren en tot verwondingen leiden.\n"
            "1. Haal de stekker van het apparaat uit het stopcontact.")
    warn = s("Take care: sharp objects can cut you.", "Scherpe voorwerpen kunnen de pomp blokkeren en tot "
             "verwondingen leiden.")
    unplug = s("Unplug the dishwasher.", "Haal de stekker van het apparaat uit het stopcontact.")
    assert precondition_missing([warn, unplug], [page]) == []
    assert [f.detail for f in precondition_missing([unplug], [page])] == ['"waarschuwing" above step 1 on page 1']
    # a requirement's own words are not a title: the step below them leaves the requirement out
    req = "Vereiste: het lampje knippert.\n1. De wasmiddellade uittrekken."
    pull = s("Pull out the detergent drawer.", "De wasmiddellade uittrekken.")
    assert [f.detail for f in precondition_missing([pull], [req])] == ['"vereiste" above step 1 on page 1']


def test_a_warning_word_inside_a_sentence_is_not_a_heading() -> None:
    page = ("Handle the glass with caution, warning: it breaks easily and that matters.\n"
            "Turn the lamp cover to remove it.")
    assert precondition_missing([s("Remove the lamp cover.", "Turn the lamp cover to remove it.")], [page]) == []


def test_a_job_that_jumps_to_another_page_is_checked_where_it_lands() -> None:
    table = "Fault table\nThe door does not open. There was a power cut, see the emergency release."
    job = [s("If the door does not open after a power cut, use the emergency release.",
             "The door does not open. There was a power cut, see the emergency release.", 1),
           s("Pull the emergency release down.", "Pull the emergency release lever down with a coin.", 2)]
    fails = precondition_missing(job, [table, EMERGENCY])
    assert [f.detail for f in fails] == ['"requirement" above step 2 on page 2', '"warning" above step 2 on page 2']


def test_only_a_step_that_repeats_the_one_before_is_a_duplicate() -> None:
    steps = [s("Rinse the drawer and the syphon in the sink with warm water.", "a" * 30),
             s("Rinse the drawer and the syphon with warm water.", "b" * 30),
             s("Reconnect the inlet hose at the tap and check it for leaks.", "c" * 30),
             s("Clean the filter in the machine.", "d" * 30),
             s("Reconnect the hose at the tap and check it for leaks.", "e" * 30)]
    assert duplicate_steps(steps) == [1]


def test_switching_back_on_does_not_undo_an_unplug() -> None:
    page = "Switch off, unplug, wait one minute and switch it back on.\n"
    reset = [s("Switch off and unplug the machine.", "Switch off, unplug, wait one minute"),
             s("Switch it back on.", "and switch it back on.")]
    assert [f.detail for f in leaves_state(reset, [page], "washer")] == ["mains off, never back on"]
    plugged = [*reset[:1], s("Plug the machine back in and switch it back on.", "and switch it back on.")]
    assert leaves_state(plugged, [page], "washer") == []


def test_a_part_left_out_fails_even_where_the_manual_stops() -> None:
    page = "Pull out the lower rack.\nRemove the spray arm.\nRinse the spray arm under running water.\n"
    arms = [s("Pull out the lower rack.", "Pull out the lower rack."),
            s("Remove the spray arm.", "Remove the spray arm."),
            s("Rinse the spray arm.", "Rinse the spray arm under running water.")]
    assert [f.detail for f in leaves_state(arms, [page], "dishwasher")] == [
        "rack removed or opened at step 1; never refitted", "arm removed or opened at step 2; never refitted"]
    back = [*arms, s("Refit the parts in reverse order.", "Rinse the spray arm under running water.")]
    assert leaves_state(back, [page], "dishwasher") == []


def test_a_warning_between_two_steps_of_the_job_must_be_carried() -> None:
    page = ("Unscrew the pump cover.\nWARNING\nThe water can be hot. Let it cool down first.\n"
            "Take out the pump filter.\n")
    job = [s("Unscrew the pump cover.", "Unscrew the pump cover."),
           s("Take out the pump filter.", "Take out the pump filter.")]
    assert [f.detail for f in precondition_missing(job, [page])] == ['"warning" above step 2 on page 1']
    carried = [job[0], s("Let the water cool down first.", "The water can be hot. Let it cool down first."), job[1]]
    assert precondition_missing(carried, [page]) == []


SHORT = PAGE.replace("Put the mains plug back into the socket.\n", "")  # the manual stops before plugging back in


def test_only_a_job_that_just_leaves_the_mains_off_may_be_finished_by_our_step() -> None:
    job = WHOLE[:-1]
    assert restorable(job, check(job, [SHORT], "washer"), [SHORT]) == [("mains", 0)]
    # the manual does say how to undo it (here, on the job's own page): the repair must cite it, never ours
    assert restorable(job, check(job, [PAGE], "washer"), [PAGE]) == []
    # negative control: the same job also fails for another reason (the filter never goes back)
    no_refit = [st for st in job if not st.text_en.startswith("Put the filter back")]
    fails = check(no_refit, [SHORT], "washer")
    assert {f.code for f in fails} == {"leaves_state"} and len(fails) == 2
    assert restorable(no_refit, fails, [SHORT]) == []
    outside = [*job[:3], s("Repeat the procedure for the second filter.", "Rinse the drain filter under running "
                           "water."), *job[4:]]
    assert restorable(outside, check(outside, [SHORT], "washer"), [SHORT]) == []
    assert restorable(job, [], [SHORT]) == []  # a job that passes needs nothing


def test_water_is_undone_before_the_mains_and_a_restore_line_on_the_next_page_counts() -> None:
    job = [s("Unplug the machine.", "Unplug the machine from the socket before you start."),
           s("Close the water tap.", "Close the water tap before you start."),
           s("Clean the inlet filter.", "Clean the inlet filter with a brush.")]
    page = "Unplug the machine from the socket before you start.\nClose the water tap before you start.\n" \
           "Clean the inlet filter with a brush.\n"
    assert restorable(job, check(job, [page], "washer"), [page]) == [("water_supply", 1), ("mains", 0)]
    assert restorable(job, check(job, [page, "Open the water tap fully."], "washer"),
                      [page, "Open the water tap fully."]) == []



def test_a_safety_chapters_plug_in_line_far_from_the_procedure_does_not_undo_the_job() -> None:
    safety = "Pull out the plug before any cleaning.\nPlug the machine in only after it is installed.\n"
    proc = "Wipe the drum with a damp cloth.\nLeave the door open to dry.\n"
    pages = [safety, "", "", proc, ""]
    job = [Step("Pull out the plug.", "Pull out the plug before any cleaning.", 1),
           Step("Wipe the drum.", "Wipe the drum with a damp cloth.", 4),
           Step("Leave the door open.", "Leave the door open to dry.", 4)]
    assert restorable(job, check(job, pages, "washer"), pages) == [("mains", 0)]
    # negative control: the same plug-in line beside the procedure itself is the manual's own way to undo it
    near = ["", "", safety, proc, ""]
    close = [Step(st.text_en, st.quote, 3) if i == 0 else st for i, st in enumerate(job)]
    assert restorable(close, check(close, near, "washer"), near) == []

RACK = ("Cleaning the filters\nSwitch the dishwasher off.\nPull the lower rack out.\nTake the filter out.\n"
        "Rinse the filter under running water.\nPut the filter back.\n")
RACK_JOB = [s("Switch the dishwasher off.", "Switch the dishwasher off."),
            s("Pull the lower rack out.", "Pull the lower rack out."),
            s("Take the filter out.", "Take the filter out."),
            s("Rinse the filter.", "Rinse the filter under running water."),
            s("Put the filter back.", "Put the filter back.")]


def failing(job: list[Step], pages: list[str]) -> list[Failure]:
    fails = check(job, pages, "dishwasher")
    return fails + [f for f in gate(job, pages, "dishwasher") if f not in fails]


def test_a_part_the_manual_never_puts_back_gets_our_step_at_the_end() -> None:
    assert own_restores(RACK_JOB, failing(RACK_JOB, [RACK]), [RACK]) == [("rack", 1, None)]
    # the manual does put it back, on the job's own page: the repair must cite that line, never ours
    back = RACK + "Push the lower rack back in.\n"
    assert own_restores(RACK_JOB, failing(RACK_JOB, [back]), [back]) == []
    # a part held by a red zone, or taken out at a step that works in one, is never ours to put back
    assert own_restores(RACK_JOB, failing(RACK_JOB, [RACK]), [RACK], held={1}) == []
    # negative control: the job also fails for another reason, so ours would not save it
    skipped = Failure("step_skipped", '"turn the filter a…" between steps 3 and 4 on page 1')
    assert own_restores(RACK_JOB, [*failing(RACK_JOB, [RACK]), skipped], [RACK]) == []
    # a part only opened is not one taken out: the flap the manual never closes again is not ours to close
    opened = [Failure("leaves_state", "flap opened at step 2; never closed")]
    assert own_restores(RACK_JOB, opened, [RACK]) == []


DRAIN = ("Draining\nClose the water tap.\nSwitch the machine off. Pull out the mains plug.\nOpen the service flap.\n"
         "Clean the pump.\nClose the service flap.\nPour 1 litre of water into compartment II and start the Drain "
         "programme.\n")
DRAIN_JOB = [s("Close the water tap.", "Close the water tap."),
             s("Switch the machine off and pull out the mains plug.",
               "Switch the machine off. Pull out the mains plug."),
             s("Open the service flap.", "Open the service flap."), s("Clean the pump.", "Clean the pump."),
             s("Close the service flap.", "Close the service flap."),
             s("Pour 1 litre of water into compartment II and start the Drain programme.",
               "Pour 1 litre of water into compartment II and start the Drain programme.")]


def test_power_and_water_are_restored_just_before_the_later_step_that_needs_them() -> None:
    fails = failing(DRAIN_JOB, [DRAIN])
    assert own_restores(DRAIN_JOB, fails, [DRAIN]) == [("water_supply", 0, 5), ("mains", 1, 5)]
    # the switch-on the gate reads as one while unplugged gets the plug back in just before it
    on = [*DRAIN_JOB[:5], s("Switch the machine back on.", "Pour 1 litre of water into compartment II and start "
                            "the Drain programme.")]
    assert ("mains", 1, 5) in own_restores(on, failing(on, [DRAIN]), [DRAIN])
    # negative control: the manual prints how to plug back in beside the procedure, so the repair must cite it
    plug = DRAIN + "Put the mains plug back into the socket.\n"
    assert own_restores(DRAIN_JOB, failing(DRAIN_JOB, [plug]), [plug]) == []


def test_turning_the_tap_back_on_is_not_switching_the_machine_on() -> None:
    page = "Close the water tap.\nPull out the mains plug.\nClean the inlet filter.\n"
    job = [s("Close the water tap.", "Close the water tap."), s("Pull out the mains plug.", "Pull out the mains plug."),
           s("Clean the inlet filter.", "Clean the inlet filter."), s("Turn the water tap back on.", ""),
           s("Plug the appliance back in.", "")]
    assert gate(job, [page], "washer") == []
    on = [*job[:3], s("Switch the machine back on.", ""), job[4]]  # negative control: a real switch-on, unplugged
    assert [f.code for f in gate(on, [page], "washer")] == ["wrong_order"]


def test_a_run_is_read_from_the_manuals_own_words_too() -> None:
    page = ("Trek de stekker uit het stopcontact.\nDraai de kraan dicht.\nReinig de pomp.\n"
            "Giet 1 liter water in vakje II en start het programma Afpompen.\n")
    job = [s("Unplug the machine.", "Trek de stekker uit het stopcontact."),
           s("Close the water tap.", "Draai de kraan dicht."), s("Clean the pump.", "Reinig de pomp."),
           s("Pour one litre of water into compartment two and start Drain.",
             "Giet 1 liter water in vakje II en start het programma Afpompen.")]
    assert own_restores(job, check(job, [page], "washer"), [page]) == [("water_supply", 1, 3), ("mains", 0, 3)]


LID = """4.2 Cleaning the dispenser
Turn the dispenser lid open.
Do not pull the holder out while the lid is still locked.
Unlock the holder and lift it out.
Rinse the holder under warm water.
Put the holder back and turn the lid closed.
4.3 Cleaning the door
"""


def _lid(*texts: str) -> list[Step]:
    quotes = {"open": "Turn the dispenser lid open.", "unlock": "Unlock the holder and lift it out.",
              "rinse": "Rinse the holder under warm water.", "back": "Put the holder back and turn the lid closed."}
    return [s(quotes.get(k, k), quotes.get(k, k)) for k in texts]


def test_a_do_not_line_between_two_steps_fails_the_gate_and_a_carried_one_passes() -> None:
    job = _lid("open", "unlock", "rinse", "back")
    assert [f.detail for f in warnings_dropped(job, [LID])] == ['"do not pull the…" between steps 1 and 2 on page 1']
    carried = [job[0], s("Leave the holder in while the lid is locked.",
                         "Do not pull the holder out while the lid is still locked."), *job[1:]]
    assert warnings_dropped(carried, [LID]) == []
    # the same line cited by another job on the card is that job's, not a gap in this one
    assert warnings_dropped(job, [LID], [s("x", "Do not pull the holder out while the lid is still locked.")]) == []


def test_a_warning_sentence_under_the_procedures_heading_above_its_first_step_fails() -> None:
    page = "5.1 Draining the pump\nThe water can be hot: let it cool down first.\nOpen the pump cover.\n"
    job = [s("Open the pump cover.", "Open the pump cover.")]
    assert [f.detail for f in warnings_dropped(job, [page])] == ['"the water can be…" above step 1 on page 1']
    # a heading between the warning and the step closes the other procedure
    other = ("5.1 Descaling\nThe water can be hot: let it cool down first.\n5.2 Draining the pump\n"
             "Open the pump cover.\n")
    assert warnings_dropped(job, [other]) == []


def test_a_lid_turned_open_and_a_holder_unlocked_must_be_closed_where_the_manual_closes_them() -> None:
    job = _lid("open", "Leave the holder in while the lid is locked.", "unlock", "rinse")
    job[1] = s(job[1].text_en, "Do not pull the holder out while the lid is still locked.")
    details = [f.detail for f in gate(job, [LID], "dishwasher")]
    assert details == ["lid opened at step 1; never closed", "holder opened at step 3; never closed"]
    assert gate([*job, *_lid("back")], [LID], "dishwasher") == []


def test_a_part_opened_where_the_manual_never_closes_it_has_nothing_to_carry() -> None:
    page = "Open the filter flap.\nTake out the filter and rinse it.\nScrew the filter back in.\n"
    job = [s("Open the filter flap.", "Open the filter flap."),
           s("Take out the filter and rinse it.", "Take out the filter and rinse it."),
           s("Screw the filter back in.", "Screw the filter back in.")]
    assert gate(job, [page], "washer") == []


def test_parts_taken_out_together_must_each_go_back() -> None:
    page = "Remove the accessories and the shelf supports.\nClean the oven.\nRefit the shelf supports.\n"
    job = [s(line, line) for line in page.splitlines()]
    assert [f.detail for f in gate(job, [page], "oven")] == []  # "accessories" names no part we track
    page = "Take out the filter and then the seal.\nRinse them.\nPut first the filter and then the seal back.\n"
    job = [s(line, line) for line in page.splitlines()]
    assert gate(job, [page], "washer") == []
    assert [f.detail for f in gate(job[:2], [page], "washer")] == [
        "filter removed or opened at step 1; never refitted", "seal removed or opened at step 1; never refitted"]


def test_a_plug_pulled_out_in_other_words_must_go_back_and_nothing_is_switched_on_before() -> None:
    job = [s("Pull its plug out of the socket.", "a"), s("Clean the pump.", "b")]
    assert [f.detail for f in gate(job, [""], "dishwasher")] == ["mains off, never back on"]
    assert gate([*job, s("Plug the appliance back in.", "c")], [""], "dishwasher") == []
    order = [s("Unplug the machine, wait a minute and switch it back on.", "a"), s("Plug the machine back in.", "b")]
    assert [f.detail for f in gate(order, [""], "washer")] == ["switched on at step 1 while unplugged"]
    connect = [s("Disconnect it by pulling the plug out.", "a"), s("Connect it to the electricity supply again.", "b"),
               s("Switch it on again.", "c")]
    assert gate(connect, [""], "washer") == []
    assert gate([s("Unplug the machine.", "a"), s("Plug the machine back in.", "b"),
                 s("Switch it back on.", "c")], [""], "washer") == []


# A procedure as manuals print it: each step after a symbol, a step's own extra line under it, a warning box.
ARM = """Cleaning the filters
■ Switch the dishwasher off.
■ Remove the lower spray arm (see "Cleaning the spray arms").
■ Lift out the filter combination.
■ Rinse all the parts under running water.
Use a washing-up brush if needed.
■ Put the filter combination back flat on the floor of the cabinet.
■ Put the lower spray arm back on the filter combination.
■ Turn the screw clockwise until the arrow points at the lock.
■ Risk of damage from dirt in the pump.
Never wash without the lower spray arm and the filters.
88
"""
_ARM_STEPS = [s("Switch it off.", "Switch the dishwasher off."),
              s("Remove the spray arm.", 'Remove the lower spray arm (see "Cleaning the spray arms").'),
              s("Lift out the filters.", "Lift out the filter combination."),
              s("Rinse the parts.", "Rinse all the parts under running water."),
              s("Put the filters back.", "Put the filter combination back flat on the floor of the cabinet."),
              s("Put the arm back.", "Put the lower spray arm back on the filter combination."),
              s("Lock the arm.", "Turn the screw clockwise until the arrow points at the lock.")]


def test_a_printed_step_between_two_of_the_jobs_steps_is_skipped() -> None:
    job = [_ARM_STEPS[0], *_ARM_STEPS[2:5]]  # the spray arm is never taken off, and never put back
    fails = steps_skipped(job, [ARM])
    assert [f.code for f in fails] == ["step_skipped"]
    assert fails[0].detail == '"remove the lower spray…" between steps 1 and 2 on page 1'
    assert "step_skipped" in [f.code for f in check(job, [ARM], "dishwasher")]  # the first check: one repair


def test_every_printed_step_quoted_passes_and_a_steps_own_extra_line_is_not_a_step() -> None:
    assert steps_skipped(_ARM_STEPS, [ARM]) == []  # "Use a washing-up brush" belongs to the rinse step


def test_printed_steps_before_the_first_or_after_the_last_step_are_not_between() -> None:
    assert steps_skipped(_ARM_STEPS[2:5], [ARM]) == []


def test_a_warning_box_between_two_steps_is_the_warning_checks_not_a_skipped_step() -> None:
    page = ARM.replace("■ Put the lower spray arm back on the filter combination.\n", "")
    page = page.replace("■ Turn the screw clockwise until the arrow points at the lock.\n", "")
    page = page.replace("■ Risk of damage", "■ Turn the screw clockwise until the arrow points at the lock.\n"
                        "■ Risk of damage")  # the box now sits after the last step
    job = [*_ARM_STEPS[:5], s("Lock the arm.", "Turn the screw clockwise until the arrow points at the lock.")]
    assert steps_skipped(job, [page]) == []
    boxed = ARM.replace("■ Rinse all", "■ Risk of scalding from hot water.\n■ Rinse all")
    assert steps_skipped(_ARM_STEPS, [boxed]) == []


def test_a_printed_step_skipped_across_a_page_break_fails() -> None:
    one, two = ARM.split("■ Rinse all")
    two = "Cleaning\n■ Rinse all" + two
    job = [*_ARM_STEPS[:2], *[s(x.text_en, x.quote, 2) for x in _ARM_STEPS[3:]]]
    fails = steps_skipped(job, [one, two])
    assert [f.detail for f in fails] == ['"lift out the filter…" between steps 2 and 3 on page 1']


def test_a_numbered_step_skipped_fails() -> None:
    page = "Descaling\n1. Fill the tank with descaler.\n2. Place a bowl under the outlet.\n3. Start the programme.\n"
    job = [s("Fill the tank.", "Fill the tank with descaler."), s("Start it.", "Start the programme.")]
    assert [f.code for f in steps_skipped(job, [page])] == ["step_skipped"]


def test_printed_steps_under_another_heading_between_two_steps_are_another_blocks() -> None:
    page = ("Cleaning\nWarning: risk of electric shock.\nSwitch the machine off and pull out the plug.\n"
            "Tips\n■ Keep the room well ventilated.\n■ Leave the door open when the machine is not\nin use.\n"
            "Outside of the machine/\nControl panel\n■ Wipe the casing with a soft, damp cloth.\n"
            "■ Remove detergent residue at once.\n")
    job = [s("Unplug it.", "Switch the machine off and pull out the plug."),
           s("Wipe the casing.", "Wipe the casing with a soft, damp cloth.")]
    assert steps_skipped(job, [page]) == []  # the tips are their own block, not a step of this procedure
    skipped = page.replace("Tips\n", "")  # no heading between: the tips sit in the first step's own block
    assert [f.detail for f in steps_skipped(job, [skipped])][:1] == [
        '"keep the room well…" between steps 1 and 2 on page 1']


def test_a_quote_printed_twice_is_placed_after_the_step_before_it() -> None:
    page = ("Faults\n■ Switch the dishwasher off.\n■ Wait a few seconds and switch it back on.\n"
            "■ Close the tap.\nDoor opener\n■ Switch the dishwasher off.\n■ Open the door by hand.\n")
    job = [s("Read the fault.", "Door opener"), s("Switch it off.", "Switch the dishwasher off."),
           s("Open the door.", "Open the door by hand.")]
    assert steps_skipped(job, [page]) == []


def test_a_fault_tables_row_label_is_not_a_skipped_step() -> None:
    page = ("Faults\n■ Switch the dishwasher off.\n■ Fault F1 There may be a technical fault.\n"
            "■ Switch the dishwasher back on after a few seconds.\n")
    job = [s("Switch it off.", "Switch the dishwasher off."),
           s("Switch it on.", "Switch the dishwasher back on after a few seconds.")]
    assert steps_skipped(job, [page]) == []


def test_across_a_page_break_only_the_pages_end_and_the_next_pages_top_are_between() -> None:
    one = ("Cleaning\n■ Switch the machine off and pull out the plug.\n■ Wipe the casing with a soft, damp cloth.\n"
           + "The casing is made of a coated metal that keeps its colour for many years of normal use.\n" * 10)
    two = "Detergent drawer\n■ Pull the drawer out until it stops.\n"
    job = [s("Unplug it.", "Switch the machine off and pull out the plug."),
           s("Pull out the drawer.", "Pull the drawer out until it stops.", 2)]
    assert steps_skipped(job, [one, two]) == []  # the job jumps to another section, it does not skip a step
    near = one.split("The casing")[0]
    assert [f.detail for f in steps_skipped(job, [near, two])] == [
        '"wipe the casing with…" between steps 1 and 2 on page 1']


def test_a_step_printed_again_between_two_steps_is_the_jobs_own() -> None:
    page = ("Fridge too warm\n■ Let food cool down before storing it.\n■ Close the door properly.\n"
            "The door is not fitted well.\n■ Close the door properly.\n■ Wait for the temperature to settle.\n")
    job = [s("Let food cool.", "Let food cool down before storing it."),
           s("Close the door.", "Close the door properly."),
           s("Wait.", "Wait for the temperature to settle.")]
    assert steps_skipped(job, [page]) == []


def test_a_list_under_a_lead_goes_with_the_lead_and_the_rest_of_its_list() -> None:
    page = ("Pump filter\nClean the pump filter if:\n• the machine does not drain,\n• the drum does not turn,\n"
            "• the display shows an alarm code.\n■ Open the pump cover.\n")
    job = [s("Clean it when needed.", "Clean the pump filter if: • the machine does not drain, • the drum does "
             "not turn,"), s("Open the cover.", "Open the pump cover.")]
    assert steps_skipped(job, [page]) == []  # the last condition is part of the quoted list, not a step
    lead = ("Draining\n1. Open the filter cover.\n2. Follow the procedure below to drain\nthe water.\n"
            "If the product has an emergency hose, to drain the water:\n• Pull the hose out of its seat.\n"
            "• Put its end in a large container.\n")
    job = [s("Open the cover.", "Open the filter cover."), s("Pull out the hose.", "Pull the hose out of its seat."),
           s("Put it in a container.", "Put its end in a large container.")]
    assert steps_skipped(job, [lead]) == []  # the lead only introduces the steps the job quotes
    skipped = lead + "3. Close the filter cover.\n"
    assert [f.code for f in steps_skipped([job[0], s("Close it.", "Close the filter cover.")], [skipped])] == [
        "step_skipped"] * 3  # a list nobody quotes is skipped, its lead with it


def test_a_fault_tables_question_is_another_cause_not_a_skipped_step() -> None:
    page = ("Faults\nNo water.\n■ Tap not opened?\n■ Filter blocked? Clean the filter.\n"
            "Programme does not start.\n■ Door closed?\n")
    job = [s("Open the tap.", "Tap not opened?"), s("Close the door.", "Door closed?")]
    assert steps_skipped(job, [page]) == []


def test_a_legend_row_is_not_a_skipped_step() -> None:
    page = ("Child lock\nPress both buttons for 3 seconds to switch it on or off.\n■ w lit: the child lock is on.\n"
            "■ w flashing: the child lock is on and the dial was turned. Turn the dial back to the programme.\n")
    job = [s("Press both buttons.", "Press both buttons for 3 seconds to switch it on or off."),
           s("Turn the dial back.", "Turn the dial back to the programme.")]
    assert steps_skipped(job, [page]) == []


def test_the_bullets_of_a_warning_box_are_the_warnings_not_skipped_steps() -> None:
    page = ("Cleaning\nWARNING! Risk of injury.\n• Switch the appliance off and pull out the plug.\n"
            "• This appliance contains hydrocarbons. Only a qualified person may service it.\n"
            "• Check the drain regularly and clean it.\n")
    job = [s("Unplug it.", "Switch the appliance off and pull out the plug."),
           s("Clean the drain.", "Check the drain regularly and clean it.")]
    assert steps_skipped(job, [page]) == []
    wrapped = page.replace("WARNING! Risk of injury.", "WARNING!\nDanger of injury or of damage to the\nappliance.")
    assert steps_skipped(job, [wrapped]) == []  # the box's title on its own lines, over two
    after = "Cleaning\n• Use the lamp only to light the cabinet.\n2.5 Care\n" + wrapped.removeprefix("Cleaning\n")
    assert steps_skipped(job, [after]) == []  # a bare warning line ends the step above it


def test_another_causes_numbered_remedy_between_two_steps_is_another_branch() -> None:
    page = ("Faults\nDetergent residue on the laundry.\n▶ Start the Rinse programme.\nWrong detergent used.\n"
            "1. Check the detergent is suitable.\n2. Empty the reservoirs.\n3. Fill the reservoirs again.\n"
            "Laundry is stiff.\n▶ Brush the laundry after drying.\n")
    job = [s("Rinse.", "Start the Rinse programme."), s("Brush it.", "Brush the laundry after drying.")]
    assert steps_skipped(job, [page]) == []
    job = [s("Check it.", "Check the detergent is suitable."), s("Fill them.", "Fill the reservoirs again.")]
    assert [f.detail for f in steps_skipped(job, [page])] == [
        '"empty the reservoirs.…" between steps 1 and 2 on page 1']  # a remedy the job quotes from is its own


def test_another_faults_remedy_between_two_steps_is_another_branch() -> None:
    page = ("Faults\nBefore you fix a fault:\n■ Switch the dishwasher off.\n"
            "■ Fault F1 There may be a technical fault.\n■ Switch the dishwasher back on after a few seconds.\n"
            "■ Choose the programme you want.\n"
            "■ Fault F2 Something blocks the door.\n■ Remove the object from the door.\n")
    job = [s("Switch it off.", "Switch the dishwasher off."), s("Clear the door.", "Remove the object from the door.")]
    assert steps_skipped(job, [page]) == []  # F1's remedy is another fault's
    job = [s("Switch it off.", "Switch the dishwasher off."),
           s("Switch it on.", "Switch the dishwasher back on after a few seconds."),
           s("Clear the door.", "Remove the object from the door.")]
    assert [f.detail for f in steps_skipped(job, [page])] == [
        '"choose the programme you…" between steps 2 and 3 on page 1']  # the job quotes F1's remedy: it is its own
    marked = page.replace("■ Fault F1", "◆ Fault F1").replace("■ Fault F2", "◆ Fault F2")
    assert steps_skipped(job[::2], [marked]) == []  # the row's own symbol, its remedy under the step symbol


def test_a_faults_other_cause_starts_its_own_remedies() -> None:
    page = ("Faults\n■ Fault door The door does not open by itself.\nThere may be a technical fault.\n"
            "■ Open the door by hand and switch the dishwasher back on.\n"
            "■ Call the service if the fault comes back.\n"
            "The door does not close by itself.\nAn object may block the door.\n"
            "■ Remove the object and switch the dishwasher back on.\n"
            "■ Call the service if the fault comes back.\n")
    job = [s("Check.", "Fault door The door does not open by itself."),
           s("Clear the door.", "Remove the object and switch the dishwasher back on.")]
    assert steps_skipped(job, [page]) == []  # the first cause's remedies are another branch
    job = [s("Open it.", "Open the door by hand and switch the dishwasher back on."),
           s("Clear the door.", "Remove the object and switch the dishwasher back on.")]
    assert [f.detail for f in steps_skipped(job, [page])] == [
        '"call the service if…" between steps 1 and 2 on page 1']  # the job takes the first cause too: its own


def test_words_printed_twice_are_the_copy_after_the_step_before(monkeypatch: pytest.MonkeyPatch) -> None:
    page = ("Faults\nBefore you fix a fault:\n■ Switch the dishwasher off.\n"
            "■ Fault F1 There may be a technical fault.\n■ Choose the programme you want.\n"
            "■ Close the door again.\n■ Switch the dishwasher off.\n■ Turn the tap off.\n")
    far = "Opening the door\nPull the door firmly out of its lock and let it stand open.\n"
    real = locate

    def last_copy(page_text: str, quote: str) -> tuple[str, int, int] | None:
        hit = real(page_text, quote)  # the aligner may settle on any copy: here the last
        if hit is None:
            return None
        flat, n = hit[0].replace("\n", " "), norm(quote)
        at = flat.rfind(n)
        return (hit[0], at, at + len(n)) if at >= 0 else hit

    monkeypatch.setattr("ingest.jobcheck.locate", last_copy)
    job = [s("Before.", "Before you fix a fault:"), s("Switch it off.", "Switch the dishwasher off."),
           s("Open it.", "Pull the door firmly out of its lock and let it stand open.", page=3)]
    assert steps_skipped(job, [page, "", far]) == []  # the copy right under the step before
    job[1] = s("Turn it off.", "Turn the tap off.")
    assert [f.detail for f in steps_skipped(job, [page, "", far])][:1] == [
        '"switch the dishwasher off.…" between steps 1 and 2 on page 1']  # a step it does skip is still found
