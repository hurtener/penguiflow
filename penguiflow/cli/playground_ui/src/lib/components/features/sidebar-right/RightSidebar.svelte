<script lang="ts">
  import { Column } from '$lib/components/containers';
  import { EventsCard } from './events';
  import { ArtifactsCard } from './artifacts';
  import { TasksCard } from './tasks';
  import { NotificationsCard } from './notifications';
</script>

<Column position="right">
  <NotificationsCard />
  <EventsCard />
  <TasksCard />
  <ArtifactsCard />
</Column>
