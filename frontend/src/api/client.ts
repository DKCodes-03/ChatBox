import type {
  AnswerEnvelope,
  AnswerOutcome,
  AnswerSegment,
  ChatContext,
  Citation,
  Clarification,
  ContextField,
  MessageRequest,
  ReasonCode,
  Referral,
  SegmentKind,
} from "../chat/types";

const API_PREFIX = "/api/v1";

const ANSWER_OUTCOMES: ReadonlySet<AnswerOutcome> = new Set([
  "answer",
  "partial",
  "clarification",
  "unable",
]);
const SEGMENT_KINDS: ReadonlySet<SegmentKind> = new Set(["explanation", "step", "limitation"]);
const CONTEXT_FIELDS: ReadonlySet<ContextField> = new Set([
  "campus",
  "term",
  "year",
  "session",
  "program",
  "student_level",
  "catalog_year",
]);
const REASON_CODES: ReadonlySet<ReasonCode> = new Set([
  "missing_evidence",
  "conflict",
  "expired_source",
  "context_required",
  "personal_case",
  "out_of_scope",
  "source_unavailable",
  "processing_unavailable",
]);

export type ServerErrorCode =
  | "bad_request"
  | "origin_not_allowed"
  | "request_too_large"
  | "invalid_request"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "method_not_allowed"
  | "conflict"
  | "session_expired"
  | "rate_limited"
  | "processing_unavailable"
  | "internal_error";

export type ClientErrorCode =
  ServerErrorCode | "invalid_response" | "network_error" | "request_aborted";

const SERVER_ERROR_CODES: ReadonlySet<ServerErrorCode> = new Set([
  "bad_request",
  "origin_not_allowed",
  "request_too_large",
  "invalid_request",
  "unauthorized",
  "forbidden",
  "not_found",
  "method_not_allowed",
  "conflict",
  "session_expired",
  "rate_limited",
  "processing_unavailable",
  "internal_error",
]);

export interface SessionEnvelope {
  expires_at: string;
  server_time: string;
}

export interface ErrorEnvelope {
  error: {
    code: ServerErrorCode;
    message: string;
    retryable: boolean;
  };
  server_time: string;
}

export interface RequestOptions {
  signal?: AbortSignal;
}

export class ApiClientError extends Error {
  readonly code: ClientErrorCode;
  readonly status: number;
  readonly retryable: boolean;
  readonly serverTime: string | null;
  readonly retryAfterSeconds: number | null;

  constructor({
    code,
    message,
    status,
    retryable,
    serverTime = null,
    retryAfterSeconds = null,
  }: {
    code: ClientErrorCode;
    message: string;
    status: number;
    retryable: boolean;
    serverTime?: string | null;
    retryAfterSeconds?: number | null;
  }) {
    super(message);
    this.name = "ApiClientError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
    this.serverTime = serverTime;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowedKeys: readonly string[]): boolean {
  return Object.keys(value).every((key) => allowedKeys.includes(key));
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isSessionEnvelope(value: unknown): value is SessionEnvelope {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, ["expires_at", "server_time"]) &&
    typeof value.expires_at === "string" &&
    typeof value.server_time === "string"
  );
}

function isChatContext(value: unknown): value is ChatContext {
  if (!isRecord(value)) {
    return false;
  }
  return (
    hasOnlyKeys(value, [...CONTEXT_FIELDS]) &&
    Object.entries(value).every(
      ([key, contextValue]) =>
        CONTEXT_FIELDS.has(key as ContextField) && typeof contextValue === "string",
    )
  );
}

function isAnswerSegment(value: unknown): value is AnswerSegment {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, ["kind", "text", "citation_ids"]) &&
    typeof value.kind === "string" &&
    SEGMENT_KINDS.has(value.kind as SegmentKind) &&
    typeof value.text === "string" &&
    isStringArray(value.citation_ids)
  );
}

function isCitation(value: unknown): value is Citation {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, ["id", "source_title", "url", "section", "page", "version_id"]) &&
    typeof value.id === "string" &&
    typeof value.source_title === "string" &&
    typeof value.url === "string" &&
    (value.section === undefined || typeof value.section === "string") &&
    (value.page === undefined ||
      (typeof value.page === "number" && Number.isInteger(value.page))) &&
    typeof value.version_id === "string"
  );
}

function isClarification(value: unknown): value is Clarification {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, ["question", "fields", "options"]) &&
    typeof value.question === "string" &&
    Array.isArray(value.fields) &&
    value.fields.every(
      (field) => typeof field === "string" && CONTEXT_FIELDS.has(field as ContextField),
    ) &&
    (value.options === undefined || isStringArray(value.options))
  );
}

function isReferral(value: unknown): value is Referral {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, ["office_name", "contact_label", "contact_url", "citation_ids"]) &&
    typeof value.office_name === "string" &&
    typeof value.contact_label === "string" &&
    typeof value.contact_url === "string" &&
    isStringArray(value.citation_ids)
  );
}

