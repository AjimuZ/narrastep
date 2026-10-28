# ADR 0012: Minimal Pixel JRPG design system

Status: accepted (2026-10-28). Supersedes: the Version 0.2 visual style.

## Context

The owner wants a simpler and better-looking interface with plain, non-promotional text. The
Version 0.2 interface uses layered shadows, glow-like hover effects, smooth line icons and several
duplicated token aliases (`--color-*`, `--dk-*`).

## Decision

Apply the `ajimu-pixel-ui` design system ("80% modern minimal application, 20% pixel JRPG
identity"):

- keep the existing color palette and map it to semantic roles; add no new color values;
- add non-color tokens: a 2 px pixel unit, a 0–2 px radius, frame color, UI, mono and optional
  pixel title fonts, about 120 ms transitions;
- build shared primitives once (window, menu entry with pixel cursor, button, field, tab,
  segmented gauge, status pill, activity line) and use them on every screen;
- replace the line icons with a 16×16 pixel icon sprite;
- remove shadows and glow; depth comes from frames, layers and contrast.

The screen-by-screen specification and copy rules are in [UI](../UI.md).

## Alternatives

- Restyle the current components: rejected; the duplicated tokens and per-component styles would
  remain.
- A component library such as Angular Material: rejected; it fights the pixel identity and adds
  weight.

## Consequences

- Every feature component changes its template and styles; tests that rely on text or structure
  are updated in the same change.
- `python .claude/skills/ajimu-pixel-ui/scripts/check_colors.py apps/web/src --tokens
  apps/web/src/styles/tokens.scss` must report no color literal outside the token file.
- Evidence: contrast, keyboard, reduced-motion, 320 px width and 200% zoom checks (P04 in the
  [roadmap](../ROADMAP.md)).
