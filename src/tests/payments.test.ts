import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { mockGetSession } = vi.hoisted(() => ({
    mockGetSession: vi.fn(),
}))

vi.mock('../lib/supabase', () => ({
    supabase: {
        auth: {
            getSession: mockGetSession,
        },
    },
}))

import { createCheckoutSession, waitForPaymentConfirmation } from '../lib/payments'

describe('createCheckoutSession', () => {
    beforeEach(() => {
        vi.clearAllMocks()
        mockGetSession.mockResolvedValue({
            data: {
                session: {
                    access_token: 'token-123',
                },
            },
        })
    })

    afterEach(() => {
        vi.unstubAllGlobals()
    })

    it('surfaces backend checkout errors through onError callback', async () => {
        vi.stubGlobal(
            'fetch',
            vi.fn().mockResolvedValue({
                ok: false,
                json: vi.fn().mockResolvedValue({ detail: 'Checkout unavailable' }),
            }),
        )

        const onError = vi.fn()
        await createCheckoutSession(onError)

        expect(onError).toHaveBeenCalledTimes(1)
        expect(onError.mock.calls[0][0].message).toBe('Checkout unavailable')
    })

    it('rejects when no active session exists', async () => {
        mockGetSession.mockResolvedValueOnce({ data: { session: null } })
        const onError = vi.fn()

        await createCheckoutSession(onError)

        expect(onError).toHaveBeenCalledTimes(1)
        expect(onError.mock.calls[0][0].message).toMatch(/not authenticated/i)
    })

    it('confirms upgrade within five polling attempts', async () => {
        vi.stubGlobal(
            'fetch',
            vi
                .fn()
                .mockResolvedValueOnce({ ok: true, json: vi.fn().mockResolvedValue({ is_pro: false }) })
                .mockResolvedValueOnce({ ok: true, json: vi.fn().mockResolvedValue({ is_pro: false }) })
                .mockResolvedValueOnce({ ok: true, json: vi.fn().mockResolvedValue({ is_pro: true }) }),
        )

        const isConfirmed = await waitForPaymentConfirmation(5, 1)

        expect(isConfirmed).toBe(true)
        expect(fetch).toHaveBeenCalledTimes(3)
    })

    it('returns false when not upgraded after five attempts', async () => {
        vi.stubGlobal(
            'fetch',
            vi.fn().mockResolvedValue({ ok: true, json: vi.fn().mockResolvedValue({ is_pro: false }) }),
        )

        const isConfirmed = await waitForPaymentConfirmation(5, 1)

        expect(isConfirmed).toBe(false)
        expect(fetch).toHaveBeenCalledTimes(5)
    })
})
