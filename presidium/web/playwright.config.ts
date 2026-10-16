import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: process.env.PRESIDIUM_AUTH_TESTS ? 'auth-lifecycle.spec.ts' : ['original-frontend.spec.ts','auth-lifecycle.spec.ts'],
  timeout: 90_000,
  expect: {
    timeout: 20_000,
  },
  reporter: [['list'], ['html', { open: 'never' }]],
  outputDir: process.env.PRESIDIUM_TEST_OUTPUT_DIR ?? '../.cache/original-frontend-tests',
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
  use: {
    actionTimeout: 30_000,
    baseURL: 'http://127.0.0.1:5173',
    trace: 'off',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    headless: true,
  },
})
