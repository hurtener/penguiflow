# Interface design workflow (Design Soul)

Use this when you need a more explicit end-to-end process (discovery → layout → components → tokens → handoff).

## 1) Clarify (ask first)

- Platform: web / iOS / Android / desktop; responsive breakpoints.
- User + job: who is using this, and what “done” looks like.
- Primary action: the *one* thing the user should do on this view.
- Content + data: what’s real, what’s placeholder; edge cases.
- Constraints: timeline, tech stack, existing design system, brand.
- Success criteria: what changes if this UI ships (metric, outcome, support load).

If anything is missing, make a best assumption and label it as an assumption.

## 2) Structure (before styling)

- Information architecture: sections, headings, grouping, progressive disclosure.
- Navigation: where does this view sit; back/forward; “where am I” cues.
- States: loading, empty, error, permission-denied, offline (if relevant).
- Density: decide whether this view is “editorial calm” or “data-dense”; soften tables even when dense.

## 3) Layout & hierarchy (spacing first)

- Use whitespace and grouping before borders.
- Keep chrome quiet; content is the hero.
- Use surfaces to nest: canvas → card → inner card → content.
- Keep one focal point per view (one primary CTA; everything else recedes).

## 4) Components (spec with states)

For each component, specify:

- Purpose + placement
- Variant (primary/secondary/tertiary)
- Sizing (padding, height, radius)
- States: default, hover, pressed, focus, disabled, loading, error
- Copy: label tone, helper text, empty-state tone (sentence case)

## 5) Tokens (make it implementable)

- Prefer CSS variables for the system tokens (colors/type/space/radius/shadows).
- Generate a starter set via:
  - `python3 scripts/generate_tokens.py --format both --out-dir .`
- If a project already has tokens, map them to this system instead of inventing new names.

## 6) Accessibility baseline

- Contrast: aim for comfortable readability on warm neutrals; avoid “fashion gray”.
- Focus: every interactive element needs a visible focus ring; prefer a soft mint glow + subtle outline.
- Targets: comfortable hit areas; spacing prevents mis-taps.
- Affordance: links/buttons should not rely on color alone (underline/weight/icon).
- Motion: subtle, optional; avoid bouncy transitions; respect reduced motion.

## 7) Engineering handoff notes (when requested)

### Tailwind mapping (pattern)

- Define CSS vars (from `tokens.css`) on `:root` and reference them in Tailwind config.
- Use tokens everywhere (no hardcoded hex values in components).

### Component decomposition

- Call out reusable primitives first: `Card`, `Button`, `Input`, `Chip`, `Toast`, `Popover`.
- Keep variants minimal; avoid a “variant explosion”.

### Table density (when needed)

- Allow dense tables, but soften: warm row background, subtle separators, readable type, generous row height.

