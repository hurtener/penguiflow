<script lang="ts">
  import { CodeBlock } from '$lib/components/composites';
  import Json from '$lib/renderers/Json.svelte';

  interface Props {
    args?: unknown;
    result?: unknown;
  }

  let { args, result }: Props = $props();

  const isObjectLike = (value: unknown): value is Record<string, unknown> =>
    typeof value === 'object' && value !== null && !Array.isArray(value);

  const isInspectable = (value: unknown): boolean => isObjectLike(value) || Array.isArray(value);
</script>

<details>
  <summary>Details</summary>
  {#if args}
    {#if isInspectable(args)}
      <div class="json-entry">
        <div class="json-label">args</div>
        <Json data={args} expandLevel={1} />
      </div>
    {:else}
      <CodeBlock label="args" content={args} />
    {/if}
  {/if}
  {#if result}
    {#if isInspectable(result)}
      <div class="json-entry">
        <div class="json-label">result</div>
        <Json data={result} expandLevel={1} />
      </div>
    {:else}
      <CodeBlock label="result" content={result} />
    {/if}
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

  .json-entry {
    margin-top: 6px;
  }

  .json-label {
    text-transform: uppercase;
    letter-spacing: 0.4px;
    font-size: 9px;
    color: var(--color-muted, #8b857c);
    margin: 0 0 3px;
  }
</style>
