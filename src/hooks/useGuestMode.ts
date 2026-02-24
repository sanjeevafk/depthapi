import { useState, useEffect } from 'react';

const GUEST_USAGE_KEY = 'guest_usage_count';
const GUEST_LIMIT = 5;

export const useGuestMode = () => {
    const [usageCount, setUsageCount] = useState<number>(0);
    const [showUpgradeModal, setShowUpgradeModal] = useState(false);

    // Initialize from localStorage
    useEffect(() => {
        const stored = localStorage.getItem(GUEST_USAGE_KEY);
        if (stored) {
            setUsageCount(parseInt(stored, 10));
        }
    }, []);

    const incrementUsage = () => {
        const newCount = usageCount + 1;
        setUsageCount(newCount);
        localStorage.setItem(GUEST_USAGE_KEY, newCount.toString());

        if (newCount > GUEST_LIMIT) {
            setShowUpgradeModal(true);
        }
    };

    const resetUsage = () => {
        setUsageCount(0);
        localStorage.setItem(GUEST_USAGE_KEY, '0');
        setShowUpgradeModal(false);
    };

    const checkLimit = () => {
        if (usageCount >= GUEST_LIMIT) {
            setShowUpgradeModal(true);
            return true;
        }
        return false;
    };

    return {
        isGuest: true, // This hook primarily manages guest logic; actual auth status comes from AuthContext
        usageCount,
        limit: GUEST_LIMIT,
        incrementUsage,
        resetUsage,
        checkLimit,
        showUpgradeModal,
        setShowUpgradeModal
    };
};
