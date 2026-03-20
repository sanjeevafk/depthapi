/**
 * Dodo Payments integration for KnowBear Pro upgrades
 */

import { supabase } from "./supabase";
import { captureFrontendError, trackTelemetry } from "./monitoring";
import { getTracePropagationHeaders } from "./monitoring";

const API_BASE_URL = import.meta.env.VITE_API_URL || "";

interface CheckoutResponse {
  checkout_url: string;
  session_id: string;
}

const normalizeError = (error: unknown): Error => {
  return error instanceof Error ? error : new Error("Unknown error");
};

/**
 * Create a checkout session and redirect user to Dodo Payments
 */
export const createCheckoutSession = async (
  onError?: (error: Error) => void,
): Promise<void> => {
  try {
    trackTelemetry("payment_checkout_start");

    // Get current user session
    const {
      data: { session },
    } = await supabase.auth.getSession();

    if (!session) {
      throw new Error("User not authenticated");
    }

    // Get current URL for success/cancel redirects
    const baseUrl = window.location.origin;
    const successUrl = `${baseUrl}/success`;
    const cancelUrl = `${baseUrl}/app`;

    // Call backend to create checkout session
    const response = await fetch(
      `${API_BASE_URL}/api/payments/create-checkout`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.access_token}`,
          ...getTracePropagationHeaders(),
        },
        body: JSON.stringify({
          plan: "pro",
          success_url: successUrl,
          cancel_url: cancelUrl,
        }),
      },
    );

    if (!response.ok) {
      let message = "Failed to create checkout session";
      try {
        const errorPayload = (await response.json()) as { detail?: string };
        if (errorPayload.detail) {
          message = errorPayload.detail;
        }
      } catch {
        // Response body is not valid JSON, use default message
      }
      throw new Error(message);
    }

    const data: CheckoutResponse = await response.json();

    trackTelemetry("payment_checkout_session_created", {
      session_id: data.session_id,
    });

    // Redirect to Dodo Payments checkout
    trackTelemetry("payment_checkout_redirect");
    window.location.href = data.checkout_url;
  } catch (error) {
    const normalized = normalizeError(error);
    console.error("Checkout error:", normalized);
    trackTelemetry("payment_checkout_error", {
      error_type: normalized.name,
    });
    captureFrontendError(normalized, { source: "payments.create_checkout" });
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
    trackTelemetry("payment_verify_status_start");
    const {
      data: { session },
    } = await supabase.auth.getSession();

    if (!session) {
      trackTelemetry("payment_verify_status_result", { status: "no_session" });
      return false;
    }

    const response = await fetch(`${API_BASE_URL}/api/payments/verify-status`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${session.access_token}`,
        ...getTracePropagationHeaders(),
      },
    });

    if (!response.ok) {
      trackTelemetry("payment_verify_status_result", {
        status: "request_failed",
        http_status: response.status,
      });
      return false;
    }

    const data = (await response.json()) as { is_pro?: boolean };
    trackTelemetry("payment_verify_status_result", {
      status: data.is_pro === true ? "pro" : "free",
    });
    return data.is_pro === true;
  } catch (error) {
    const normalized = normalizeError(error);
    console.error("Payment verification error:", normalized);
    trackTelemetry("payment_verify_status_result", {
      status: "error",
      error_type: normalized.name,
    });
    captureFrontendError(normalized, { source: "payments.verify_status" });
    return false;
  }
};

/**
 * Poll payment status until user is upgraded (max ~30 seconds by default)
 */
export const waitForPaymentConfirmation = async (
  maxAttempts: number = 15,
  intervalMs: number = 2000,
): Promise<boolean> => {
  for (let i = 0; i < maxAttempts; i++) {
    const isPro = await verifyPaymentStatus();

    if (isPro) {
      return true;
    }

    // Wait before next attempt
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }

  return false;
};
