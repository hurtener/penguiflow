<script lang="ts">
  import { Column } from '$lib/components/layout';
  import { EventsCard } from './events';
  import { ConfigCard } from './config';
</script>

<Column position="right">
  <EventsCard />
  <ConfigCard />
</Column>