function isAnswerEnvelope(value: unknown): value is AnswerEnvelope {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, [
      "outcome",
      "segments",
      "citations",
      "context",
      "clarification",
      "referral",
      "reason_code",
      "expires_at",
      "server_time",
    ]) &&
    typeof value.outcome === "string" &&
    ANSWER_OUTCOMES.has(value.outcome as AnswerOutcome) &&
    Array.isArray(value.segments) &&
    value.segments.every(isAnswerSegment) &&
    Array.isArray(value.citations) &&
    value.citations.every(isCitation) &&
    isChatContext(value.context) &&
    (value.clarification === null || isClarification(value.clarification)) &&
    (value.referral === null || isReferral(value.referral)) &&
    isNullableString(value.reason_code) &&
    (value.reason_code === null || REASON_CODES.has(value.reason_code as ReasonCode)) &&
    typeof value.expires_at === "string" &&
    typeof value.server_time === "string"
  );
}

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  return (
    isRecord(value) &&
    hasOnlyKeys(value, ["error", "server_time"]) &&
    isRecord(value.error) &&
    hasOnlyKeys(value.error, ["code", "message", "retryable"]) &&
    typeof value.error.code === "string" &&
    SERVER_ERROR_CODES.has(value.error.code as ServerErrorCode) &&
    typeof value.error.message === "string" &&
    typeof value.error.retryable === "boolean" &&
    typeof value.server_time === "string"
  );
}

function retryAfterSeconds(response: Response): number | null {
  const value = response.headers.get("Retry-After");
  if (value === null || !/^\d+$/.test(value)) {
    return null;
  }
  const seconds = Number(value);
  return Number.isSafeInteger(seconds) ? seconds : null;
}

function invalidResponse(status: number): ApiClientError {
  return new ApiClientError({
    code: "invalid_response",
    message: "The service returned an invalid response.",
    status,
    retryable: status >= 500 || status === 0 || (status >= 200 && status < 300),
  });
}

async function responseJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw invalidResponse(response.status);
  }
}

async function responseError(response: Response): Promise<ApiClientError> {
  const payload = await responseJson(response);
  if (!isErrorEnvelope(payload)) {
    return invalidResponse(response.status);
  }
  return new ApiClientError({
    code: payload.error.code,
    message: payload.error.message,
    status: response.status,
    retryable: payload.error.retryable,
    serverTime: payload.server_time,
    retryAfterSeconds: retryAfterSeconds(response),
  });
}

function requestInit(
  method: "GET" | "POST" | "DELETE",
  signal: AbortSignal | undefined,
  body?: string,
): RequestInit {
  return {
    method,
    ...(body === undefined ? {} : { body }),
    cache: "no-store",
    credentials: "same-origin",
    headers:
      body === undefined
        ? { Accept: "application/json" }
        : {
            Accept: "application/json",
            "Content-Type": "application/json; charset=UTF-8",
          },
    mode: "same-origin",
    redirect: "error",
    signal,
  };
}

export class ChatApiClient {
  readonly #fetch: typeof fetch;

  constructor(fetchImplementation: typeof fetch = globalThis.fetch.bind(globalThis)) {
    this.#fetch = fetchImplementation;
  }

  async #request(
    path: string,
    init: RequestInit,
    signal: AbortSignal | undefined,
  ): Promise<Response> {
    try {
      return await this.#fetch(`${API_PREFIX}${path}`, init);
    } catch {
      if (signal?.aborted) {
        throw new ApiClientError({
          code: "request_aborted",
          message: "The request was cancelled.",
          status: 0,
          retryable: false,
        });
      }
      throw new ApiClientError({
        code: "network_error",
        message: "The service could not be reached.",
        status: 0,
        retryable: true,
      });
    }
  }

  async #jsonRequest<T>(
    path: string,
    init: RequestInit,
    signal: AbortSignal | undefined,
    validate: (value: unknown) => value is T,
  ): Promise<T> {
    const response = await this.#request(path, init, signal);
    if (!response.ok) {
      throw await responseError(response);
    }
    const payload = await responseJson(response);
    if (!validate(payload)) {
      throw invalidResponse(response.status);
    }
    return payload;
  }

  createSession(options: RequestOptions = {}): Promise<SessionEnvelope> {
    return this.#jsonRequest(
      "/sessions",
      requestInit("POST", options.signal, "{}"),
      options.signal,
      isSessionEnvelope,
    );
  }

  getSession(options: RequestOptions = {}): Promise<SessionEnvelope> {
    return this.#jsonRequest(
      "/session",
      requestInit("GET", options.signal),
      options.signal,
      isSessionEnvelope,
    );
  }

  async endSession(options: RequestOptions = {}): Promise<void> {
    const response = await this.#request(
      "/session",
      requestInit("DELETE", options.signal),
      options.signal,
    );
    if (!response.ok) {
      throw await responseError(response);
    }
    if (response.status !== 204) {
      throw invalidResponse(response.status);
    }
  }

  sendMessage(request: MessageRequest, options: RequestOptions = {}): Promise<AnswerEnvelope> {
    return this.#jsonRequest(
      "/messages",
      requestInit("POST", options.signal, JSON.stringify(request)),
      options.signal,
      isAnswerEnvelope,
    );
  }
}

export const chatApiClient = new ChatApiClient();
