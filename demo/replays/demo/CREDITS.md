# The demo replay

This replay was made, not recorded (`tools/make_demo_replay.py`). Its steps and quotes come from the fictional
Acme WM-0000 card, which has a fictional manual. Its stop script is the `nl.mains.casing` rule in `rules/nl.yaml`.
No household, plate or serial number is in it.

The two frames are photos from Wikimedia Commons, cropped and scaled to 1024x768:

- `frames/f_front.jpg`: "Domestic washing machine Germany.jpg" by Spacekid, CC0, cropped to the door and the
  lower panel. <https://commons.wikimedia.org/wiki/File:Domestic_washing_machine_Germany.jpg>
- `frames/f_casing.jpg`: "Washing machine- without front.jpg", public domain.
  <https://commons.wikimedia.org/wiki/File:Washing_machine-_without_front.jpg>

Each crop was checked by eye before it was committed: no brand mark, no text, no face and no house identifier in
frame. `tests/l0/test_demo.py` pins each frame to the sha256 it had when checked, so a changed frame needs a new
check. Neither machine's maker endorses or is affiliated with this project, and the Acme card is not about either
machine.
