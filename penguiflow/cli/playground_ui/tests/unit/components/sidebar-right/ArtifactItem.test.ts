import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import ArtifactItem from '$lib/components/sidebar-right/artifacts/ArtifactItem.svelte';
import type { ArtifactRef } from '$lib/types';
import * as api from '$lib/services/api';

// Mock the api module
vi.mock('$lib/services/api', () => ({
  downloadArtifact: vi.fn()
}));

// Mock sessionStore
vi.mock('$lib/stores', () => ({
  sessionStore: {
    sessionId: 'test-session-123'
  }
}));

describe('ArtifactItem component', () => {
  const mockArtifact: ArtifactRef = {
    id: 'artifact-123',
    mime_type: 'application/pdf',
    size_bytes: 1024,
    filename: 'report.pdf',
    sha256: null,
    source: { tool: 'pdf_generator' }
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('renders artifact filename', () => {
      render(ArtifactItem, { props: { artifact: mockArtifact } });

      const name = document.querySelector('.artifact-name');
      expect(name?.textContent).toBe('report.pdf');
    });

    it('renders artifact id when filename is null', () => {
      const artifactWithoutFilename: ArtifactRef = {
        ...mockArtifact,
        filename: null
      };

      render(ArtifactItem, { props: { artifact: artifactWithoutFilename } });

      const name = document.querySelector('.artifact-name');
      expect(name?.textContent).toBe('artifact-123');
    });

    it('displays formatted file size', () => {
      render(ArtifactItem, { props: { artifact: mockArtifact } });

      const meta = document.querySelector('.artifact-meta');
      expect(meta?.textContent).toContain('1.0 KB');
    });

    it('displays mime type pill', () => {
      render(ArtifactItem, { props: { artifact: mockArtifact } });

      const pill = document.querySelector('.pill');
      expect(pill?.textContent).toBe('pdf');
    });

    it('renders download button', () => {
      render(ArtifactItem, { props: { artifact: mockArtifact } });

      const button = document.querySelector('.download-btn');
      expect(button).toBeTruthy();
      expect(button?.getAttribute('aria-label')).toContain('Download');
    });
  });

  describe('file icons by mime type', () => {
    it('shows pdf icon for PDF files', () => {
      render(ArtifactItem, { props: { artifact: mockArtifact } });

      const icon = document.querySelector('.artifact-icon');
      expect(icon?.getAttribute('data-type')).toBe('pdf');
    });

    it('shows image icon for image files', () => {
      const imageArtifact: ArtifactRef = {
        ...mockArtifact,
        mime_type: 'image/png'
      };

      render(ArtifactItem, { props: { artifact: imageArtifact } });

      const icon = document.querySelector('.artifact-icon');
      expect(icon?.getAttribute('data-type')).toBe('image');
    });

    it('shows spreadsheet icon for excel files', () => {
      const excelArtifact: ArtifactRef = {
        ...mockArtifact,
        mime_type: 'application/vnd.ms-excel'
      };

      render(ArtifactItem, { props: { artifact: excelArtifact } });

      const icon = document.querySelector('.artifact-icon');
      expect(icon?.getAttribute('data-type')).toBe('spreadsheet');
    });

    it('shows file icon for unknown types', () => {
      const unknownArtifact: ArtifactRef = {
        ...mockArtifact,
        mime_type: 'application/octet-stream'
      };

      render(ArtifactItem, { props: { artifact: unknownArtifact } });

      const icon = document.querySelector('.artifact-icon');
      expect(icon?.getAttribute('data-type')).toBe('file');
    });
  });

  describe('download functionality', () => {
    it('calls downloadArtifact on click', async () => {
      const mockDownload = vi.mocked(api.downloadArtifact).mockResolvedValue();

      render(ArtifactItem, { props: { artifact: mockArtifact } });

      const button = document.querySelector('.download-btn') as HTMLButtonElement;
      await fireEvent.click(button);

      expect(mockDownload).toHaveBeenCalledWith(
        'artifact-123',
        'test-session-123',
        'report.pdf'
      );
    });

    it('disables button while downloading', async () => {
      vi.mocked(api.downloadArtifact).mockImplementation(
        () => new Promise(resolve => setTimeout(resolve, 100))
      );

      render(ArtifactItem, { props: { artifact: mockArtifact } });

      const button = document.querySelector('.download-btn') as HTMLButtonElement;
      await fireEvent.click(button);

      expect(button.disabled).toBe(true);
    });

    it('shows spinner while downloading', async () => {
      vi.mocked(api.downloadArtifact).mockImplementation(
        () => new Promise(resolve => setTimeout(resolve, 100))
      );

      render(ArtifactItem, { props: { artifact: mockArtifact } });

      const button = document.querySelector('.download-btn') as HTMLButtonElement;
      await fireEvent.click(button);

      const spinner = document.querySelector('.spinner');
      expect(spinner).toBeTruthy();
    });

    it('shows error message on download failure', async () => {
      vi.mocked(api.downloadArtifact).mockRejectedValue(new Error('Network error'));

      render(ArtifactItem, { props: { artifact: mockArtifact } });

      const button = document.querySelector('.download-btn') as HTMLButtonElement;
      await fireEvent.click(button);

      await waitFor(() => {
        const error = document.querySelector('.artifact-error');
        expect(error?.textContent).toBe('Network error');
      });
    });

    it('re-enables button after download completes', async () => {
      vi.mocked(api.downloadArtifact).mockResolvedValue();

      render(ArtifactItem, { props: { artifact: mockArtifact } });

      const button = document.querySelector('.download-btn') as HTMLButtonElement;
      await fireEvent.click(button);

      await waitFor(() => {
        expect(button.disabled).toBe(false);
      });
    });
  });

  describe('edge cases', () => {
    it('handles null mime_type', () => {
      const noMimeArtifact: ArtifactRef = {
        ...mockArtifact,
        mime_type: null
      };

      render(ArtifactItem, { props: { artifact: noMimeArtifact } });

      const icon = document.querySelector('.artifact-icon');
      expect(icon?.getAttribute('data-type')).toBe('file');
    });

    it('handles null size_bytes', () => {
      const noSizeArtifact: ArtifactRef = {
        ...mockArtifact,
        size_bytes: null
      };

      render(ArtifactItem, { props: { artifact: noSizeArtifact } });

      const meta = document.querySelector('.artifact-meta');
      expect(meta?.textContent).toContain('Unknown size');
    });

    it('passes undefined filename when filename is null', async () => {
      const mockDownload = vi.mocked(api.downloadArtifact).mockResolvedValue();

      const noFilenameArtifact: ArtifactRef = {
        ...mockArtifact,
        filename: null
      };

      render(ArtifactItem, { props: { artifact: noFilenameArtifact } });

      const button = document.querySelector('.download-btn') as HTMLButtonElement;
      await fireEvent.click(button);

      expect(mockDownload).toHaveBeenCalledWith(
        'artifact-123',
        'test-session-123',
        undefined
      );
    });
  });
});
