<script lang="ts">
  import { marked, type Tokens } from 'marked';
  import DOMPurify from 'dompurify';
  import katex from 'katex';
  import 'katex/dist/katex.min.css';

  interface Props {
    content?: string;
    allowHtml?: boolean;
    syntaxHighlight?: boolean;
    mathEnabled?: boolean;
  }

  let { content = '', allowHtml = false, syntaxHighlight = true, mathEnabled = true }: Props = $props();

  const escapeHtml = (html: string) =>
    html.replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const buildRenderer = () => {
    const renderer = new marked.Renderer();
    if (!allowHtml) {
      renderer.html = ({ text }: Tokens.HTML | Tokens.Tag) => escapeHtml(text);
    }
    if (!syntaxHighlight) {
      renderer.code = ({ text, lang }: Tokens.Code) => {
        const language = lang ? `language-${lang}` : '';
        return `<pre><code class="${language}">${escapeHtml(text)}</code></pre>`;
      };
    }
    return renderer;
  };

  const applyMath = (html: string): string => {
    if (!mathEnabled || typeof DOMParser === 'undefined') {
      return html;
    }
    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, 'text/html');
      const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
      const nodes: Text[] = [];
      while (walker.nextNode()) {
        const node = walker.currentNode as Text;
        const parent = node.parentElement;
        if (parent && (parent.tagName === 'CODE' || parent.tagName === 'PRE')) {
          continue;
        }
        nodes.push(node);
      }

      const mathRegex = /\$\$([^$]+)\$\$|\$([^$\n]+)\$/g;
      for (const node of nodes) {
        const text = node.nodeValue ?? '';
        let match: RegExpExecArray | null;
        let lastIndex = 0;
        let hasMatch = false;
        const fragment = doc.createDocumentFragment();

        while ((match = mathRegex.exec(text)) !== null) {
          hasMatch = true;
          const fullMatch = match[0];
          const displayExpr = match[1];
          const inlineExpr = match[2];
          const expr = (displayExpr ?? inlineExpr ?? '').trim();
          const displayMode = Boolean(displayExpr);

          if (match.index > lastIndex) {
            fragment.appendChild(doc.createTextNode(text.slice(lastIndex, match.index)));
          }

          let rendered = fullMatch;
          try {
            rendered = katex.renderToString(expr, { displayMode, throwOnError: false });
          } catch {
            rendered = fullMatch;
          }
          const container = doc.createElement(displayMode ? 'div' : 'span');
          container.innerHTML = rendered;
          fragment.appendChild(container);
          lastIndex = match.index + fullMatch.length;
        }

        if (!hasMatch) {
          continue;
        }

        if (lastIndex < text.length) {
          fragment.appendChild(doc.createTextNode(text.slice(lastIndex)));
        }

        node.parentNode?.replaceChild(fragment, node);
      }
      return doc.body.innerHTML;
    } catch {
      return html;
    }
  };

  const renderer = $derived(buildRenderer());
  const rawHtml = $derived(marked.parse(content || '', { async: false, renderer }) as string);
  const html = $derived(DOMPurify.sanitize(applyMath(rawHtml), { USE_PROFILES: { html: true, svg: false } }));
</script>

<div class="markdown" class:math={mathEnabled}>
  {@html html}
</div>

<style>
  .markdown {
    padding: 1rem;
    font-size: 0.95rem;
    line-height: 1.6;
  }

  /* Headings */
  .markdown :global(h1),
  .markdown :global(h2),
  .markdown :global(h3),
  .markdown :global(h4),
  .markdown :global(h5),
  .markdown :global(h6) {
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    font-weight: 600;
  }

  .markdown :global(h1:first-child),
  .markdown :global(h2:first-child),
  .markdown :global(h3:first-child) {
    margin-top: 0;
  }

  /* Paragraphs */
  .markdown :global(p) {
    margin: 0.75em 0;
  }

  /* Lists - ensure numbers/bullets have room */
  .markdown :global(ul),
  .markdown :global(ol) {
    margin: 0.75em 0;
    padding-left: 1.75em;
  }

  .markdown :global(li) {
    margin: 0.35em 0;
  }

  .markdown :global(li > ul),
  .markdown :global(li > ol) {
    margin: 0.25em 0;
  }

  /* Code */
  .markdown :global(code) {
    font-family: var(--font-mono, ui-monospace);
    background: #f1f5f9;
    padding: 0.1rem 0.25rem;
    border-radius: 0.25rem;
  }

  .markdown :global(pre) {
    background: #0f172a;
    color: #e2e8f0;
    padding: 0.75rem;
    border-radius: 0.5rem;
    overflow-x: auto;
    margin: 1em 0;
  }

  .markdown :global(pre code) {
    background: transparent;
    padding: 0;
  }

  /* Blockquotes */
  .markdown :global(blockquote) {
    margin: 1em 0;
    padding: 0.5em 1em;
    border-left: 3px solid #cbd5e1;
    background: #f8fafc;
    color: #475569;
  }

  /* Tables */
  .markdown :global(table) {
    border-collapse: collapse;
    margin: 1em 0;
    width: 100%;
  }

  .markdown :global(th),
  .markdown :global(td) {
    border: 1px solid #e2e8f0;
    padding: 0.5em 0.75em;
    text-align: left;
  }

  .markdown :global(th) {
    background: #f8fafc;
    font-weight: 600;
  }

  /* Horizontal rule */
  .markdown :global(hr) {
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 1.5em 0;
  }
</style>
