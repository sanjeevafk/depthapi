/**
 * Dodo Payments integration for KnowBear Pro upgrades
 */

import { supabase } from './supabase';

const API_BASE_URL = import.meta.env.VITE_API_URL || '';

interface CheckoutResponse {
    checkout_url: string;
    session_id: string;
}

const normalizeError = (error: unknown): Error => {
    return error instanceof Error ? error : new Error('Unknown error')
}

/**
 * Create a checkout session and redirect user to Dodo Payments
 */
export const createCheckoutSession = async (
    onError?: (error: Error) => void
): Promise<void> => {
    try {
        // Get current user session
        const { data: { session } } = await supabase.auth.getSession();

        if (!session) {
            throw new Error('User not authenticated');
        }

        // Get current URL for success/cancel redirects
        const baseUrl = window.location.origin;
        const successUrl = `${baseUrl}/success`;
        const cancelUrl = `${baseUrl}/app`;

        // Call backend to create checkout session
        const response = await fetch(`${API_BASE_URL}/api/payments/create-checkout`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${session.access_token}`
            },
            body: JSON.stringify({
                plan: 'pro',
                success_url: successUrl,
                cancel_url: cancelUrl
            })
        });

        if (!response.ok) {
            let message = 'Failed to create checkout session';
            try {
                const errorPayload = await response.json() as { detail?: string };
                if (errorPayload.detail) {
                    message = errorPayload.detail;
                }
            } catch {
                // Response body is not valid JSON, use default message
            }
            throw new Error(message);
        }

        const data: CheckoutResponse = await response.json();

        // Redirect to Dodo Payments checkout
        window.location.href = data.checkout_url;

    } catch (error) {
        const normalized = normalizeError(error)
        console.error('Checkout error:', normalized);
        if (onError) {
            onError(normalized);
        } else {
            throw normalized;
        }
    }
};

/**
 * Verify payment status after successful payment
 */
export const verifyPaymentStatus = async (): Promise<boolean> => {
    try {
        const { data: { session } } = await supabase.auth.getSession();

        if (!session) {
            return false;
        }

        const response = await fetch(`${API_BASE_URL}/api/payments/verify-status`, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${session.access_token}`
            }
        });

        if (!response.ok) {
            return false;
        }

        const data = await response.json() as { is_pro?: boolean };
        return data.is_pro === true;

    } catch (error) {
        console.error('Payment verification error:', normalizeError(error));
        return false;
    }
};

/**
 * Poll payment status until user is upgraded (max ~30 seconds by default)
 */
export const waitForPaymentConfirmation = async (
    maxAttempts: number = 15,
    intervalMs: number = 2000
): Promise<boolean> => {
    for (let i = 0; i < maxAttempts; i++) {
        const isPro = await verifyPaymentStatus();

        if (isPro) {
            return true;
        }

        // Wait before next attempt
        await new Promise(resolve => setTimeout(resolve, intervalMs));
    }

    return false;
};
