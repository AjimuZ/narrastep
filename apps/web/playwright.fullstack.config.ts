import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/fullstack',
  fullyParallel: false,
  forbidOnly: Boolean(process.env['CI']),
  retries: process.env['CI'] ? 1 : 0,
  workers: 1,
  timeout: 300_000,
  expect: {
    timeout: 20_000,
  },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env['NARRASTEP_E2E_BASE_URL'] ?? 'http://127.0.0.1:4200',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium-fullstack',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
