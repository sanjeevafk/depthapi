import { defineConfig } from '@playwright/test'

export default defineConfig({
    testDir: 'tests/e2e',
    retries: 0,
    timeout: 30_000,
    expect: {
        timeout: 5_000,
    },
    use: {
        baseURL: 'http://127.0.0.1:4173',
        trace: 'retain-on-failure',
    },
    webServer: {
        command: 'cross-env VITE_SUPABASE_URL= VITE_SUPABASE_ANON_KEY= VITE_SENTRY_ENABLED=false npm run dev -- --host 127.0.0.1 --port 4173',
        url: 'http://127.0.0.1:4173',
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
    },
})
