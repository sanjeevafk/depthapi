/**
 * Dodo Payments integration for KnowBear Pro upgrades
 */

import { supabase } from './supabase';

const API_BASE_URL = import.meta.env.VITE_API_URL || '';

interface CheckoutResponse {
    checkout_url: string;
    session_id: string;
}

/**
 * Create a checkout session and redirect user to Dodo Payments
 */
export const createCheckoutSession = async (
    onError?: (error: any) => void
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
            const error = await response.json();
            throw new Error(error.detail || 'Failed to create checkout session');
        }

        const data: CheckoutResponse = await response.json();

        // Redirect to Dodo Payments checkout
        window.location.href = data.checkout_url;

    } catch (error) {
        console.error('Checkout error:', error);
        if (onError) {
            onError(error);
        } else {
            throw error;
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

        const data = await response.json();
        return data.is_pro === true;

    } catch (error) {
        console.error('Payment verification error:', error);
        return false;
    }
};

/**
 * Poll payment status until user is upgraded (max 30 seconds)
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
