<script lang="ts">
  import type { ToolInfo } from '$lib/types';
  import { Pill } from '$lib/components/ui';

  interface Props {
    tool: ToolInfo;
  }

  let { tool }: Props = $props();
</script>

<div class="tool-row">
  <div>
    <div class="tool-name">{tool.name}</div>
    <div class="muted tiny">{tool.desc}</div>
  </div>
  <div class="tag-row">
    {#each tool.tags as tag, tagIdx (`${tool.name}-${tagIdx}`)}
      <Pill variant="ghost" size="small">{tag}</Pill>
    {/each}
  </div>
</div>

<style>
  .tool-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding: 6px 0;
    gap: 8px;
  }

  .tool-name {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text, #1f1f1f);
  }

  .muted {
    color: var(--color-muted, #7a756d);
  }

  .tiny {
    font-size: 10px;
  }

  .tag-row {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
</style>
