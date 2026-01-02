<script lang="ts">
  import { getAGUIContext } from '$lib/agui';

  const { state, status, agentState, activeSteps } = getAGUIContext();

  export let expanded = false;
</script>

<details class="agui-debugger" bind:open={expanded}>
  <summary>
    AG-UI Debug
    <span class="status" class:running={$status === 'running'} class:error={$status === 'error'}>
      {$status}
    </span>
  </summary>

  <div class="content">
    <section>
      <h4>Thread / Run</h4>
      <code>{$state.threadId ?? '—'} / {$state.runId ?? '—'}</code>
    </section>

    {#if $activeSteps.length > 0}
      <section>
        <h4>Active Steps</h4>
        <ul>
          {#each $activeSteps as s}
            <li>{s.name}</li>
          {/each}
        </ul>
      </section>
    {/if}

    <section>
      <h4>Agent State</h4>
      <pre>{JSON.stringify($agentState, null, 2)}</pre>
    </section>

    {#if $state.error}
      <section class="error">
        <h4>Error</h4>
        <pre>{JSON.stringify($state.error, null, 2)}</pre>
      </section>
    {/if}
  </div>
</details>

<style>
  .agui-debugger {
    font-family: var(--font-mono);
    font-size: 11px;
    background: #1f1f1f;
    color: #f4f0ea;
    padding: 10px;
    border-radius: 10px;
  }

  summary {
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .status {
    padding: 2px 6px;
    border-radius: 6px;
    font-size: 10px;
    background: #3a3632;
    text-transform: uppercase;
  }

  .status.running {
    background: var(--color-primary, #31a6a0);
    color: #ffffff;
  }

  .status.error {
    background: var(--color-error-accent, #b24c4c);
    color: #ffffff;
  }

  .content {
    margin-top: 8px;
    padding-top: 8px;
    border-top: 1px solid #3a3632;
  }

  section {
    margin-bottom: 8px;
  }

  h4 {
    margin: 0 0 4px;
    font-size: 10px;
    text-transform: uppercase;
    opacity: 0.7;
  }

  pre,
  code {
    margin: 0;
    font-size: 10px;
  }

  ul {
    margin: 0;
    padding-left: 16px;
  }

  .error {
    color: #f5b4b4;
  }
</style>
