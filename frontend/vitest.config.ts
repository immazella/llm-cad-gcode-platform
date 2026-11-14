import { defineConfig } from 'vitest/config';

// Keep the test runner independent from the production Vite plugin version.
export default defineConfig({
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'] },
});
