/**
 * Utility functions for artifact display and formatting
 */

/**
 * Format file size in human-readable format
 */
export function formatSize(bytes: number | null): string {
  if (!bytes) return 'Unknown size';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Get icon type based on MIME type
 */
export function getMimeIcon(mime: string | null): string {
  if (!mime) return 'file';
  if (mime.startsWith('image/')) return 'image';
  if (mime === 'application/pdf') return 'pdf';
  if (mime.includes('spreadsheet') || mime.includes('excel')) return 'spreadsheet';
  if (mime.includes('presentation') || mime.includes('powerpoint')) return 'presentation';
  return 'file';
}

/**
 * Get human-readable label for MIME type
 */
export function getMimeLabel(mime: string | null): string {
  if (!mime) return 'file';
  const parts = mime.split('/');
  return parts[1] ?? parts[0] ?? 'file';
}
