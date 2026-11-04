"""G0 and a mains conductor: taken off its connection it holds as mains.wiring in every table; a cable that is not the
mains (low-voltage, data, charging, extension, a cord reel, a battery terminal) and unplugging at the socket pass."""

from __future__ import annotations

import pytest

from rules import Hold, Pass, check_sentence

COUNTRIES = ["nl", "uk", "us", "de"]

# Each class from the review of the first wiring fix, then siblings of it.
MUST_HOLD_EN = [
    # colours joined by "and", "or" or a slash
    "Remove the brown and blue wires.", "Remove the green and yellow wire.", "Remove the green/yellow earth.",
    "Disconnect the brown or blue wire.", "Cut the red, black and green wires.", "Unscrew the brown/blue cores.",
    # bare L, N, live, neutral, earth
    "Disconnect L and N.", "Loosen the screws holding L and N.", "Disconnect the earth.", "Undo the neutral.",
    "Unscrew L, N and earth.", "Free the phase.",
    # withdraw, slide off
    "Slide the spade off the heating element tab.", "Withdraw the power lead from the back of the cooker.",
    "Withdraw the flex from the terminal block.", "Slide the spade connector off the element.",
    "Pull the spades off the pump.",
    # a hard-wired appliance taken off the supply
    "Unwire the hob.", "Disconnect the cooker from the mains.", "Unwire the cooker.",
    "Disconnect the oven from the mains supply.", "Detach the water heater from the mains.",
    # the first fix's own cases
    "Remove the mains cable from its terminal.", "Disconnect the live.", "Pull the brown wire off the spade connector.",
    "Take the flex inside the machine.", "Unscrew the plug connector inside the machine.", "Cut the mains lead.",
    "Free the wiring loom from the terminal block.", "Detach the power cord from the choc block.",
    # a terminal or terminal cover named as the appliance's own
    "Prise off the hob terminal cover.", "Remove the cooker terminal cover.",
    "Take the mains terminal cover off.", "Loosen the wiring terminals.",
    # a not-mains word near the cable, not naming it
    "Remove the brown wire marked with a label.", "Free the supply flex from the data plate bracket.",
    "Remove the supply wires from the battery charger's mains input.", "Cut the brown wire next to the USB port.",
    "Disconnect the mains cable behind the data plate.", "Remove the live wire from the battery charger's terminal.",
    # a bare cord or lead with a mains part or the supply in the clause
    "Pull the cord off the motor terminals.", "Pull the lead off the thermostat.",
    "Remove the cord from the pump motor.", "Disconnect the cord from the mains input.",
    # a wired-in appliance or a fixed supply
    "Disconnect the battery-backed mains supply.", "Disconnect your built-in oven from the mains.",
    "Take the cooker off the mains.", "Disconnect the dishwasher from its fixed supply.",
    "Disconnect the electric water heater from the electricity supply.", "Disconnect the oven from the fused spur.",
    "Disconnect the integrated hob from the mains.", "Take the electric oven off the mains supply.",
    "Disconnect the washing machine from its fixed wiring.", "Detach the built-in oven from the power supply.",
    "Disconnect the cooker from the electricity.", "Disconnect the boiler from the fused spur.",
    # bare poles joined
    "Disconnect live and neutral.", "Disconnect neutral and earth.", "Swap live and neutral.",
    # the final round: a bare pole, a wired-in appliance with no supply named, the passive, a cord at the appliance
    "Detach earth from the frame.", "Unscrew earth from the chassis.", "Disconnect the hard-wired hob.",
    "Disconnect the hob.", "Disconnect the hardwired cooker.", "Disconnect the oven.",
    "Disconnect the extractor fan from the mains.", "Disconnect the extractor from the mains.",
    "With the panel off, disconnect the wires to the pump.", "Disconnect the wires to the element.",
    "The brown wire should then be removed.", "The live wire must then be disconnected.",
    "Cut the cord at the appliance end.", "Cut the lead at the plug end.",
    "Disconnect the boiler from the mains.", "Disconnect the electric boiler from the power supply.",
    "Disconnect the combi boiler from the mains.",
]
MUST_HOLD_NL = [
    "Haal de bruine en blauwe draad los.", "Maak L en N los.", "Haal de aarde los.", "Trek de fase eruit.",
    "Maak de nul los.", "Neem de netkabel eraf.",
    "Maak de blauwe en bruine ader los.", "Trek de bruine/blauwe draad eruit.", "Haal de aardedraad eraf.",
    "Maak de fase en de nul los.", "Neem het netsnoer eruit.", "Koppel de kookplaat los van het lichtnet.",
    "Trek de kabelschoen van het element af.",
    "Neem de kabel weg.", "Koppel de stekkerverbinding los.", "Haal de bruine draad uit de kroonsteen los.",
    "Knip het netsnoer door.", "Maak de voedingskabel los van de klem.", "Trek de kabel uit de klem.",
    "Schroef het netsnoer los van de klemmenstrook.",
    "Maak de draden van het label los.", "Haal de inductiekookplaat los van het elektriciteitsnet.",
    "Ontkoppel de vaatwasser van de vaste aansluiting.", "Koppel de inbouwoven los van het stroomnet.",
    "Haal de kookplaat los van de vaste aansluiting.", "Maak fase en nul los.", "Trek de draden achter het label los.",
    "Haal het snoer van de motor los.", "Ontkoppel de doorstromer van het net.",
    "Haal de ketel los van de vaste aansluiting.",
    "Haal de kookplaat van de groep af.", "Koppel de doorstromer los.", "Koppel het fornuis af van het lichtnet.",
    "Trek de stekkertjes van het verwarmingselement.", "Verwijder de voedingsdraden.",
    "Ontkoppel de ketel van de stroomvoorziening.", "Ontkoppel de elektrische boiler van het stroomnet.",
    "Haal de oven los van de eindgroep.", "Ontkoppel de inductiekookplaat.", "Koppel de oven af van het stroomnet.",
    "Trek het stekkertje van de pomp.", "Verwijder de aansluitdraden.", "Ontkoppel de cv-ketel van het lichtnet.",
    "Maak aarde los van het frame.", "De bruine draad moet dan worden losgemaakt.",
]
MUST_PASS_EN = [
    # low-voltage, data, charging, extension, cord rewind, a container
    "Remove the charging cable from the vacuum.", "Disconnect the USB cable.", "Remove the HDMI cable.",
    "Take the power cord out of the storage compartment.", "Pull the cord to release the lid.",
    "Take the lead out of the box.", "Release the cord rewind button.", "Take the extension lead away from the sink.",
    "Remove the ethernet cable from the router.", "Take the aerial lead out of the TV.",
    "Disconnect the speaker wire.", "Pull the cord to start it.", "Take the cable out of the accessory bag.",
    "Unplug the charger cable from the phone.",
    # battery terminals
    "Clean the battery terminals of the remote.", "Replace the batteries, noting the + terminal.",
    "Check the battery terminal is clean.", "Detach the battery terminal cover.",
    # unplugging, cable ties, wire shelves, hoses
    "Unplug the power cord from the wall socket.", "Pull the plug out of the socket.", "Cut the cable tie.",
    "Snip the cable ties holding the hose.", "Disconnect the hose connector from the tap.", "Wipe the wire shelf.",
    "Take the power cord out of the socket.", "Unplug the mains cable from the socket.", "Unplug the machine.",
    # how a manual says unplug: a general rule, not one appliance's
    "Disconnect the appliance from the mains.", "Disconnect the mains plug from the mains socket.",
    "Disconnect the appliance from the mains and wait until it is cold.",
    "Disconnect the appliance from the mains supply.",
    "Disconnect the washing machine from the mains before cleaning.", "Disconnect the boiler from the power supply.",
    # not-mains words naming the cable; a bare lead with no mains near it
    "Remove the USB cable from the charger's input.", "Disconnect the data cable from the TV.",
    "Pull the cord to open the blind.", "Take the lead out of the drawer.", "Take the USB charging cable out.",
    # a cable routed through a clip; a charging lead off its base; a boiler on a plug
    "Unclip the cable from the cable clip.", "Free the cord from the cord clip.",
    "Remove the phone charger cable from the dock.",
    "Disconnect the boiler from the power supply.", "Disconnect the combi boiler from the power supply.",
]
MUST_PASS_NL = [
    "Haal de oplaadkabel uit de stofzuiger.", "Haal het snoer van de haspel.", "Verwijder de USB-kabel.",
    "Knip het draadje van het etiket af.",
    "Verwijder de netwerkkabel.", "Haal de verlengkabel weg bij de gootsteen.", "Knip het draadje van de knoop af.",
    "Haal de oplader uit het stopcontact.", "Haal de stekker uit het stopcontact.", "De deur klemt een beetje.",
    "Koppel het apparaat los van het lichtnet.", "Rol het verlengsnoer op de haspel.",
    "Knip het draadje bij de naad af.",
    "Klik de kabel in de kabelclip.", "Knip de losse draadjes af.", "Haal het snoer uit de kabelhouder.",
    "Knip het naaidraad af.", "Haal de oplaadkabel van het basisstation.",
]


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", MUST_HOLD_EN)
def test_a_conductor_taken_off_holds_in_every_table(country: str, text: str) -> None:
    r = check_sentence(text, "en", None, None, country=country)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.mains.wiring", text


@pytest.mark.parametrize("text", MUST_HOLD_NL)
def test_a_conductor_taken_off_holds_in_dutch(text: str) -> None:
    r = check_sentence(text, "nl", None, None, country="nl")
    assert isinstance(r, Hold) and r.rule_id == "nl.mains.wiring", text


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", MUST_PASS_EN)
def test_a_cable_that_is_not_the_mains_passes_in_every_table(country: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country), Pass), text


@pytest.mark.parametrize("text", MUST_PASS_NL)
def test_a_cable_that_is_not_the_mains_passes_in_dutch(text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl"), Pass), text


@pytest.mark.parametrize("country", COUNTRIES)
def test_known_over_hold_a_charging_lead_from_the_base_stays_held_by_the_casing(country: str) -> None:
    """Safe, but held by mains.casing ("the base") since before the wiring work. Passing it would loosen the casing
    matcher, so it stays held and is listed as a known over-hold."""
    r = check_sentence("Detach the charging lead from the base.", "en", None, None, country=country)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.mains.casing"
