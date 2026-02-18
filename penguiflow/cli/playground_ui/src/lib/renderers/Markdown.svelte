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
    padded?: boolean;
  }

  let {
    content = '',
    allowHtml = false,
    syntaxHighlight = true,
    mathEnabled = true,
    padded = true
  }: Props = $props();

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

<div class="markdown markdown-content" class:math={mathEnabled} class:padded={padded}>
  {@html html}
</div>

<style>
  .markdown {
    font-size: 0.95rem;
    line-height: 1.7;
    color: var(--color-text-secondary, #3c3a36);
    overflow-wrap: anywhere;
  }

  .markdown.padded {
    padding: 1rem;
  }
</style>
