<script lang="ts">
  import { Column } from '$lib/components/layout';
  import { EventsCard } from './events';
  import { ArtifactsCard } from './artifacts';
</script>

<Column position="right">
  <EventsCard />
  <ArtifactsCard />
</Column>
