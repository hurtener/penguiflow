import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import MobileHeaderHost from './MobileHeaderHost.svelte';

describe('MobileHeader component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders header with agent name', () => {
    render(MobileHeaderHost);
    expect(screen.getByText('Test Agent')).toBeTruthy();
  });

  it('renders hamburger menu button', () => {
    render(MobileHeaderHost);
    const menuBtn = screen.getByLabelText('Toggle menu');
    expect(menuBtn).toBeTruthy();
    expect(menuBtn.getAttribute('type')).toBe('button');
  });

  it('hamburger button has correct type attribute', () => {
    render(MobileHeaderHost);
    const menuBtn = screen.getByLabelText('Toggle menu');
    expect(menuBtn.getAttribute('type')).toBe('button');
  });

  it('drawer is hidden by default', () => {
    render(MobileHeaderHost);
    const drawer = document.querySelector('.drawer');
    expect(drawer).toBeNull();
  });

  it('opens drawer when hamburger clicked', async () => {
    render(MobileHeaderHost);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    const drawer = document.querySelector('.drawer');
    expect(drawer).toBeTruthy();
  });

  it('shows backdrop when drawer is open', async () => {
    render(MobileHeaderHost);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    const backdrop = screen.getByLabelText('Close menu');
    expect(backdrop).toBeTruthy();
  });

  it('renders three tabs in drawer', async () => {
    render(MobileHeaderHost);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    const tabs = Array.from(document.querySelectorAll<HTMLButtonElement>('.drawer-tab'));
    expect(tabs.map(tab => tab.textContent?.trim())).toEqual(['Info', 'Spec', 'Config']);
  });

  it('tab buttons have correct type attribute', async () => {
    render(MobileHeaderHost);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    const tabButtons = document.querySelectorAll('.drawer-tab');
    tabButtons.forEach(btn => {
      expect(btn.getAttribute('type')).toBe('button');
    });
  });

  it('Info tab is active by default', async () => {
    render(MobileHeaderHost);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    const infoTab = document.querySelector<HTMLButtonElement>('.drawer-tab.active');
    expect(infoTab?.textContent?.trim()).toBe('Info');
  });

  it('switches active tab on click', async () => {
    render(MobileHeaderHost);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);

    const tabs = Array.from(document.querySelectorAll<HTMLButtonElement>('.drawer-tab'));
    const configTab = tabs.find(tab => tab.textContent?.trim() === 'Config');
    const infoTab = tabs.find(tab => tab.textContent?.trim() === 'Info');
    expect(configTab).toBeTruthy();
    expect(infoTab).toBeTruthy();

    await fireEvent.click(configTab!);

    expect(configTab?.classList.contains('active')).toBe(true);
    expect(infoTab?.classList.contains('active')).toBe(false);
  });

  it('closes drawer when backdrop clicked', async () => {
    render(MobileHeaderHost);
    const menuBtn = screen.getByLabelText('Toggle menu');

    await fireEvent.click(menuBtn);
    expect(document.querySelector('.drawer')).toBeTruthy();

    const backdrop = screen.getByLabelText('Close menu');
    await fireEvent.click(backdrop);

    expect(document.querySelector('.drawer')).toBeNull();
  });

  it('toggles drawer closed when hamburger clicked again', async () => {
    render(MobileHeaderHost);
    const menuBtn = screen.getByLabelText('Toggle menu');

    // Open
    await fireEvent.click(menuBtn);
    expect(document.querySelector('.drawer')).toBeTruthy();

    // Close
    await fireEvent.click(menuBtn);
    expect(document.querySelector('.drawer')).toBeNull();
  });

  it('hamburger icon transforms when open', async () => {
    render(MobileHeaderHost);
    const menuBtn = screen.getByLabelText('Toggle menu');

    const hamburger = document.querySelector('.hamburger');
    expect(hamburger?.classList.contains('open')).toBe(false);

    await fireEvent.click(menuBtn);

    expect(hamburger?.classList.contains('open')).toBe(true);
  });
});
