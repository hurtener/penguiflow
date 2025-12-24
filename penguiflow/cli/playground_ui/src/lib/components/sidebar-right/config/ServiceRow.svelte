<script lang="ts">
  import type { ServiceInfo } from '$lib/types';
  import { Pill } from '$lib/components/ui';

  interface Props {
    service: ServiceInfo;
  }

  let { service }: Props = $props();
</script>

<div class="service-row">
  <div>
    <div class="service-name">{service.name}</div>
    <div class="muted tiny">{service.url || 'not configured'}</div>
  </div>
  <Pill
    variant={service.status === 'enabled' ? 'subtle' : 'ghost'}
    size="small"
  >
    {service.status}
  </Pill>
</div>

<style>
  .service-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 0;
  }

  .service-name {
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
</style>
