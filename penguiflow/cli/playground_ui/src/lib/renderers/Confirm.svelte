<script lang="ts">
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

  function handleConfirm() {
    onResult?.({ confirmed: true });
  }

  function handleCancel() {
    onResult?.({ confirmed: false });
  }
</script>

<div class={`confirm ${variant}`}>
  <div class="confirm-header">
    <h3>{title}</h3>
  </div>
  <div class="confirm-body">
    <Markdown content={message} allowHtml={false} padded={false} />
    {#if details}
      <details>
        <summary>Details</summary>
        <Markdown content={details} allowHtml={false} padded={false} />
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
    border-radius: var(--radius-2xl, 18px);
    border: 1px solid var(--color-border, #f0ebe4);
    background: var(--color-card-bg, #fcfaf7);
    box-shadow: var(--shadow-card, 0 12px 32px rgba(17, 17, 17, 0.06));
  }

  .confirm.warning {
    border-color: var(--color-border, #f0ebe4);
  }

  .confirm.danger {
    border-color: var(--color-error-border, #f5dddd);
  }

  .confirm.success {
    border-color: var(--color-border, #f0ebe4);
  }

  h3 {
    margin: 0;
    font-size: 1.1rem;
    color: var(--color-text, #1f1f1f);
  }

  .confirm-body {
    margin-top: 0.75rem;
    color: var(--color-text-secondary, #3c3a36);
  }

  .confirm-actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.75rem;
    margin-top: 1rem;
  }

  .btn-cancel {
    border: 1px solid var(--color-border, #f0ebe4);
    background: var(--color-btn-ghost-bg, #f2eee8);
    padding: 0.4rem 0.75rem;
    border-radius: var(--radius-md, 10px);
    cursor: pointer;
  }

  .btn-confirm {
    border: none;
    background: var(--color-btn-primary-gradient, linear-gradient(135deg, #31a6a0, #1a7c75));
    color: #ffffff;
    padding: 0.4rem 0.75rem;
    border-radius: var(--radius-md, 10px);
    cursor: pointer;
    box-shadow: 0 0 0 0 var(--color-btn-primary-shadow, rgba(49, 166, 160, 0.3));
  }
</style>
