# Interface Design (Design Soul) — Starter tokens

Use these as a starting point, then tune to the product’s brand and accessibility needs.

## Colors

- Canvas (parchment): `#FAF7F2`
- Surface (cream): `#F4EFE6`
- Surface 2 (warm gray-beige): `#ECE6DC`
- Border (warm hairline): `#D8D0C4`
- Text (charcoal): `#2B2723`
- Text muted (warm gray): `#6A625B`

Accents / states

- Primary accent (muted mint/teal): `#3B9C94`
- Primary hover: `#348A83`
- Link / secondary emphasis (slate blue): `#3D6A7E`
- Warm accent (terracotta): `#C97A64`
- Success: `#3F8E6B`
- Warning: `#C9943B`
- Error: `#B64A4A`

## Radius

- Controls (inputs/chips): `10px`
- Cards/panels: `16px`
- Popovers/toasts: `20px`

## Shadows

- Soft shadow (small): `0 1px 2px rgba(43, 39, 35, 0.06)`
- Soft shadow (medium): `0 8px 24px rgba(43, 39, 35, 0.08)`

## Spacing scale

Use consistent steps; default scale:

`4px, 8px, 12px, 16px, 24px, 32px, 48px`

## CSS variables (example)

```css
:root {
  --ifd-color-canvas: #faf7f2;
  --ifd-color-surface: #f4efe6;
  --ifd-color-surface-2: #ece6dc;
  --ifd-color-border: #d8d0c4;
  --ifd-color-text: #2b2723;
  --ifd-color-text-muted: #6a625b;

  --ifd-color-accent: #3b9c94;
  --ifd-color-accent-hover: #348a83;
  --ifd-color-link: #3d6a7e;
  --ifd-color-accent-warm: #c97a64;
  --ifd-color-success: #3f8e6b;
  --ifd-color-warning: #c9943b;
  --ifd-color-error: #b64a4a;

  --ifd-radius-control: 10px;
  --ifd-radius-card: 16px;
  --ifd-radius-popover: 20px;

  --ifd-shadow-sm: 0 1px 2px rgba(43, 39, 35, 0.06);
  --ifd-shadow-md: 0 8px 24px rgba(43, 39, 35, 0.08);
}
```

For a generated version, run `scripts/generate_tokens.py`.

