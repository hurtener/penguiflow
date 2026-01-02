<script lang="ts">
  interface Props {
    src?: string;
    poster?: string;
    autoplay?: boolean;
    controls?: boolean;
    loop?: boolean;
    muted?: boolean;
    width?: string;
    height?: string;
  }

  let {
    src = '',
    poster = undefined,
    autoplay = false,
    controls = true,
    loop = false,
    muted = false,
    width = undefined,
    height = undefined
  }: Props = $props();
</script>

<div class="video-wrapper">
  <video
    src={src}
    {poster}
    {autoplay}
    {controls}
    {loop}
    {muted}
    style={`width: ${width || '100%'}; height: ${height || 'auto'};`}
  ></video>
</div>

<style>
  .video-wrapper {
    padding: 0.5rem;
  }

  video {
    border-radius: 0.5rem;
    background: #000000;
  }
</style>
