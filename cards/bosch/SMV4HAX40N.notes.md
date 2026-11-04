# Bosch SMV4HAX40N card: notes

Nothing on this card is staged.

`pump_cover_out` ("the pump cover is out of its housing") and `pump_cover_refitted` ("the pump cover is back in place
over its housing") are done-when lines Muse Wonder wrote. Each is cited to the manual's p59 step it belongs to
(removing the pump cover, and fitting it back and pressing it down). Because they say "housing", the rule engine's
casing rule (G0) held them. They stay as the model wrote them, English only, as a real test of that rule.

Every step and done-when line on the card is either the model's or a hand rewording of a line the manual prints. Each
is cited to its page and re-checked by code. The hand edits add nothing the manual doesn't say: tap back on and switch
on (p45) and the p59 warning each quote the manual. No line exists only for a demo moment.
