import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [
    svelte({
      hot: !process.env.VITEST
    })
  ],
  test: {
    include: ['tests/unit/**/*.test.ts'],
    globals: true,
    environment: 'jsdom',
    setupFiles: ['tests/setup.ts'],
    alias: {
      $lib: new URL('./src/lib', import.meta.url).pathname
    },
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['src/lib/**/*.ts', 'src/lib/**/*.svelte'],
      exclude: ['src/lib/types/**']
    }
  },
  resolve: {
    alias: {
      $lib: new URL('./src/lib', import.meta.url).pathname
    },
    conditions: ['browser']
  }
});
