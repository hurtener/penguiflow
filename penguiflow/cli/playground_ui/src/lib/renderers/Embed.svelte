<script lang="ts">
  interface Props {
    url?: string;
    title?: string;
    width?: string;
    height?: string;
    allowFullscreen?: boolean;
  }

  let {
    url = '',
    title = undefined,
    width = '100%',
    height = '400px',
    allowFullscreen = true
  }: Props = $props();
</script>

<div class="embed-wrapper">
  <iframe
    src={url}
    title={title}
    style={`width: ${width}; height: ${height};`}
    allowfullscreen={allowFullscreen}
  ></iframe>
</div>

<style>
  .embed-wrapper {
    padding: 0.5rem;
  }

  iframe {
    border: none;
    border-radius: 0.5rem;
  }
</style>
