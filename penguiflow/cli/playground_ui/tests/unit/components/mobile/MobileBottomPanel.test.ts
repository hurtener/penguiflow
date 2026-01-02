import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/svelte';
import MobileBottomPanel from '$lib/components/features/mobile/MobileBottomPanel.svelte';

describe('MobileBottomPanel component', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders toggle bar with label', () => {
    render(MobileBottomPanel);
    expect(screen.getByText('Show Details')).toBeTruthy();
  });

  it('toggle button has correct type attribute', () => {
    render(MobileBottomPanel);
    const toggleBtn = screen.getByLabelText('Show details panel');
    expect(toggleBtn.getAttribute('type')).toBe('button');
  });

  it('toggle button has aria-expanded attribute', () => {
    render(MobileBottomPanel);
    const toggleBtn = screen.getByLabelText('Show details panel');
    expect(toggleBtn.getAttribute('aria-expanded')).toBe('false');
  });

  it('panel is collapsed by default', () => {
    render(MobileBottomPanel);
    const panel = document.querySelector('.bottom-panel');
    expect(panel?.classList.contains('open')).toBe(false);
  });

  it('tabs are hidden when panel is collapsed', () => {
    render(MobileBottomPanel);
    const tabs = document.querySelector('.panel-tabs');
    expect(tabs).toBeNull();
  });

  it('opens panel when toggle clicked', async () => {
    render(MobileBottomPanel);
    const toggleBtn = screen.getByLabelText('Show details panel');

    await fireEvent.click(toggleBtn);

    const panel = document.querySelector('.bottom-panel');
    expect(panel?.classList.contains('open')).toBe(true);
  });

  it('updates aria-expanded when opened', async () => {
    render(MobileBottomPanel);
    const toggleBtn = screen.getByLabelText('Show details panel');

    await fireEvent.click(toggleBtn);

    // After opening, aria-label changes
    const openToggle = screen.getByLabelText('Hide details panel');
    expect(openToggle.getAttribute('aria-expanded')).toBe('true');
  });

  it('label changes to Hide Details when open', async () => {
    render(MobileBottomPanel);
    const toggleBtn = screen.getByLabelText('Show details panel');

    await fireEvent.click(toggleBtn);

    expect(screen.getByText('Hide Details')).toBeTruthy();
  });

  it('renders three tabs when open', async () => {
    render(MobileBottomPanel);
    const toggleBtn = screen.getByLabelText('Show details panel');

    await fireEvent.click(toggleBtn);

    expect(screen.getByText('Steps')).toBeTruthy();
    expect(screen.getByText('Events')).toBeTruthy();
    expect(screen.getByText('Artifacts')).toBeTruthy();
  });

  it('tab buttons have correct type attribute', async () => {
    render(MobileBottomPanel);
    const toggleBtn = screen.getByLabelText('Show details panel');

    await fireEvent.click(toggleBtn);

    const tabButtons = document.querySelectorAll('.panel-tab');
    tabButtons.forEach(btn => {
      expect(btn.getAttribute('type')).toBe('button');
    });
  });

  it('Steps tab is active by default', async () => {
    render(MobileBottomPanel);
    const toggleBtn = screen.getByLabelText('Show details panel');

    await fireEvent.click(toggleBtn);

    const stepsTab = screen.getByText('Steps');
    expect(stepsTab.classList.contains('active')).toBe(true);
  });

  it('switches active tab on click', async () => {
    render(MobileBottomPanel);
    const toggleBtn = screen.getByLabelText('Show details panel');

    await fireEvent.click(toggleBtn);

    const eventsTab = screen.getByText('Events');
    await fireEvent.click(eventsTab);

    expect(eventsTab.classList.contains('active')).toBe(true);

    const stepsTab = screen.getByText('Steps');
    expect(stepsTab.classList.contains('active')).toBe(false);
  });

  it('closes panel when toggle clicked again', async () => {
    render(MobileBottomPanel);
    const toggleBtn = screen.getByLabelText('Show details panel');

    // Open
    await fireEvent.click(toggleBtn);
    expect(document.querySelector('.bottom-panel')?.classList.contains('open')).toBe(true);

    // Close - need to get the updated toggle button
    const closeBtn = screen.getByLabelText('Hide details panel');
    await fireEvent.click(closeBtn);

    expect(document.querySelector('.bottom-panel')?.classList.contains('open')).toBe(false);
  });

  it('renders toggle handle element', () => {
    render(MobileBottomPanel);
    const handle = document.querySelector('.toggle-handle');
    expect(handle).toBeTruthy();
  });

  it('renders panel content when open', async () => {
    render(MobileBottomPanel);
    const toggleBtn = screen.getByLabelText('Show details panel');

    await fireEvent.click(toggleBtn);

    const content = document.querySelector('.panel-content');
    expect(content).toBeTruthy();
  });

  it('content is hidden when panel is collapsed', () => {
    render(MobileBottomPanel);
    const content = document.querySelector('.panel-content');
    expect(content).toBeNull();
  });
});
