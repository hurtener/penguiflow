import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import ArtifactsCard from '$lib/components/sidebar-right/artifacts/ArtifactsCard.svelte';
import { artifactsStore } from '$lib/stores/artifacts.svelte';
import type { ArtifactStoredEvent } from '$lib/types';
import * as api from '$lib/services/api';

// Mock the api module
vi.mock('$lib/services/api', () => ({
  downloadArtifact: vi.fn()
}));

describe('ArtifactsCard component', () => {
  const createMockEvent = (id: string, filename: string): ArtifactStoredEvent => ({
    artifact_id: id,
    mime_type: 'application/pdf',
    size_bytes: 1024,
    filename,
    source: {},
    trace_id: 'trace-1',
    session_id: 'session-1',
    ts: Date.now()
  });

  beforeEach(() => {
    vi.clearAllMocks();
    artifactsStore.clear();
  });

  describe('empty state', () => {
    it('renders card with Artifacts title', () => {
      render(ArtifactsCard);

      const title = document.querySelector('.artifacts-title');
      expect(title?.textContent).toContain('Artifacts');
    });

    it('shows empty state message when no artifacts', () => {
      render(ArtifactsCard);

      const emptyMessage = document.querySelector('.no-artifacts');
      expect(emptyMessage).toBeTruthy();
      expect(emptyMessage?.textContent).toContain('No artifacts yet');
    });

    it('does not show count badge when empty', () => {
      render(ArtifactsCard);

      const badge = document.querySelector('.count-badge');
      expect(badge).toBeNull();
    });

    it('does not show Download All button when empty', () => {
      render(ArtifactsCard);

      const downloadAllBtn = document.querySelector('.download-all-btn');
      expect(downloadAllBtn).toBeNull();
    });
  });

  describe('with artifacts', () => {
    it('shows count badge with correct number', () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'report1.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-2', 'report2.pdf'));

      render(ArtifactsCard);

      const badge = document.querySelector('.count-badge');
      expect(badge?.textContent).toBe('2');
    });

    it('renders artifact items', () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'report1.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-2', 'report2.pdf'));

      render(ArtifactsCard);

      const items = document.querySelectorAll('.artifact-item');
      expect(items.length).toBe(2);
    });

    it('does not show empty state message when artifacts exist', () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'report1.pdf'));

      render(ArtifactsCard);

      const emptyMessage = document.querySelector('.no-artifacts');
      expect(emptyMessage).toBeNull();
    });

    it('shows Download All button when more than 1 artifact', () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'report1.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-2', 'report2.pdf'));

      render(ArtifactsCard);

      const downloadAllBtn = document.querySelector('.download-all-btn');
      expect(downloadAllBtn).toBeTruthy();
      expect(downloadAllBtn?.textContent).toContain('Download All');
    });
  });

  describe('single artifact', () => {
    it('shows count badge for single artifact', () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'single.pdf'));

      render(ArtifactsCard);

      const badge = document.querySelector('.count-badge');
      expect(badge?.textContent).toBe('1');
    });

    it('does not show Download All button for single artifact', () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'single.pdf'));

      render(ArtifactsCard);

      const downloadAllBtn = document.querySelector('.download-all-btn');
      expect(downloadAllBtn).toBeNull();
    });
  });

  describe('Download All functionality', () => {
    it('downloads all artifacts when clicked', async () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'report1.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-2', 'report2.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-3', 'report3.pdf'));

      const mockDownload = vi.mocked(api.downloadArtifact).mockResolvedValue();

      render(ArtifactsCard);

      const downloadAllBtn = document.querySelector('.download-all-btn') as HTMLButtonElement;
      expect(downloadAllBtn).toBeTruthy();

      await fireEvent.click(downloadAllBtn);

      await waitFor(() => {
        expect(mockDownload).toHaveBeenCalledTimes(3);
      });
    });

    it('disables button while downloading', async () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'report1.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-2', 'report2.pdf'));

      vi.mocked(api.downloadArtifact).mockImplementation(
        () => new Promise(resolve => setTimeout(resolve, 50))
      );

      render(ArtifactsCard);

      const downloadAllBtn = document.querySelector('.download-all-btn') as HTMLButtonElement;
      await fireEvent.click(downloadAllBtn);

      expect(downloadAllBtn.disabled).toBe(true);
    });

    it('shows Downloading... text while in progress', async () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'report1.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-2', 'report2.pdf'));

      vi.mocked(api.downloadArtifact).mockImplementation(
        () => new Promise(resolve => setTimeout(resolve, 100))
      );

      render(ArtifactsCard);

      const downloadAllBtn = document.querySelector('.download-all-btn') as HTMLButtonElement;
      await fireEvent.click(downloadAllBtn);

      expect(downloadAllBtn.textContent).toContain('Downloading...');
    });

    it('continues downloading other artifacts on error', async () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'report1.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-2', 'report2.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-3', 'report3.pdf'));

      const mockDownload = vi.mocked(api.downloadArtifact);
      mockDownload
        .mockResolvedValueOnce()
        .mockRejectedValueOnce(new Error('Failed'))
        .mockResolvedValueOnce();

      render(ArtifactsCard);

      const downloadAllBtn = document.querySelector('.download-all-btn') as HTMLButtonElement;
      await fireEvent.click(downloadAllBtn);

      await waitFor(() => {
        expect(mockDownload).toHaveBeenCalledTimes(3);
      });
    });

    it('re-enables button after all downloads complete', async () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'report1.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-2', 'report2.pdf'));

      vi.mocked(api.downloadArtifact).mockResolvedValue();

      render(ArtifactsCard);

      const downloadAllBtn = document.querySelector('.download-all-btn') as HTMLButtonElement;
      await fireEvent.click(downloadAllBtn);

      await waitFor(() => {
        expect(downloadAllBtn.disabled).toBe(false);
        expect(downloadAllBtn.textContent).toContain('Download All');
      });
    });
  });

  describe('accessibility', () => {
    it('has accessible button labels', () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'report1.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-2', 'report2.pdf'));

      render(ArtifactsCard);

      const downloadAllBtn = document.querySelector('.download-all-btn');
      expect(downloadAllBtn).toBeTruthy();
      // The button has aria-label attribute
      expect(downloadAllBtn?.getAttribute('aria-label')).toBeTruthy();
    });

    it('updates aria-label while downloading', async () => {
      artifactsStore.addArtifact(createMockEvent('artifact-1', 'report1.pdf'));
      artifactsStore.addArtifact(createMockEvent('artifact-2', 'report2.pdf'));

      vi.mocked(api.downloadArtifact).mockImplementation(
        () => new Promise(resolve => setTimeout(resolve, 100))
      );

      render(ArtifactsCard);

      const downloadAllBtn = document.querySelector('.download-all-btn') as HTMLButtonElement;
      await fireEvent.click(downloadAllBtn);

      expect(downloadAllBtn.getAttribute('aria-label')).toContain('Downloading');
    });
  });
});
