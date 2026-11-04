# Fix-It Lens brand

The mark is the lock-on: two crop corners close on a part, and the part is outlined the way the camera outlines it.
The part is a round bayonet cap with a lug either side and a bore at its centre. The wordmark is "Fix-It Lens" in
Fraunces 600, stored as outlines.

Every file here is built by `build.py` (see its docstring for the command). Edit the drawing there, not the outputs.

## Colours

Two grounds, each with its own foreground and accent. Use the pair for the ground you are on and never mix them.

| Token | On ink (dark) | On paper (light) | Use |
|---|---|---|---|
| `--brand-ground` | `#15130f` | `#f2ede2` | The surface behind the mark, and the icon plates |
| `--brand-fg` | `#f1ece0` | `#1c1a16` | The corners and the wordmark |
| `--brand-accent` | `#93a8ff` | `#2a49d8` | The cap: its outline, the 30% fill and the bore |
| `--brand-faint` | `#958e7e` | `#736d5f` | Mono labels beside the mark (OG image, end card) |

These are the app's own values (`phone/src/style.css`: `--bg`, `--fg`, `--blue`, `--faint`). The red, amber and green
signals never go in the mark.

## Clear space and minimum size

- **Clear space:** keep one corner arm free on every side, which is a quarter of the mark's height. No text or edge
  comes closer.
- **Minimum size:** the drawn mark is for 24 px and up. Below that, use `mark-16.svg`, a separate pixel-by-pixel
  drawing for 16 px that stays sharp at 1× and 2×. Don't shrink the large mark down instead.
- **Lockup:** 20 px tall at least. Below that, use the mark alone.
- Don't rotate the mark, recolour one part of it, add the two missing corners or fill the cap solid.

## Files

| File | Where it goes |
|---|---|
| `mark.svg`, `mark-light.svg` | The mark on its own, transparent, for dark and light grounds |
| `mark-16.svg` | The 16 px drawing on its ink tile |
| `lockup.svg`, `lockup-light.svg` | Mark and wordmark, transparent; the app and site headers |
| `web/favicon.svg`, `web/favicon.ico` | Browser tab; the .ico holds 16, 32 (both the pixel drawing) and 48 |
| `web/icon.svg`, `web/icon-192.png`, `web/icon-512.png` | PWA icons, `purpose: any` |
| `web/icon-maskable-512.png` | PWA, `purpose: maskable`: full bleed, the mark inside the middle 60% |
| `web/apple-touch-icon.png` | iOS home screen, 180, full bleed (iOS rounds it) |
| `ha/icon.png`, `ha/icon@2x.png` | Home Assistant integration icon, 256 and 512, for light UI |
| `ha/dark_icon.png`, `ha/dark_icon@2x.png` | The same for dark UI |
| `ha/logo.png`, `ha/dark_logo.png` (and `@2x`) | Integration logo, the lockup, light and dark |
| `addon/icon.png`, `addon/logo.png` | Home Assistant add-on, 128 square and 250 wide, on their own ink plate |
| `og/og.png` | Link preview, 1200 × 630 |
| `video/endcard.png`, `video/endcard.svg` | End card, 1920 × 1080, ready to use |
| `video/lockup-3840.png`, `video/endcard-mark.svg` | Transparent lockup and mark for compositing |

The `web/` names match `phone/public/`, so those files can be copied over one for one. The integration's files go in
its `brand/` folder under the same names.

`directions/` keeps the four directions the mark was chosen from.
