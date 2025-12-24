import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/svelte';
import Empty from '$lib/components/ui/Empty.svelte';

describe('Empty component', () => {
  it('renders title', () => {
    render(Empty, {
      props: { title: 'No items found' }
    });

    expect(screen.getByText('No items found')).toBeTruthy();
  });

  it('renders optional icon', () => {
    render(Empty, {
      props: { title: 'Empty', icon: '📭' }
    });

    expect(screen.getByText('📭')).toBeTruthy();
  });

  it('renders optional subtitle', () => {
    render(Empty, {
      props: { title: 'Empty', subtitle: 'Try adding some items' }
    });

    expect(screen.getByText('Try adding some items')).toBeTruthy();
  });

  it('does not render icon when not provided', () => {
    render(Empty, {
      props: { title: 'Empty' }
    });

    const icon = document.querySelector('.icon');
    expect(icon).toBeNull();
  });

  it('does not render subtitle when not provided', () => {
    render(Empty, {
      props: { title: 'Empty' }
    });

    const subtitle = document.querySelector('.subtitle');
    expect(subtitle).toBeNull();
  });

  it('applies inline class when inline prop is true', () => {
    render(Empty, {
      props: { title: 'Empty', inline: true }
    });

    const empty = document.querySelector('.empty');
    expect(empty?.classList.contains('inline')).toBe(true);
  });

  it('does not apply inline class by default', () => {
    render(Empty, {
      props: { title: 'Empty' }
    });

    const empty = document.querySelector('.empty');
    expect(empty?.classList.contains('inline')).toBe(false);
  });

  it('renders all elements together', () => {
    render(Empty, {
      props: {
        title: 'No Results',
        icon: '🔍',
        subtitle: 'Try a different search'
      }
    });

    expect(screen.getByText('🔍')).toBeTruthy();
    expect(screen.getByText('No Results')).toBeTruthy();
    expect(screen.getByText('Try a different search')).toBeTruthy();
  });
});
