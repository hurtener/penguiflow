<script lang="ts">
  import { marked } from 'marked';
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
      renderer.html = (html) => escapeHtml(html);
    }
    if (!syntaxHighlight) {
      renderer.code = (code, infostring) => {
        const lang = infostring ? `language-${infostring}` : '';
        return `<pre><code class="${lang}">${escapeHtml(code)}</code></pre>`;
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
  }

  .markdown :global(pre code) {
    background: transparent;
    padding: 0;
  }
</style>
