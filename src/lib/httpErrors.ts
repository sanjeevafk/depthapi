export type ApiErrorDetail = {
  type?: string;
  retry_allowed?: boolean;
  limit?: number;
  consumed?: number;
  scope?: string;
};

export type ApiError = Error & {
  status?: number;
  detail?: ApiErrorDetail;
};

const messageFromDetail = (detail: ApiErrorDetail): string => {
  if (detail.type === "quota_exceeded") {
    return "Rate limited for today. Please try again after your quota resets.";
  }
  if (detail.type === "rate_limit_exceeded") {
    return "Rate limited. Please wait a moment before retrying.";
  }
  if (detail.type === "invalid_api_key") {
    return "Authentication failed. Please verify provider credentials.";
  }
  if (detail.type === "service_degraded") {
    return "Model service is temporarily unavailable. Please try again shortly.";
  }
  if (detail.type === "bad_request") {
    return "Request could not be processed. Please review the prompt and retry.";
  }
  if (detail.type === "llm_error") {
    return "API error from the model provider. Please retry.";
  }
  return "";
};

export const buildApiError = async (response: Response): Promise<ApiError> => {
  let message = "";
  let detail: ApiErrorDetail | undefined;

  try {
    const payload = (await response.json()) as Record<string, unknown>;
    const payloadDetail = payload.detail;
    const payloadError = payload.error;

    if (typeof payloadDetail === "string" && payloadDetail.trim()) {
      message = payloadDetail.trim();
    } else if (typeof payloadError === "string" && payloadError.trim()) {
      message = payloadError.trim();
    } else if (payloadDetail && typeof payloadDetail === "object") {
      detail = payloadDetail as ApiErrorDetail;
      message = messageFromDetail(detail);
    }
  } catch {
    // ignore non-json error payloads
  }

  const err = new Error(
    message || `Request failed (${response.status}). Please try again.`,
  ) as ApiError;
  err.status = response.status;
  if (detail) {
    err.detail = detail;
  }
  return err;
};
