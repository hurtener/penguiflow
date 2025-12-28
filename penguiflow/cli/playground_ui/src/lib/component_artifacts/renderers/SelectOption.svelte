<script lang="ts">
  import { createEventDispatcher } from 'svelte';

  interface OptionItem {
    value: string;
    label: string;
    description?: string;
    icon?: string;
    disabled?: boolean;
    metadata?: Record<string, unknown>;
  }

  interface Props {
    title?: string;
    description?: string;
    options?: OptionItem[];
    multiple?: boolean;
    minSelections?: number;
    maxSelections?: number;
    layout?: 'list' | 'grid' | 'cards';
    searchable?: boolean;
    onResult?: (result: unknown) => void;
  }

  let {
    title = 'Choose an option',
    description = '',
    options = [],
    multiple = false,
    minSelections = 1,
    maxSelections = undefined,
    layout = 'list',
    searchable = false,
    onResult = undefined
  }: Props = $props();

  const dispatch = createEventDispatcher<{ submit: string[]; cancel: void }>();

  let query = $state('');
  let selected = $state<Set<string>>(new Set());
  let error = $state<string | null>(null);

  const filtered = $derived(options.filter((opt) => {
    if (!searchable || !query.trim()) return true;
    const needle = query.toLowerCase();
    return opt.label.toLowerCase().includes(needle) || opt.value.toLowerCase().includes(needle);
  }));

  function toggle(value: string) {
    if (!multiple) {
      selected = new Set([value]);
      error = null;
      return;
    }
    const next = new Set(selected);
    if (next.has(value)) {
      next.delete(value);
    } else {
      if (maxSelections && next.size >= maxSelections) return;
      next.add(value);
    }
    selected = next;
    error = null;
  }

  function handleSubmit() {
    const values = Array.from(selected);
    if (values.length < minSelections) {
      error = `Select at least ${minSelections} option${minSelections > 1 ? 's' : ''}.`;
      return;
    }
    error = null;
    dispatch('submit', values);
    onResult?.({ selection: multiple ? values : values[0] });
  }

  function handleCancel() {
    dispatch('cancel');
    onResult?.({ selection: null, cancelled: true });
  }
</script>

<div class="select-option">
  <div class="header">
    <h3>{title}</h3>
    {#if description}
      <p>{description}</p>
    {/if}
  </div>

  {#if searchable}
    <input class="search" placeholder="Search..." bind:value={query} />
  {/if}

  <div class={`options ${layout}`}>
    {#each filtered as opt}
      <button
        class={`option ${selected.has(opt.value) ? 'active' : ''}`}
        disabled={opt.disabled}
        onclick={() => toggle(opt.value)}
      >
        <div class="option-title">
          {#if opt.icon}
            <span class="icon">{opt.icon}</span>
          {/if}
          <span>{opt.label}</span>
        </div>
        {#if opt.description}
          <div class="option-desc">{opt.description}</div>
        {/if}
      </button>
    {/each}
  </div>

  <div class="actions">
    {#if error}
      <div class="error">{error}</div>
    {/if}
    <button class="btn-cancel" onclick={handleCancel}>Cancel</button>
    <button class="btn-submit" onclick={handleSubmit}>Submit</button>
  </div>
</div>

<style>
  .select-option {
    padding: 1.25rem;
  }

  h3 {
    margin: 0;
  }

  .header p {
    margin: 0.25rem 0 0;
    color: #6b7280;
  }

  .search {
    margin-top: 0.75rem;
    width: 100%;
    padding: 0.4rem 0.6rem;
    border-radius: 0.375rem;
    border: 1px solid #d1d5db;
  }

  .options {
    margin-top: 0.75rem;
    display: grid;
    gap: 0.75rem;
  }

  .options.grid {
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  }

  .options.cards {
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }

  .option {
    padding: 0.75rem;
    text-align: left;
    border: 1px solid #e5e7eb;
    border-radius: 0.5rem;
    background: #ffffff;
    cursor: pointer;
  }

  .option.active {
    border-color: #2563eb;
    box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
  }

  .option-desc {
    font-size: 0.75rem;
    color: #64748b;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.75rem;
    margin-top: 1rem;
    align-items: center;
  }

  .error {
    margin-right: auto;
    font-size: 0.75rem;
    color: #dc2626;
  }

  .btn-submit {
    background: #2563eb;
    color: #ffffff;
    border: none;
    padding: 0.4rem 0.75rem;
    border-radius: 0.375rem;
  }

  .btn-cancel {
    background: #ffffff;
    border: 1px solid #d1d5db;
    padding: 0.4rem 0.75rem;
    border-radius: 0.375rem;
  }
</style>
