import { test, expect, type Page } from '@playwright/test'

const allowOnlyLocalhost = async (page: Page) => {
    await page.route('**/*', async (route) => {
        const url = new URL(route.request().url())
        if (url.origin !== 'http://127.0.0.1:4173') {
            await route.abort()
            return
        }
        await route.continue()
    })
}

test('landing page loads without external calls', async ({ page }) => {
    await allowOnlyLocalhost(page)

    await page.goto('/')

    await expect(page.getByRole('navigation').getByText('KnowBear')).toBeVisible()
})

test('chat route renders auth gate with missing Supabase env', async ({ page }) => {
    await allowOnlyLocalhost(page)

    await page.goto('/app')

    await expect(page.getByText('Welcome back')).toBeVisible()
    await expect(page.getByRole('button', { name: /continue with google/i })).toBeVisible()
})

test('features page exposes all learning modes', async ({ page }) => {
    await allowOnlyLocalhost(page)

    await page.goto('/features')

    await expect(page.getByText(/Learn, Socratic, or Technical modes/i)).toBeVisible()
    await expect(page.getByText(/Switch between ELI5 and technical depth/i)).toBeVisible()
})
