<script lang="ts">
  import DiffTreeNode from './DiffTreeNode.svelte';

  type DiffTree = {
    key: string;
    kind: 'same' | 'diff' | 'mixed';
    reference?: string;
    actual?: string;
    children?: DiffTree[];
  };

  interface Props {
    node: DiffTree;
    depth?: number;
  }

  let { node, depth = 0 }: Props = $props();
</script>

<div class="tree-node" style={`--depth:${depth}`}>
  {#if node.kind === 'same'}
    <span class="kv-token same">✓ {node.key}</span>
  {:else if node.kind === 'diff'}
    <span class="kv-token diff">{node.key}: {node.reference} -&gt; {node.actual}</span>
  {:else}
    <span class="kv-token mixed">{node.key}</span>
  {/if}
</div>

{#if node.kind === 'mixed' && node.children && node.children.length > 0}
  <div class="tree-children">
    {#each node.children as child, index (`${node.key}-${index}-${child.key}`)}
      <DiffTreeNode node={child} depth={depth + 1} />
    {/each}
  </div>
{/if}

<style>
  .tree-node {
    margin-left: calc(var(--depth, 0) * 12px);
  }

  .tree-children {
    display: grid;
    gap: 4px;
  }

  .kv-token {
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 11px;
    line-height: 1.4;
    border: 1px solid transparent;
    display: inline-flex;
    width: fit-content;
  }

  .kv-token.same {
    background: #f5faf7;
    border-color: #cfe7d8;
    color: #50745b;
  }

  .kv-token.diff {
    background: #fff1f1;
    border-color: #e0a6a6;
    color: #8a2d2d;
  }

  .kv-token.mixed {
    background: #f6f1e8;
    border-color: #ddd2c4;
    color: #5f5a51;
    font-weight: 600;
  }
</style>
