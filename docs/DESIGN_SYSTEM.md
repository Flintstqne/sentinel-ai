# Design system

Single source: `src/sentinel_ai/ui/theme.py` (tokens), `ui/fonts.py` (type), `ui/icons.py`
(status icons), `companion/sprites.py` (the duck).

## Colour

| Token | Hex | Use |
|---|---|---|
| bg / surface / surface_raised / surface_hover | `#14171c` / `#1b1f25` / `#232830` / `#292f38` | charcoal surfaces, darkest to lightest |
| border / border_strong | `#2c333d` / `#3a4350` | hairlines |
| text / text_muted | `#dbe1e8` / `#8d97a6` | ink |
| accent | `#3aa9a0` | muted turquoise: the brand and interactive colour only |
| good | `#5fae7a` | status: passing |
| warning | `#d9a441` | status: warning |
| danger | `#e0605a` | status: failing, confirmed high severity |
| neutral | `#8d97a6` | status: not assessed, check error |

Status has its own reserved scale and is never used decoratively. It always appears as a
**mark** (an icon, a square) beside an ink-coloured word, never as text colour, and each
status has its own icon *shape*, so meaning never depends on colour alone. The brand
turquoise is deliberately not the "passing" colour.

Contrast was measured with the dataviz skill's WCAG function. On the card surface `#1b1f25`:
ink 12.6:1, muted 5.6:1, accent 5.8:1, good 6.2:1, warning 7.4:1, critical 4.7:1. The weakest
text case, muted ink on the hover surface, is 4.56:1; critical on the hover surface is
3.8:1, above the 3:1 needed for graphics (it is only ever a mark).

## Type

Bundled, all SIL Open Font License 1.1 (licence texts ship beside the files in
`assets/fonts/`): **IBM Plex Sans** for text and every number, **IBM Plex Mono** for evidence,
**Pixelify Sans** for the wordmark and section labels only, where its pixel grid echoes the
duck. Figures use the plain sans (a display face on a number reads as decoration).

| Role | Face | Size / weight |
|---|---|---|
| caption / small / body | Plex Sans | 11 / 12 / 13 px, 400 |
| body_strong | Plex Sans | 13 px, 500 |
| title / headline | Plex Sans | 16 / 22 px, 600 |
| figure | Plex Sans | 30 px, 600 |
| mono | Plex Mono | 12 px |
| label / wordmark | Pixelify Sans | 12 / 18 px |

**Fonts are set in code, never in the stylesheet.** A `font-size` in the application
stylesheet silently overrides every `QFont` set on a widget; that disabled this whole scale
once. A regression test checks the figure, wordmark and evidence fonts at runtime.

## Spacing and shape

4 px grid (`SPACE`: 4, 8, 12, 16, 24, 32). Corner radius 2 px: nearly square, which sits
better beside pixel art than soft rounding. No gradients, glows or pill shapes.

## The dashboard

Header (identity, last scan in local time, Scan now), a one-sentence lead statement that
states the worst thing first, a row of stat tiles (label, figure, optional hint), then
controls sorted worst-first beside the selected control's detail (what we found, evidence in
mono, recommended action with Copy, framework reference, finding history, Acknowledge).
Empty state, dimmed-while-scanning state (the previous results stay put; no skeleton, no
layout jump), arrow-key navigation. Every piece of scan text is escaped or shown as plain
text: evidence can contain attacker-controlled strings.

## Sprite

* 24 x 24 cells on one canvas with one feet baseline, so the duck never hops when it turns.
  Default scale 3 (an integer, so pixels stay crisp). The window's bottom edge is the ground.
* Built from shaded ellipses (`pixelart.py`): one light direction, a four-step slate ramp
  (shadow, base, light, highlight), a soft edge where parts overlap and a single dark outline
  around the whole silhouette. Details are placed by hand on top.
* **Front view** (standing and every status state): a round head, two glowing goggle lenses
  with a bridge, a wide flat slate bill with nostrils, small wings, an egg-shaped body with a
  glowing chest core, splayed webbed feet, an antenna bulb that is the alert beacon.
* **Side view** (walking): right-facing, three leg poses played as a four-pose cycle;
  left-facing is the same grid flipped.
* Frames differ only in the lens cells, a few overlay cells and, for the side view, the legs.
  The outlines are guarded by golden-outline tests.
* Tone recolours the lenses, beacon and chest core: normal turquoise, warning amber,
  critical red. The bill is slate, not orange, because amber is reserved for warnings.
* Animation offsets are sideways or upward only; nothing dips below the baseline.
* **Off-the-ground poses** (the standing builder with parameters: lift, squash, wings, legs):
  *held* lifts the figure two rows so the legs can dangle, drops the antenna, opens the lenses
  wide and alternates the wings between raised and held-out, with the legs swung left, centre
  or right to trail the drag; *fall* tucks the feet and flaps faster; *land* squashes the
  figure, shuts the lenses and puffs dust either side. These frames do not rest on the feet
  baseline, by design.
