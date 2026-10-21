import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss(), {
    name: 'local-webkit-script',
    generateBundle: {
      order: 'post',
      handler(_options, bundle) {
        const html = bundle['index.html'];
        if (html?.type === 'asset') {
          html.source = String(html.source).replace(/<script type="module"/g, '<script defer').replace(/ crossorigin/g, '');
        }
      },
    },
  }],
  // Do not read repository .env files into the desktop bundle.
  envDir: false,
  build: {
    target: 'safari17',
    cssTarget: 'safari17',
    // A classic local bundle avoids file:// module/CORS restrictions in WebKit.
    modulePreload: false,
    rolldownOptions: {
      output: { format: 'iife', codeSplitting: false },
    },
  },
});
