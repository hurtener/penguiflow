---
name: interface-design
description: Design and critique user interfaces (web/mobile) using a calm, cozy-premium “Design Soul” aesthetic (warm parchment neutrals, muted mint/teal accents, soft rounded surfaces, subtle depth, and generous whitespace). Use for UX/UI direction, screen layouts, information architecture, component + state specs, design tokens (CSS/Tailwind), interaction/motion guidance, accessibility review, and implementation handoff notes.
---

# Interface Design (Design Soul)

## Default workflow

1. Clarify the job: user, context, platform, constraints, content/data, success metric, existing patterns.
2. Define structure: one primary action per view, sections, navigation, empty/error states.
3. Compose the screen: spacing-first grouping, low-contrast separation, warm surfaces, quiet chrome.
4. Specify components + states: buttons/inputs/cards/tables; include hover/pressed/focus/disabled/loading/error.
5. Set tokens: start from `scripts/generate_tokens.py` and map to any existing tokens.
6. Run an a11y + usability pass: contrast, focus visibility, keyboard flow, target sizes, color-not-alone.
7. Handoff: provide CSS/Tailwind/React guidance and call out reusable components/tokens.

## Output format (one screen)

Deliver in this order unless the user asks otherwise:

1. Summary: primary user goal + primary action.
2. Layout & hierarchy: regions/sections, nav, grouping, empty/error.
3. Components: list specs + states.
4. Tokens: concrete values or mapping (colors/type/space/radius/shadow).
5. Interaction & motion: what animates + durations/easing.
6. Accessibility: checks + any risks.

## Project memory (optional)

If the user wants persistence across iterations, offer to create/update:

- `.interface-design/system.md` with approved tokens, component patterns, and “do / don’t”.

## References

- Full design language: `references/design-soul-guidelines.md`
- Critique rubric: `references/critique-checklist.md`
- Starter tokens: `references/tokens-starter.md` and `scripts/generate_tokens.py`
- Workflow + handoff details: `references/interface-design-workflow.md`
