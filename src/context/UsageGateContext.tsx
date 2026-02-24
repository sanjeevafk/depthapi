import { createContext, useContext, useState, ReactNode } from 'react';
import { useAuth } from './AuthContext';
import { useGuestMode } from '../hooks/useGuestMode';

export type ActionType = 'search' | 'export_data' | 'premium_mode';

export interface UsageGateContextType {
    checkAction: (action: ActionType, mode?: string) => { allowed: boolean; downgraded?: boolean };
    recordAction: (action: ActionType, mode?: string) => void;
    showPremiumModal: boolean;
    setShowPremiumModal: (show: boolean) => void;
    paywallContext: { mode?: string, action?: ActionType } | null;
    upgradeToPro: () => void;
    isPro: boolean;
    deepDiveUsageCount: number;
    deepDiveLimit: number;
}

const UsageGateContext = createContext<UsageGateContextType | undefined>(undefined);

export function UsageGateProvider({ children }: { children: ReactNode }) {
    const { user, profile } = useAuth();
    const guestMode = useGuestMode();
    const [showPremiumModal, setShowPremiumModal] = useState(false);
    const [paywallContext, setPaywallContext] = useState<{ mode?: string, action?: ActionType } | null>(null);

    // Use profile status directly as source of truth, fallback to localStorage for instant load
    const isPro = profile?.is_pro === true || localStorage.getItem('knowbear_pro_status') === 'true';

    const checkAction = (
        action: ActionType,
        mode: string = 'fast'
    ): { allowed: boolean; downgraded?: boolean } => {
        // PRO users bypass all limits
        if (isPro) {
            return { allowed: true };
        }

        // HARD GATED Features
        if (action === 'premium_mode' || action === 'export_data') {
            setPaywallContext({ mode, action });
            setShowPremiumModal(true);
            return { allowed: false };
        }

        // SEARCH logic
        if (action === 'search') {
            // Fast mode is indefinite (unlimited) for everyone
            if (mode === 'fast') {
                return { allowed: true };
            }

            // Guest check (Global Limit) - only applies to non-fast modes if any are accessible to guests
            if (!user) {
                if (guestMode.checkLimit()) {
                    setShowPremiumModal(true);
                    return { allowed: false };
                }
            }
        }

        return { allowed: true };
    };

    const recordAction = (action: ActionType, mode: string = 'fast') => {
        if (action === 'search') {
            // Only increment strict usage limit for non-fast searches
            if (!user && mode !== 'fast') {
                guestMode.incrementUsage();
            }
        }
    };

    const upgradeToPro = () => {
        localStorage.setItem('knowbear_pro_status', 'true');
        window.location.reload();
    };

    const setShowPremiumModalWithReset = (show: boolean) => {
        if (!show) setPaywallContext(null);
        setShowPremiumModal(show);
    };

    return (
        <UsageGateContext.Provider value={{
            checkAction,
            recordAction,
            showPremiumModal,
            setShowPremiumModal: setShowPremiumModalWithReset,
            paywallContext,
            upgradeToPro,
            isPro,
            deepDiveUsageCount: 0,
            deepDiveLimit: 0
        }}>
            {children}
        </UsageGateContext.Provider>
    );
}

export const useUsageGateContext = () => {
    const context = useContext(UsageGateContext);
    if (!context) {
        throw new Error('useUsageGateContext must be used within a UsageGateProvider');
    }
    return context;
};
