<script lang="ts">
  import ComponentRenderer from './ComponentRenderer.svelte';
  import Markdown from './Markdown.svelte';

  interface Section {
    id?: string;
    title?: string;
    content?: string;
    components?: Array<{ component: string; props: Record<string, unknown>; caption?: string }>;
    subsections?: Section[];
  }

  interface Props {
    title?: string;
    subtitle?: string;
    metadata?: { author?: string; date?: string; version?: string };
    toc?: boolean;
    sections?: Section[];
    footer?: string;
  }

  let {
    title = '',
    subtitle = '',
    metadata = {},
    toc = false,
    sections = [],
    footer = ''
  }: Props = $props();

  function slugify(text: string): string {
    return text.toLowerCase().replace(/\s+/g, '-').replace(/[^\w-]/g, '');
  }

  function generateToc(items: Section[], level = 0): Array<{ title: string; id: string; level: number }> {
    const entries: Array<{ title: string; id: string; level: number }> = [];
    for (const section of items) {
      if (section.title) {
        entries.push({ title: section.title, id: section.id || slugify(section.title), level });
      }
      if (section.subsections) {
        entries.push(...generateToc(section.subsections, level + 1));
      }
    }
    return entries;
  }

  const tocEntries = $derived(toc ? generateToc(sections) : []);
</script>

<article class="artifact-report">
  <header class="report-header">
    {#if title}
      <h1>{title}</h1>
    {/if}
    {#if subtitle}
      <p class="subtitle">{subtitle}</p>
    {/if}
    {#if metadata.author || metadata.date}
      <div class="meta">
        {#if metadata.author}
          <span>By {metadata.author}</span>
        {/if}
        {#if metadata.date}
          <span>{metadata.date}</span>
        {/if}
        {#if metadata.version}
          <span>v{metadata.version}</span>
        {/if}
      </div>
    {/if}
  </header>

  {#if toc && tocEntries.length > 0}
    <nav class="toc">
      <h2>Contents</h2>
      <ul>
        {#each tocEntries as entry}
          <li style:margin-left={`${entry.level * 1}rem`}>
            <a href={`#${entry.id}`}>{entry.title}</a>
          </li>
        {/each}
      </ul>
    </nav>
  {/if}

  <div class="report-body">
    {#each sections as section}
      <section id={section.id || (section.title ? slugify(section.title) : undefined)}>
        {#if section.title}
          <h2>{section.title}</h2>
        {/if}

        {#if section.content}
          <Markdown content={section.content} padded={false} />
        {/if}

        {#if section.components}
          {#each section.components as comp}
            <figure class="component-figure">
              <ComponentRenderer component={comp.component} props={comp.props} />
              {#if comp.caption}
                <figcaption>{comp.caption}</figcaption>
              {/if}
            </figure>
          {/each}
        {/if}

        {#if section.subsections}
          {#each section.subsections as subsection}
            <section class="subsection">
              {#if subsection.title}
                <h3>{subsection.title}</h3>
              {/if}
              {#if subsection.content}
                <Markdown content={subsection.content} padded={false} />
              {/if}
              {#if subsection.components}
                {#each subsection.components as comp}
                  <figure class="component-figure">
                    <ComponentRenderer component={comp.component} props={comp.props} />
                    {#if comp.caption}
                      <figcaption>{comp.caption}</figcaption>
                    {/if}
                  </figure>
                {/each}
              {/if}
            </section>
          {/each}
        {/if}
      </section>
    {/each}
  </div>

  {#if footer}
    <footer class="report-footer">
      <Markdown content={footer} padded={false} />
    </footer>
  {/if}
</article>

<style>
  .artifact-report {
    max-width: 900px;
    margin: 0 auto;
    padding: clamp(1.25rem, 4vw, 2.5rem);
    line-height: 1.7;
    color: var(--color-text-secondary, #3c3a36);
  }

  .report-header {
    margin-bottom: 2rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--color-border, #f0ebe4);
  }

  .report-header h1 {
    margin: 0 0 0.5rem;
    font-size: 2rem;
    color: var(--color-text, #1f1f1f);
  }

  .subtitle {
    margin: 0 0 0.5rem;
    font-size: 1.25rem;
    color: var(--color-muted, #6b665f);
  }

  .meta {
    display: flex;
    gap: 1rem;
    font-size: 0.875rem;
    color: var(--color-muted-light, #8a847c);
  }

  .toc {
    margin-bottom: 2rem;
    padding: 1rem 1.25rem;
    background: var(--color-pill-ghost-bg, #f4f0ea);
    border: 1px solid var(--color-border, #f0ebe4);
    border-radius: var(--radius-xl, 16px);
  }

  .toc h2 {
    margin: 0 0 0.5rem;
    font-size: 1rem;
    color: var(--color-text, #1f1f1f);
  }

  .toc ul {
    margin: 0;
    padding-left: 1rem;
    list-style: none;
  }

  .toc li {
    margin: 0.25rem 0;
  }

  .toc a {
    color: var(--color-primary-text, #1f6c68);
    text-decoration: underline;
    text-underline-offset: 2px;
  }

  .toc a:hover {
    color: var(--color-primary-light, #106c67);
    text-decoration-thickness: 2px;
  }

  .report-body section {
    margin-bottom: 2rem;
  }

  .report-body h2 {
    margin: 0 0 1rem;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid var(--color-border, #f0ebe4);
    color: var(--color-text, #1f1f1f);
  }

  .report-body h3 {
    margin: 1.5rem 0 0.75rem;
    color: var(--color-text, #1f1f1f);
  }

  .subsection {
    margin-top: 1.5rem;
    padding-top: 0.25rem;
  }

  .component-figure {
    margin: 1.5rem 0;
  }

  figcaption {
    margin-top: 0.5rem;
    font-size: 0.875rem;
    color: var(--color-muted, #6b665f);
    text-align: center;
    font-style: italic;
  }

  .report-footer {
    margin-top: 2rem;
    padding-top: 1rem;
    border-top: 1px solid var(--color-border, #f0ebe4);
    font-size: 0.875rem;
    color: var(--color-muted, #6b665f);
  }
</style>
