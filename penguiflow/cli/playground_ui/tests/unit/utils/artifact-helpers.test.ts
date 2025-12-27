import { describe, it, expect } from 'vitest';
import { formatSize, getMimeIcon, getMimeLabel } from '$lib/utils/artifact-helpers';

describe('artifact-helpers', () => {
  describe('formatSize', () => {
    it('returns "Unknown size" for null', () => {
      expect(formatSize(null)).toBe('Unknown size');
    });

    it('returns "Unknown size" for 0', () => {
      expect(formatSize(0)).toBe('Unknown size');
    });

    it('formats bytes correctly', () => {
      expect(formatSize(100)).toBe('100 B');
      expect(formatSize(500)).toBe('500 B');
      expect(formatSize(1023)).toBe('1023 B');
    });

    it('formats kilobytes correctly', () => {
      expect(formatSize(1024)).toBe('1.0 KB');
      expect(formatSize(2048)).toBe('2.0 KB');
      expect(formatSize(1536)).toBe('1.5 KB');
      expect(formatSize(10240)).toBe('10.0 KB');
      expect(formatSize(1048575)).toBe('1024.0 KB');
    });

    it('formats megabytes correctly', () => {
      expect(formatSize(1048576)).toBe('1.0 MB');
      expect(formatSize(2097152)).toBe('2.0 MB');
      expect(formatSize(1572864)).toBe('1.5 MB');
      expect(formatSize(10485760)).toBe('10.0 MB');
    });
  });

  describe('getMimeIcon', () => {
    it('returns "file" for null', () => {
      expect(getMimeIcon(null)).toBe('file');
    });

    it('returns "image" for image mime types', () => {
      expect(getMimeIcon('image/png')).toBe('image');
      expect(getMimeIcon('image/jpeg')).toBe('image');
      expect(getMimeIcon('image/gif')).toBe('image');
      expect(getMimeIcon('image/webp')).toBe('image');
      expect(getMimeIcon('image/svg+xml')).toBe('image');
    });

    it('returns "pdf" for PDF mime type', () => {
      expect(getMimeIcon('application/pdf')).toBe('pdf');
    });

    it('returns "spreadsheet" for spreadsheet mime types', () => {
      expect(getMimeIcon('application/vnd.ms-excel')).toBe('spreadsheet');
      expect(getMimeIcon('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')).toBe('spreadsheet');
    });

    it('returns "presentation" for presentation mime types', () => {
      expect(getMimeIcon('application/vnd.ms-powerpoint')).toBe('presentation');
      expect(getMimeIcon('application/vnd.openxmlformats-officedocument.presentationml.presentation')).toBe('presentation');
    });

    it('returns "file" for unknown mime types', () => {
      expect(getMimeIcon('application/json')).toBe('file');
      expect(getMimeIcon('text/plain')).toBe('file');
      expect(getMimeIcon('application/octet-stream')).toBe('file');
    });
  });

  describe('getMimeLabel', () => {
    it('returns "file" for null', () => {
      expect(getMimeLabel(null)).toBe('file');
    });

    it('returns subtype for standard mime types', () => {
      expect(getMimeLabel('image/png')).toBe('png');
      expect(getMimeLabel('image/jpeg')).toBe('jpeg');
      expect(getMimeLabel('application/pdf')).toBe('pdf');
      expect(getMimeLabel('text/plain')).toBe('plain');
      expect(getMimeLabel('application/json')).toBe('json');
    });

    it('returns type if no subtype', () => {
      expect(getMimeLabel('text')).toBe('text');
    });

    it('handles complex subtypes', () => {
      expect(getMimeLabel('application/vnd.ms-excel')).toBe('vnd.ms-excel');
      expect(getMimeLabel('image/svg+xml')).toBe('svg+xml');
    });
  });
});
