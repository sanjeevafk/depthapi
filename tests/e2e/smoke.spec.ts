import { test, expect } from '@playwright/test'

test('landing page loads without external calls', async ({ page }) => {
    await page.route('**/*', async (route) => {
        const url = new URL(route.request().url())
        if (url.origin !== 'http://127.0.0.1:4173') {
            await route.abort()
            return
        }
        await route.continue()
    })

    await page.goto('/')

    await expect(page.getByRole('navigation').getByText('KnowBear')).toBeVisible()
})
