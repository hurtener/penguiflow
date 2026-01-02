import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'
import { visualizer } from 'rollup-plugin-visualizer'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    svelte(),
    process.env.VITE_ANALYZE
      ? visualizer({ filename: 'dist/bundle-stats.html', gzipSize: true, brotliSize: true })
      : null
  ].filter(Boolean),
  resolve: {
    alias: {
      $lib: path.resolve('./src/lib')
    }
  }
})
