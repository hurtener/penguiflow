<script lang="ts">
  interface Props {
    value: string | number;
    label: string;
    format?: 'number' | 'currency' | 'percent' | 'compact';
    prefix?: string;
    suffix?: string;
    change?: number;
    changeLabel?: string;
    sparkline?: number[];
    icon?: string;
    color?: string;
  }

  let {
    value,
    label,
    format = 'number',
    prefix = undefined,
    suffix = undefined,
    change = undefined,
    changeLabel = 'vs last period',
    sparkline = undefined,
    icon = undefined,
    color = undefined
  }: Props = $props();

  const formatter = (val: string | number) => {
    if (typeof val === 'string') return val;
    switch (format) {
      case 'currency':
        return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(val);
      case 'percent':
        return new Intl.NumberFormat(undefined, { style: 'percent' }).format(val);
      case 'compact':
        return new Intl.NumberFormat(undefined, { notation: 'compact' }).format(val);
      default:
        return new Intl.NumberFormat().format(val);
    }
  };

  const changeClass = $derived(change !== undefined
    ? (change >= 0 ? 'positive' : 'negative')
    : '');
</script>

<div class="metric" style={color ? `border-color: ${color}` : ''}>
  <div class="metric-header">
    <span class="metric-label">{label}</span>
    {#if icon}
      <span class="metric-icon">{icon}</span>
    {/if}
  </div>
  <div class="metric-value">
    {#if prefix}{prefix}{/if}{formatter(value)}{#if suffix}{suffix}{/if}
  </div>
  {#if change !== undefined}
    <div class={`metric-change ${changeClass}`}>
      {change >= 0 ? '+' : ''}{(change * 100).toFixed(1)}% {changeLabel}
    </div>
  {/if}
  {#if sparkline}
    <div class="metric-sparkline">
      {#each sparkline as point, i}
        <span style={`height: ${Math.max(8, point / Math.max(...sparkline) * 30)}px`} class="spark"></span>
      {/each}
    </div>
  {/if}
</div>

<style>
  .metric {
    padding: 1rem;
    border-radius: 0.75rem;
    border: 1px solid #e5e7eb;
    background: #ffffff;
  }

  .metric-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.75rem;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .metric-value {
    font-size: 1.75rem;
    font-weight: 600;
    margin-top: 0.5rem;
  }

  .metric-change {
    margin-top: 0.25rem;
    font-size: 0.75rem;
  }

  .metric-change.positive {
    color: #16a34a;
  }

  .metric-change.negative {
    color: #dc2626;
  }

  .metric-sparkline {
    display: flex;
    gap: 2px;
    align-items: flex-end;
    margin-top: 0.5rem;
  }

  .spark {
    width: 6px;
    background: #cbd5f5;
    border-radius: 2px;
  }
</style>
