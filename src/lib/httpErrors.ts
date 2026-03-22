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
    return "Daily quota exceeded. Please try again after your quota resets.";
  }
  if (detail.type === "rate_limit_exceeded") {
    return "You are sending requests too quickly. Please wait a moment.";
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
    message || `API error: ${response.status}`,
  ) as ApiError;
  err.status = response.status;
  if (detail) {
    err.detail = detail;
  }
  return err;
};
