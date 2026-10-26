import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import bundledModules from './scripts/bundled-modules.mjs';

// Points every minified chunk at the third-party notices, which
// scripts/third-party-notices.mjs writes into dist after the build.
const banner = '/*! Bundled third-party code keeps its own licenses: see Third-Party-Notices.txt at the site or app root. */';
// Emit fonts as files, never data: URIs, so the notices check can attribute them.
const keepFontFiles = (file) => (/\.(?:woff2?|ttf|otf|eot)$/i.test(file) ? false : undefined);

export default defineConfig({
  // bundledModules records which packages end up in the bundle.
  plugins: [svelte(), bundledModules()],
  // From Vite 7.2 the minifier drops /*! comments unless this is set.
  esbuild: { legalComments: 'inline' },
  server: {
    port: 4173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true
      },
      '/healthz': {
        target: 'http://localhost:8080',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    assetsInlineLimit: keepFontFiles,
    rollupOptions: { output: { banner } }
  }
});
