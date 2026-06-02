import type { ErrorResponse } from "@/src/core/contracts";

export class ApiError extends Error {
  status: number;
  detail: string | null;
  kind: string | null;

  constructor(status: number, payload: ErrorResponse) {
    super(payload.detail || payload.error);
    this.status = status;
    this.detail = payload.detail ?? null;
    this.kind = payload.kind ?? null;
  }
}

export class NetworkError extends Error {
  kind = "network_error";

  constructor(message = "Gateway is unavailable.") {
    super(message);
    this.name = "NetworkError";
  }
}

export function getGatewayBaseUrl(): string {
  return (process.env.NEXT_PUBLIC_ANVIL_GATEWAY_URL || "http://127.0.0.1:18000").replace(/\/$/, "");
}

export function resolveGatewayUrl(path: string): string {
  return new URL(path, `${getGatewayBaseUrl()}/`).toString();
}

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${getGatewayBaseUrl()}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {}),
      },
    });
  } catch {
    throw new NetworkError();
  }

  if (!response.ok) {
    const payload = (await response.json()) as ErrorResponse;
    throw new ApiError(response.status, payload);
  }

  return (await response.json()) as T;
}

export async function apiFormRequest<T>(path: string, form: FormData): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${getGatewayBaseUrl()}${path}`, {
      method: "POST",
      body: form,
    });
  } catch {
    throw new NetworkError();
  }

  if (!response.ok) {
    const payload = (await response.json()) as ErrorResponse;
    throw new ApiError(response.status, payload);
  }

  return (await response.json()) as T;
}
