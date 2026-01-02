<script lang="ts">
  interface Props {
    html?: string;
    css?: string;
    js?: string;
    height?: string;
    sandbox?: string;
  }

  let {
    html = '',
    css = undefined,
    js = undefined,
    height = '400px',
    sandbox = 'allow-scripts'
  }: Props = $props();

  const srcdoc = $derived(`<!DOCTYPE html><html><head><style>${css ?? ''}</style></head><body>${html ?? ''}<script>${js ?? ''}<\/script></body></html>`);
</script>

<iframe
  class="html-frame"
  style={`height: ${height};`}
  {sandbox}
  srcdoc={srcdoc}
  title="HTML preview"
></iframe>

<style>
  .html-frame {
    width: 100%;
    border: none;
    background: #ffffff;
  }
</style>
