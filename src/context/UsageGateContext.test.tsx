import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { UsageGateProvider, useUsageGateContext } from './UsageGateContext'

vi.mock('./AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'user-1' },
    profile: { is_pro: false },
  }),
}))

vi.mock('../hooks/useGuestMode', () => ({
  useGuestMode: () => ({
    checkLimit: () => false,
    incrementUsage: () => undefined,
  }),
}))

describe('UsageGateContext', () => {
  it('does not elevate to Pro from localStorage', () => {
    window.localStorage.setItem('knowbear_pro_status', 'true')

    const { result } = renderHook(() => useUsageGateContext(), {
      wrapper: ({ children }) => <UsageGateProvider>{children}</UsageGateProvider>,
    })

    expect(result.current.isPro).toBe(false)
  })
})
