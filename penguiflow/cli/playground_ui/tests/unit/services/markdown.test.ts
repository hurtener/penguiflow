import { describe, it, expect } from 'vitest';
import { renderMarkdown } from '$lib/services/markdown';

describe('markdown service', () => {
  describe('renderMarkdown', () => {
    it('renders basic markdown', () => {
      const result = renderMarkdown('**bold** and *italic*');

      expect(result).toContain('<strong>bold</strong>');
      expect(result).toContain('<em>italic</em>');
    });

    it('renders headings', () => {
      const result = renderMarkdown('# Heading 1\n## Heading 2');

      expect(result).toContain('<h1>Heading 1</h1>');
      expect(result).toContain('<h2>Heading 2</h2>');
    });

    it('renders code blocks', () => {
      const result = renderMarkdown('```js\nconst x = 1;\n```');

      expect(result).toContain('<code');
      expect(result).toContain('const x = 1;');
    });

    it('renders inline code', () => {
      const result = renderMarkdown('Use `npm install`');

      expect(result).toContain('<code>npm install</code>');
    });

    it('renders lists', () => {
      const result = renderMarkdown('- Item 1\n- Item 2');

      expect(result).toContain('<ul>');
      expect(result).toContain('<li>Item 1</li>');
      expect(result).toContain('<li>Item 2</li>');
    });

    it('renders links', () => {
      const result = renderMarkdown('[Link](https://example.com)');

      expect(result).toContain('<a href="https://example.com">Link</a>');
    });

    it('handles line breaks (GFM)', () => {
      const result = renderMarkdown('Line 1\nLine 2');

      // With breaks: true, newlines become <br>
      expect(result).toContain('<br>');
    });

    it('returns empty string for empty input', () => {
      expect(renderMarkdown('')).toBe('');
    });

    it('returns empty string for null-like input', () => {
      expect(renderMarkdown(null as unknown as string)).toBe('');
      expect(renderMarkdown(undefined as unknown as string)).toBe('');
    });

    it('handles plain text without markdown', () => {
      const result = renderMarkdown('Plain text here');

      expect(result).toContain('Plain text here');
      expect(result).toContain('<p>');
    });

    it('renders tables (GFM)', () => {
      const markdown = `
| Col 1 | Col 2 |
|-------|-------|
| A     | B     |
`;
      const result = renderMarkdown(markdown);

      expect(result).toContain('<table>');
      expect(result).toContain('<th>Col 1</th>');
      expect(result).toContain('<td>A</td>');
    });

    it('handles malformed markdown gracefully', () => {
      // Should not throw
      const result = renderMarkdown('**unclosed bold');
      expect(typeof result).toBe('string');
    });
  });
});
