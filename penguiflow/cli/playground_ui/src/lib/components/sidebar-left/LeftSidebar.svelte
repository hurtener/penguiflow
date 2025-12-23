<script lang="ts">
  import type { Snippet } from 'svelte';
  import { Column } from '$lib/components/layout';

  interface Props {
    children?: Snippet;
  }

  let { children }: Props = $props();
</script>

<Column position="left">
  {@render children?.()}
</Column>
