<script lang="ts">
  import { CodeBlock } from '$lib/components/ui';

  interface Props {
    args?: unknown;
    result?: unknown;
  }

  let { args, result }: Props = $props();
</script>

<details>
  <summary>Details</summary>
  {#if args}
    <CodeBlock label="args" content={args} />
  {/if}
  {#if result}
    <CodeBlock label="result" content={result} />
  {/if}
</details>

<style>
  details {
    margin-top: 6px;
  }

  summary {
    font-size: 10px;
    color: var(--color-muted, #7a756d);
    cursor: pointer;
  }
</style>
