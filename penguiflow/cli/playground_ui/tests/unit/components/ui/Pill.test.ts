import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/svelte';
import Pill from '$lib/components/ui/Pill.svelte';

describe('Pill component', () => {
  it('renders with default variant', () => {
    render(Pill, { props: {} });

    const pill = document.querySelector('.pill');
    expect(pill).toBeTruthy();
    expect(pill?.classList.contains('default')).toBe(true);
  });

  it('renders with subtle variant', () => {
    render(Pill, { props: { variant: 'subtle' } });

    const pill = document.querySelector('.pill');
    expect(pill?.classList.contains('subtle')).toBe(true);
  });

  it('renders with ghost variant', () => {
    render(Pill, { props: { variant: 'ghost' } });

    const pill = document.querySelector('.pill');
    expect(pill?.classList.contains('ghost')).toBe(true);
  });

  it('renders with small size', () => {
    render(Pill, { props: { size: 'small' } });

    const pill = document.querySelector('.pill');
    expect(pill?.classList.contains('small')).toBe(true);
  });

  it('applies custom class', () => {
    render(Pill, { props: { class: 'custom-class' } });

    const pill = document.querySelector('.pill');
    expect(pill?.classList.contains('custom-class')).toBe(true);
  });

  it('renders as a span element', () => {
    render(Pill, { props: {} });

    const pill = document.querySelector('span.pill');
    expect(pill).toBeTruthy();
  });
});
