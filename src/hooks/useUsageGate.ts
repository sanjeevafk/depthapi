import {
    useUsageGateContext,
    type ActionType,
    type UsageGateContextType,
} from '../context/UsageGateContext';

export type { ActionType };

export function useUsageGate(): UsageGateContextType {
    return useUsageGateContext();
}
