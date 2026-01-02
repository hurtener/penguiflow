<script lang="ts">
  interface Props {
    src?: string;
    alt?: string;
    caption?: string;
    width?: string;
    maxWidth?: string;
    height?: string;
    objectFit?: 'contain' | 'cover' | 'fill' | 'none';
    zoomable?: boolean;
    border?: boolean;
  }

  let {
    src = '',
    alt = '',
    caption = undefined,
    width = undefined,
    maxWidth = undefined,
    height = undefined,
    objectFit = 'contain',
    zoomable = false,
    border = false
  }: Props = $props();

  let showLightbox = $state(false);
</script>

<div class={`image-wrapper ${border ? 'bordered' : ''}`}>
  {#if zoomable}
    <button type="button" class="zoom-button" onclick={() => showLightbox = true}>
      <img
        src={src}
        alt={alt}
        style={`width: ${width || 'auto'}; max-width: ${maxWidth || '100%'}; height: ${height || 'auto'}; object-fit: ${objectFit};`}
      />
    </button>
  {:else}
    <img
      src={src}
      alt={alt}
      style={`width: ${width || 'auto'}; max-width: ${maxWidth || '100%'}; height: ${height || 'auto'}; object-fit: ${objectFit};`}
    />
  {/if}
  {#if caption}
    <div class="caption">{caption}</div>
  {/if}
</div>

{#if zoomable && showLightbox}
  <div class="lightbox" role="button" tabindex="0" onclick={() => showLightbox = false} onkeydown={(e) => e.key === 'Escape' && (showLightbox = false)}>
    <img src={src} alt={alt} />
  </div>
{/if}

<style>
  .image-wrapper {
    padding: 0.75rem;
    text-align: center;
  }

  .image-wrapper.bordered img {
    border: 1px solid #e5e7eb;
    border-radius: 0.5rem;
  }

  img {
    max-width: 100%;
  }

  .zoom-button {
    border: none;
    background: none;
    padding: 0;
    cursor: zoom-in;
    display: block;
  }

  .caption {
    margin-top: 0.5rem;
    font-size: 0.75rem;
    color: #6b7280;
  }

  .lightbox {
    position: fixed;
    inset: 0;
    background: rgba(15, 23, 42, 0.8);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 50;
  }

  .lightbox img {
    max-width: 90%;
    max-height: 90%;
  }
</style>
