<script lang="ts">
  interface Props {
    code?: string;
    language?: string;
    filename?: string;
    showLineNumbers?: boolean;
    startLine?: number;
    highlightLines?: number[];
    diff?: boolean;
    maxHeight?: string;
    copyable?: boolean;
  }

  let {
    code = '',
    language = undefined,
    filename = undefined,
    showLineNumbers = true,
    startLine = 1,
    highlightLines = undefined,
    diff = false,
    maxHeight = undefined,
    copyable = true
  }: Props = $props();

  const escapeHtml = (text: string) =>
    text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const lines = $derived(code.split('\n'));
  const highlighted = $derived(new Set(highlightLines ?? []));

  const diffClassFor = (line: string) => {
    if (!diff) return '';
    if (line.startsWith('+')) return 'diff-add';
    if (line.startsWith('-')) return 'diff-remove';
    return 'diff-context';
  };

  function handleCopy() {
    navigator.clipboard.writeText(code);
  }
</script>

<div class="code-block" style={maxHeight ? `max-height: ${maxHeight}` : ''}>
  {#if filename}
    <div class="code-header">
      <span>{filename}</span>
      {#if copyable}
        <button onclick={handleCopy}>Copy</button>
      {/if}
    </div>
  {/if}
  <pre class={diff ? 'diff' : ''}>
    {#each lines as line, idx}
      {@const lineNumber = startLine + idx}
      <div class={`code-line ${highlighted.has(lineNumber) ? 'highlight' : ''} ${diffClassFor(line)}`}>
        {#if showLineNumbers}
          <span class="line-number">{lineNumber}</span>
        {/if}
        <code class={language ? `lang-${language}` : ''}>
          {@html escapeHtml(line)}
        </code>
      </div>
    {/each}
  </pre>
</div>

<style>
  .code-block {
    background: #0f172a;
    color: #e2e8f0;
    border-radius: 0.75rem;
    overflow: auto;
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  }

  .code-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.5rem 0.75rem;
    background: #1e293b;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .code-header button {
    background: #334155;
    border: none;
    color: #e2e8f0;
    padding: 0.25rem 0.5rem;
    border-radius: 0.25rem;
    cursor: pointer;
    font-size: 0.7rem;
  }

  pre {
    margin: 0;
    padding: 0.75rem 0.5rem;
    font-family: inherit;
    font-size: 0.85rem;
    line-height: 1.4;
  }

  .code-line {
    display: flex;
    gap: 0.75rem;
    min-height: 1.4em;
    padding: 0;
    margin: 0;
  }

  .line-number {
    color: #64748b;
    min-width: 2rem;
    text-align: right;
    user-select: none;
    flex-shrink: 0;
  }

  code {
    display: block;
    white-space: pre;
    margin: 0;
    padding: 0;
  }

  .code-line.highlight {
    background: rgba(59, 130, 246, 0.15);
  }

  .code-line.diff-add {
    background: rgba(34, 197, 94, 0.15);
  }

  .code-line.diff-remove {
    background: rgba(239, 68, 68, 0.15);
  }

  .code-line.diff-context {
    opacity: 0.9;
  }
</style>
