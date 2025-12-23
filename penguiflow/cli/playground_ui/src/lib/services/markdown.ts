import { marked } from 'marked';

// Configure marked options
marked.setOptions({
  breaks: true,
  gfm: true
});

/**
 * Render markdown to HTML (synchronous for streaming compatibility)
 */
export function renderMarkdown(text: string): string {
  if (!text) return '';
  try {
    return marked.parse(text, { async: false }) as string;
  } catch {
    return text;
  }
}
