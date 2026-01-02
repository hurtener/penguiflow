<script lang="ts">
  interface Props {
    columns?: Array<Record<string, unknown>>;
    rows?: Array<Record<string, unknown>>;
    pageSize?: number;
    sortable?: boolean;
    filterable?: boolean;
    selectable?: boolean;
    exportable?: boolean;
    striped?: boolean;
    compact?: boolean;
  }

  let {
    columns = [],
    rows = [],
    pageSize = 10,
    sortable = true,
    filterable = false,
    selectable = false,
    exportable = false,
    striped = true,
    compact = false
  }: Props = $props();

  let search = $state('');
  let page = $state(1);
  let sortField = $state<string | null>(null);
  let sortDir = $state<'asc' | 'desc'>('asc');
  let selected = $state<Set<string | number>>(new Set());

  const normalizedColumns = $derived(
    columns.map((col) => ({
      field: col.field as string,
      header: (col.header as string) || (col.field as string),
      width: col.width as number | undefined,
      sortable: (col.sortable as boolean) ?? sortable,
      filterable: (col.filterable as boolean) ?? filterable,
      format: (col.format as string) ?? 'text',
      align: (col.align as string) ?? 'left'
    }))
  );

  const filteredRows = $derived(rows.filter((row) => {
    if (!filterable || !search.trim()) return true;
    const needle = search.toLowerCase();
    return Object.values(row).some((value) => String(value ?? '').toLowerCase().includes(needle));
  }));

  const sortedRows = $derived([...filteredRows].sort((a, b) => {
    if (!sortField) return 0;
    const left = a[sortField];
    const right = b[sortField];
    if (left === right) return 0;
    if (left === null || left === undefined) return 1;
    if (right === null || right === undefined) return -1;
    if (left < right) return sortDir === 'asc' ? -1 : 1;
    return sortDir === 'asc' ? 1 : -1;
  }));

  const totalPages = $derived(pageSize > 0 ? Math.max(1, Math.ceil(sortedRows.length / pageSize)) : 1);

  $effect(() => {
    if (page > totalPages) {
      page = totalPages;
    }
  });

  const pagedRows = $derived(pageSize > 0
    ? sortedRows.slice((page - 1) * pageSize, page * pageSize)
    : sortedRows);

  function toggleSort(field: string) {
    if (!sortable) return;
    if (sortField === field) {
      sortDir = sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      sortField = field;
      sortDir = 'asc';
    }
  }

  function formatValue(value: unknown, format: string) {
    if (value === null || value === undefined) return '';
    switch (format) {
      case 'currency':
        return new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD' }).format(Number(value));
      case 'percent':
        return new Intl.NumberFormat(undefined, { style: 'percent' }).format(Number(value));
      case 'number':
        return new Intl.NumberFormat().format(Number(value));
      case 'date':
        return new Date(String(value)).toLocaleDateString();
      case 'datetime':
        return new Date(String(value)).toLocaleString();
      case 'boolean':
        return value ? 'True' : 'False';
      default:
        return String(value);
    }
  }

  function toggleSelection(key: string | number) {
    if (!selectable) return;
    const next = new Set(selected);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    selected = next;
  }

  function exportCsv() {
    const headers = normalizedColumns.map((col) => col.header).join(',');
    const lines = sortedRows.map((row) =>
      normalizedColumns
        .map((col) => {
          const raw = row[col.field];
          const formatted = formatValue(raw, col.format);
          return `"${String(formatted).replace(/"/g, '""')}"`;
        })
        .join(',')
    );
    const csv = [headers, ...lines].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'data.csv';
    link.click();
    URL.revokeObjectURL(url);
  }
</script>

<div class={`datagrid ${compact ? 'compact' : ''}`}>
  <div class="datagrid-toolbar">
    {#if filterable}
      <input
        class="datagrid-search"
        placeholder="Search..."
        bind:value={search}
      />
    {/if}
    {#if exportable}
      <button class="export-btn" onclick={exportCsv}>Export CSV</button>
    {/if}
  </div>

  <div class="table-wrapper">
    <table>
      <thead>
        <tr>
          {#if selectable}
            <th class="select-col"></th>
          {/if}
          {#each normalizedColumns as col}
            <th
              class:sortable={col.sortable}
              style={col.width ? `width: ${col.width}px` : ''}
              onclick={() => col.sortable && toggleSort(col.field)}
            >
              <span>{col.header}</span>
              {#if sortField === col.field}
                <span class="sort-indicator">{sortDir === 'asc' ? '▲' : '▼'}</span>
              {/if}
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each pagedRows as row, idx}
          {@const key = (row.id as string | number) ?? idx}
          <tr class:striped={striped && idx % 2 === 1}>
            {#if selectable}
              <td class="select-col">
                <input
                  type="checkbox"
                  checked={selected.has(key)}
                  onchange={() => toggleSelection(key)}
                />
              </td>
            {/if}
            {#each normalizedColumns as col}
              <td class={`align-${col.align}`}>
                {formatValue(row[col.field], col.format)}
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  {#if pageSize > 0 && totalPages > 1}
    <div class="pagination">
      <button onclick={() => page = Math.max(1, page - 1)} disabled={page === 1}>Prev</button>
      <span>Page {page} / {totalPages}</span>
      <button onclick={() => page = Math.min(totalPages, page + 1)} disabled={page === totalPages}>Next</button>
    </div>
  {/if}
</div>

<style>
  .datagrid {
    padding: 0.75rem 1rem;
  }

  .datagrid.compact {
    padding: 0.5rem;
  }

  .datagrid-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.5rem;
    gap: 0.5rem;
  }

  .datagrid-search {
    flex: 1;
    padding: 0.4rem 0.6rem;
    border: 1px solid #e5e7eb;
    border-radius: 0.375rem;
    font-size: 0.875rem;
  }

  .export-btn {
    padding: 0.35rem 0.75rem;
    border-radius: 0.375rem;
    border: 1px solid #d1d5db;
    background: #f9fafb;
    font-size: 0.75rem;
    cursor: pointer;
  }

  .table-wrapper {
    overflow-x: auto;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.875rem;
  }

  th, td {
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid #e5e7eb;
  }

  th.sortable {
    cursor: pointer;
    user-select: none;
  }

  .sort-indicator {
    margin-left: 0.25rem;
    font-size: 0.65rem;
  }

  .striped {
    background: #f9fafb;
  }

  .align-left {
    text-align: left;
  }

  .align-right {
    text-align: right;
  }

  .align-center {
    text-align: center;
  }

  .select-col {
    width: 36px;
  }

  .pagination {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 0.75rem;
    margin-top: 0.5rem;
    font-size: 0.75rem;
  }

  .pagination button {
    border: 1px solid #d1d5db;
    background: #ffffff;
    padding: 0.25rem 0.5rem;
    border-radius: 0.25rem;
    cursor: pointer;
  }

  .pagination button:disabled {
    cursor: not-allowed;
    opacity: 0.5;
  }
</style>
