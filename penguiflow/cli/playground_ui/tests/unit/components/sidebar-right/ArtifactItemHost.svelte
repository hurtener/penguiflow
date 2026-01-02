<script lang="ts">
  import ArtifactItem from '$lib/components/features/sidebar-right/artifacts/ArtifactItem.svelte';
  import { createSessionStore, setSessionStore } from '$lib/stores';
  import type { ArtifactRef } from '$lib/types';

  export let sessionId = 'test-session-123';
  export let artifact: ArtifactRef;

  const sessionStore = createSessionStore();
  sessionStore.sessionId = sessionId;
  setSessionStore(sessionStore);
</script>

<ArtifactItem {artifact} />
