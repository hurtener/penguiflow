<script lang="ts">
  import ArtifactsCard from '$lib/components/features/sidebar-right/artifacts/ArtifactsCard.svelte';
  import { setArtifactsStore, setSessionStore, type ArtifactsStore, type SessionStore } from '$lib/stores';

  export let artifactsStore: ArtifactsStore;
  export let sessionStore: SessionStore;

  setArtifactsStore(artifactsStore);
  setSessionStore(sessionStore);
</script>

<ArtifactsCard />
