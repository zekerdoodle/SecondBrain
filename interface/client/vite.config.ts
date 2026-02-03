import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      registerType: 'prompt',
      // Disable auto-injection of registerSW.js script since we use useRegisterSW hook
      injectRegister: false,
      manifest: {
        name: 'Second Brain',
        short_name: 'Second Brain',
        description: 'Your personal knowledge management system',
        theme_color: '#0d1117',
        background_color: '#0d1117',
        display: 'standalone',
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' }
        ]
      },
      injectManifest: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg,woff2,mp3}']
      }
    })
  ],
  server: {
    host: '0.0.0.0',
    allowedHosts: ['debian', 'localhost', '127.0.0.1']
  }
})