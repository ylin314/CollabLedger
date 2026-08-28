export interface ApiErrorDetail {
  field?: string;
  message?: string;
}

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: ApiErrorDetail[];

  constructor(
    message: string,
    status: number,
    code?: string,
    details?: ApiErrorDetail[],
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

let unauthorizedHandler: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null) {
  unauthorizedHandler = handler;
}

export async function request<T = unknown>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (
    options.body != null &&
    !(options.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(url, {
    ...options,
    credentials: "include",
    headers,
  });
  let payload: any = null;
  if (response.status !== 204) {
    const body = await response.text();
    if (body) {
      try {
        payload = JSON.parse(body);
      } catch {
        payload = body;
      }
    }
  }

  if (!response.ok) {
    if (response.status === 401) unauthorizedHandler?.();
    throw new ApiError(
      payload?.error?.message || "请求失败，请稍后重试",
      response.status,
      payload?.error?.code,
      payload?.error?.details,
    );
  }

  return (response.status === 204 ? null : payload) as T;
}

export function getJson<T = unknown>(url: string) {
  return request<T>(url);
}

export function sendJson<T = unknown>(url: string, options: RequestInit = {}) {
  return request<T>(url, options);
}

export function formatApiError(error: unknown) {
  const apiError = error as ApiError;
  const details = Array.isArray(apiError?.details)
    ? apiError.details.map((item) => item?.message).filter(Boolean)
    : [];
  const unique = [...new Set(details)];
  if (unique.length) return unique.join("；");
  return apiError?.message || "请求失败，请稍后重试";
}
