<script lang="ts">
  import type { PauseInfo } from '$lib/types';

  interface Props {
    pause: PauseInfo;
  }

  let { pause }: Props = $props();

  const authUrl = $derived(
    (pause.payload?.auth_url as string) || (pause.payload?.url as string) || ''
  );
  const provider = $derived((pause.payload?.provider as string) || '');
</script>

<div class="pause-card">
  <div class="pause-title">Action required</div>
  {#if authUrl}
    <a
      class="pause-link"
      href={authUrl}
      target="_blank"
      rel="noreferrer"
    >
      Open authorization link
    </a>
  {/if}
  {#if pause.resume_token}
    <div class="pause-token">Resume token: {pause.resume_token}</div>
  {/if}
  <div class="pause-meta">
    {pause.reason ? `reason: ${pause.reason}` : 'paused'}
    {#if provider}
      - provider: {provider}
    {/if}
  </div>
</div>

<style>
  .pause-card {
    margin-top: 10px;
    padding: 10px;
    background: var(--color-pill-ghost-bg, #f4f0ea);
    border-radius: 10px;
    border: 1px solid var(--color-pill-ghost-border, #ebe5dd);
  }

  .pause-title {
    font-weight: 700;
    font-size: 12px;
    color: var(--color-text, #1f1f1f);
    margin-bottom: 6px;
  }

  .pause-link {
    display: inline-block;
    font-size: 12px;
    color: var(--color-primary, #106c67);
    text-decoration: underline;
    margin-bottom: 4px;
  }

  .pause-token {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-muted, #6b665f);
    margin-bottom: 4px;
  }

  .pause-meta {
    font-size: 10px;
    color: var(--color-muted, #7a756d);
  }
</style>
