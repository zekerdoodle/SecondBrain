import { defineConfig } from 'vite';
import { viteSingleFile } from 'vite-plugin-singlefile';

export default defineConfig({
  root: 'src',
  plugins: [viteSingleFile()],
  build: {
    outDir: '..',
    emptyOutDir: false,
    // Inline all assets into the HTML
    assetsInlineLimit: Infinity,
  },
});
