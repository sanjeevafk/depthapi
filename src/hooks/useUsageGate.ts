import { useUsageGateContext, ActionType } from '../context/UsageGateContext';

export type { ActionType };

export const useUsageGate = () => {
    return useUsageGateContext();
};
