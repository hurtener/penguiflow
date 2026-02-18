# Design Soul — Guidelines

North star sentence (designer-ready)

Design a warm, airy, premium productivity system: parchment-like neutrals, mint/teal accents used sparingly, soft rounded cards and inputs, subtle ambient shadows, humanist typography, generous whitespace, and quiet hierarchy—calm confidence, cozy clarity, and gentle tactility.

## Design soul

A calm, cozy-premium interface that feels like quiet morning light on paper: warm, airy, and intentionally low-drama. It blends Apple-like clarity with a softer, more human warmth—nothing shouts, everything invites. The UI should feel trustworthy, gentle, and modern, with subtle depth and tactile softness rather than sharp “enterprise” edges.

## Visual hierarchy philosophy

- Clarity over chrome: the interface frame stays quiet; content is the hero.
- Low-contrast by default: separation comes from spacing and tone, not heavy lines.
- One focal point at a time: a single primary action per view; everything else recedes.
- Soft structure: use rounded surfaces, gentle shadows, and restrained borders to create order without rigidity.

## Color language (cozy, warm, confident)

### Base neutrals (the “paper” world)

- Canvas: warm off-white / parchment (never pure white)
- Surface: slightly deeper cream (cards sit on the canvas, not above it)
- Surface 2: a whisper of warm gray-beige for nested areas (sidebars, tool trays)
- Borders / Dividers: barely-there warm gray lines (thin, low opacity)

Rule: neutrals should feel like stone + paper + linen, not sterile gray.

### Accent system (fresh but soft)

- Primary accent: muted mint/teal (calm confidence; used sparingly)
- Secondary accents: desaturated sky / slate blues for links and secondary emphasis
- Warm accent: soft terracotta/coral reserved for “create/new” moments or highlights
- Success: gentle green (not neon)
- Warning: warm amber (soft, readable)
- Error: muted red (firm but not aggressive)

Rule: accents are a seasoning, never the whole meal. If the screen looks colorful, it’s too much.

## Contrast & accessibility

- Keep text contrast high enough to read comfortably, but avoid harsh black-on-white.
- Prefer charcoal text on warm surfaces; reserve true black for rare emphasis.
- Make links and interactive states discoverable with color + subtle underline/weight, not color alone.

## Shape & roundness (soft hardware)

### Corner radius signature

- Small controls (chips, inputs): medium rounding (friendly, precise)
- Cards / panels: large rounding (pillowy, modern)
- Floating elements (toasts, popovers): even larger rounding (soft, “touchable”)

Rule: rounding should feel consistent across the system; avoid mixing sharp rectangles with very round elements.

### Geometry guidelines

- Prefer rounded rectangles over perfect circles for most UI.
- Make buttons feel “cushioned,” not “boxed.”
- Avoid sharp corners entirely except for data-dense tables (and even then, soften).

## Depth, shadows, and material (quiet elevation)

### Depth style

- Use subtle, diffused shadows—like ambient light, not spotlight.
- Communicate elevation more by tone and spacing than shadow strength.
- Keep surfaces layered: canvas → card → inner card → content.

### Shadow rules

- Never use hard shadows or dramatic contrast.
- Avoid “floating everywhere.” Only elevate:
  - active cards
  - popovers/menus
  - the primary composer/action bar
- Use gentle blurred shadows + a faint border to keep things crisp but soft.

### Borders

- Borders are hairline and warm, often barely visible.
- Use borders primarily to stabilize layouts (cards on similar tones), not as separators.

## Typography (clear, warm, modern)

### Typeface vibe

- Use a humanist sans or modern system sans that reads friendly, clean, and premium.
- Avoid overly geometric fonts that feel cold.
- Prefer slightly rounded or humanist letterforms.

### Type hierarchy

- Headlines: modest size, strong weight, not oversized.
- Body: comfortable line height, slightly larger than typical “enterprise UI.”
- Labels / metadata: quiet, lighter weight, higher letter spacing only if needed.

### Text color strategy

- Use charcoal for primary text.
- Use a warm mid-gray for secondary text.
- Avoid low-contrast “fashion gray” that harms readability.

### Voice in typography

- Keep voice calm, confident, minimal punctuation.
- Prefer sentence case over ALL CAPS.
- Use numbers and labels in a way that feels “editorial,” not “dashboardy.”

## Spacing & layout (air as a design element)

- Use generous whitespace as part of the brand.
- Use consistent spacing steps; avoid “almost the same” paddings.
- Make grouping obvious through proximity first, then tone, then borders.

Rule: if you need a divider everywhere, spacing isn’t doing its job.

## Components: tactile, contained, composed

### Cards

- Make cards the primary unit: soft radius, warm background, subtle border, gentle shadow.
- Keep headers clean and compact; show actions as quiet icons or tertiary buttons.

### Buttons

- Primary: mint/teal fill, soft radius, calm contrast, minimal label.
- Secondary: surface-toned with a subtle border (never stark).
- Tertiary: text/button-link style, used frequently.
- Hover: brighten slightly; pressed: subtle inset.

### Inputs

- Use large, pill-like inputs with comfortable padding.
- Use soft mint focus glow rather than sharp blue outlines.
- Keep placeholder text warm gray and readable (not too faint).

### Pills / chips

- Use for filters, tags, statuses.
- Make them “soft badges,” not hard labels.

### Iconography

- Use thin-stroke icons with consistent weight.
- Keep icons as utility—never decorative noise.
- Group icons into clusters; avoid scattered icon soup.

## Motion & interaction tone (soft, responsive, not flashy)

- Prefer gentle fades, short slides, subtle scale on popovers.
- Avoid bouncy/playful motion; keep it calm productivity.
- Make loading indicators minimal (inline shimmer or soft pulse), not loud banners.

## “Do / Don’t” summary

Do

- Warm neutrals, soft shadows, rounded surfaces
- One strong accent used sparingly
- Charcoal text, readable sizes, generous spacing
- Quiet borders, thoughtful grouping, minimal chrome

Don’t

- Pure white + pure black everywhere
- Neon accents, heavy gradients, loud shadows
- Over-segmentation with dividers
- Too many icons in the header / scattered controls

