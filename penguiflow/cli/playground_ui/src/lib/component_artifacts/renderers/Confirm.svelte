<script lang="ts">
  import { createEventDispatcher } from 'svelte';
  import Markdown from './Markdown.svelte';

  interface Props {
    title?: string;
    message?: string;
    confirmLabel?: string;
    cancelLabel?: string;
    variant?: 'info' | 'warning' | 'danger' | 'success';
    details?: string;
    onResult?: (result: unknown) => void;
  }

  let {
    title = 'Confirm',
    message = '',
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    variant = 'info',
    details = undefined,
    onResult = undefined
  }: Props = $props();

  const dispatch = createEventDispatcher<{ confirm: void; cancel: void }>();

  function handleConfirm() {
    dispatch('confirm');
    onResult?.({ confirmed: true });
  }

  function handleCancel() {
    dispatch('cancel');
    onResult?.({ confirmed: false });
  }
</script>

<div class={`confirm ${variant}`}>
  <div class="confirm-header">
    <h3>{title}</h3>
  </div>
  <div class="confirm-body">
    <Markdown content={message} allowHtml={false} />
    {#if details}
      <details>
        <summary>Details</summary>
        <Markdown content={details} allowHtml={false} />
      </details>
    {/if}
  </div>
  <div class="confirm-actions">
    <button class="btn-cancel" onclick={handleCancel}>{cancelLabel}</button>
    <button class="btn-confirm" onclick={handleConfirm}>{confirmLabel}</button>
  </div>
</div>

<style>
  .confirm {
    padding: 1.25rem;
    border-radius: 0.75rem;
    border: 1px solid #e5e7eb;
    background: #ffffff;
  }

  .confirm.warning {
    border-color: #facc15;
  }

  .confirm.danger {
    border-color: #f87171;
  }

  .confirm.success {
    border-color: #4ade80;
  }

  h3 {
    margin: 0;
    font-size: 1.1rem;
  }

  .confirm-body {
    margin-top: 0.75rem;
  }

  .confirm-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.75rem;
    margin-top: 1rem;
  }

  .btn-cancel {
    border: 1px solid #d1d5db;
    background: #ffffff;
    padding: 0.4rem 0.75rem;
    border-radius: 0.375rem;
    cursor: pointer;
  }

  .btn-confirm {
    border: none;
    background: #2563eb;
    color: #ffffff;
    padding: 0.4rem 0.75rem;
    border-radius: 0.375rem;
    cursor: pointer;
  }
</style>
