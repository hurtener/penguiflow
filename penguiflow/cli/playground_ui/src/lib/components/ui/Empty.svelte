<script lang="ts">
  interface Props {
    icon?: string;
    title: string;
    subtitle?: string;
    inline?: boolean;
  }

  let { icon, title, subtitle, inline = false }: Props = $props();
</script>

<div class="empty" class:inline>
  {#if icon}
    <div class="icon">{icon}</div>
  {/if}
  <div class="title">{title}</div>
  {#if subtitle}
    <div class="subtitle">{subtitle}</div>
  {/if}
</div>

<style>
  .empty {
    text-align: center;
    color: var(--color-muted, #7a756d);
    padding: 30px 10px;
  }

  .empty.inline {
    padding: 12px;
  }

  .icon {
    font-size: 24px;
    margin-bottom: 6px;
    opacity: 0.6;
  }

  .title {
    font-weight: 600;
    font-size: 13px;
    margin-bottom: 4px;
  }

  .subtitle {
    font-size: 11px;
    color: var(--color-muted-light, #8a847c);
  }
</style>
