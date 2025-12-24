import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import Tabs from '$lib/components/ui/Tabs.svelte';

describe('Tabs component', () => {
  const defaultTabs = [
    { id: 'tab1', label: 'Tab 1' },
    { id: 'tab2', label: 'Tab 2' },
    { id: 'tab3', label: 'Tab 3' }
  ];

  it('renders all tabs', () => {
    render(Tabs, {
      props: { tabs: defaultTabs, active: 'tab1' }
    });

    expect(screen.getByText('Tab 1')).toBeTruthy();
    expect(screen.getByText('Tab 2')).toBeTruthy();
    expect(screen.getByText('Tab 3')).toBeTruthy();
  });

  it('marks active tab', () => {
    render(Tabs, {
      props: { tabs: defaultTabs, active: 'tab2' }
    });

    const tab2 = screen.getByText('Tab 2');
    expect(tab2.classList.contains('active')).toBe(true);

    const tab1 = screen.getByText('Tab 1');
    expect(tab1.classList.contains('active')).toBe(false);
  });

  it('calls onchange when tab clicked', async () => {
    const onchange = vi.fn();

    render(Tabs, {
      props: { tabs: defaultTabs, active: 'tab1', onchange }
    });

    const tab2 = screen.getByText('Tab 2');
    await fireEvent.click(tab2);

    expect(onchange).toHaveBeenCalledWith('tab2');
  });

  it('renders tabs as buttons', () => {
    render(Tabs, {
      props: { tabs: defaultTabs, active: 'tab1' }
    });

    const buttons = document.querySelectorAll('button.tab');
    expect(buttons).toHaveLength(3);
  });

  it('handles empty tabs array', () => {
    render(Tabs, {
      props: { tabs: [], active: '' }
    });

    const buttons = document.querySelectorAll('button.tab');
    expect(buttons).toHaveLength(0);
  });
});
