import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import MobileHeader from '$lib/components/mobile/MobileHeader.svelte';

// Mock the agentStore
vi.mock('$lib/stores', () => ({
  agentStore: {
    meta: { name: 'Test Agent' }
  }
}));

describe('MobileHeader component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders header with agent name', () => {
    render(MobileHeader);
    expect(screen.getByText('Test Agent')).toBeTruthy();
  });

  it('renders hamburger menu button', () => {
    render(MobileHeader);
    const menuBtn = screen.getByLabelText('Toggle menu');
    expect(menuBtn).toBeTruthy();
    expect(menuBtn.getAttribute('type')).toBe('button');
  });

  it('hamburger button has correct type attribute', () => {
    render(MobileHeader);
    const menuBtn = screen.getByLabelText('Toggle menu');
    expect(menuBtn.getAttribute('type')).toBe('button');
  });

  it('drawer is hidden by default', () => {
    render(MobileHeader);
    const drawer = document.querySelector('.drawer');
    expect(drawer).toBeNull();
  });

  it('opens drawer when hamburger clicked', async () => {
    render(MobileHeader);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    const drawer = document.querySelector('.drawer');
    expect(drawer).toBeTruthy();
  });

  it('shows backdrop when drawer is open', async () => {
    render(MobileHeader);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    const backdrop = screen.getByLabelText('Close menu');
    expect(backdrop).toBeTruthy();
  });

  it('renders three tabs in drawer', async () => {
    render(MobileHeader);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    expect(screen.getByText('Info')).toBeTruthy();
    expect(screen.getByText('Spec')).toBeTruthy();
    expect(screen.getByText('Actions')).toBeTruthy();
  });

  it('tab buttons have correct type attribute', async () => {
    render(MobileHeader);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    const tabButtons = document.querySelectorAll('.drawer-tab');
    tabButtons.forEach(btn => {
      expect(btn.getAttribute('type')).toBe('button');
    });
  });

  it('Info tab is active by default', async () => {
    render(MobileHeader);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    const infoTab = screen.getByText('Info');
    expect(infoTab.classList.contains('active')).toBe(true);
  });

  it('switches active tab on click', async () => {
    render(MobileHeader);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    const specTab = screen.getByText('Spec');
    await fireEvent.click(specTab);

    expect(specTab.classList.contains('active')).toBe(true);

    const infoTab = screen.getByText('Info');
    expect(infoTab.classList.contains('active')).toBe(false);
  });

  it('closes drawer when backdrop clicked', async () => {
    render(MobileHeader);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);
    expect(document.querySelector('.drawer')).toBeTruthy();

    const backdrop = screen.getByLabelText('Close menu');
    await fireEvent.click(backdrop);

    expect(document.querySelector('.drawer')).toBeNull();
  });

  it('toggles drawer closed when hamburger clicked again', async () => {
    render(MobileHeader);
    const menuBtn = screen.getByLabelText('Toggle menu');

    // Open
    await fireEvent.click(menuBtn);
    expect(document.querySelector('.drawer')).toBeTruthy();

    // Close
    await fireEvent.click(menuBtn);
    expect(document.querySelector('.drawer')).toBeNull();
  });

  it('hamburger icon transforms when open', async () => {
    render(MobileHeader);
    const menuBtn = screen.getByLabelText('Toggle menu');

    const hamburger = document.querySelector('.hamburger');
    expect(hamburger?.classList.contains('open')).toBe(false);

    await fireEvent.click(menuBtn);

    expect(hamburger?.classList.contains('open')).toBe(true);
  });
});
