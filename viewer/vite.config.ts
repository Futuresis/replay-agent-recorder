import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'replay/xyflow_assets',
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: 'viewer/src/main.tsx',
      output: {
        entryFileNames: 'xyflow-viewer.js',
        assetFileNames: 'xyflow-viewer.[ext]',
      },
    },
  },
});
