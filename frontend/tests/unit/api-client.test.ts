import { describe, expect, it, vi } from "vitest";

import { ApiClientError, ChatApiClient } from "../../src/api/client";
import type { AnswerEnvelope } from "../../src/chat/types";

const session = {
  expires_at: "2026-09-30T18:30:00Z",
  server_time: "2026-09-30T18:00:00Z",
};

const answer: AnswerEnvelope = {
  outcome: "answer",
  segments: [
    {
      kind: "explanation",
      text: "Use the published procedure.",
      citation_ids: ["source-1"],
    },
  ],
  citations: [
    {
      id: "source-1",
      source_title: "Published Procedure",
      url: "https://www.pnw.edu/procedure",
      section: "Steps",
      version_id: "018f8418-bcd6-7000-8000-000000000001",
    },
  ],
  context: { campus: "Hammond" },
  clarification: null,
  referral: null,
  reason_code: null,
  ...session,
};

function jsonResponse(body: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

describe("ChatApiClient", () => {
  it("creates a session using a same-origin, no-store request and browser cookies", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(session, { status: 201 }));
    const client = new ChatApiClient(fetcher);

    await expect(client.createSession()).resolves.toEqual(session);

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/sessions", {
      method: "POST",
      body: "{}",
      cache: "no-store",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json; charset=UTF-8",
      },
      mode: "same-origin",
      redirect: "error",
      signal: undefined,
    });
  });

  it("gets session metadata without sending a body or resetting it client-side", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(session));
    const client = new ChatApiClient(fetcher);

    await expect(client.getSession()).resolves.toEqual(session);

    expect(fetcher).toHaveBeenCalledWith("/api/v1/session", {
      method: "GET",
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      mode: "same-origin",
      redirect: "error",
      signal: undefined,
    });
  });

  it("posts one typed message request with explicit context", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(answer));
    const client = new ChatApiClient(fetcher);
    const request = {
      message: "How do I complete the procedure?",
      context: { campus: "Hammond" },
    };

    await expect(client.sendMessage(request)).resolves.toEqual(answer);

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher).toHaveBeenCalledWith("/api/v1/messages", {
      method: "POST",
      body: JSON.stringify(request),
      cache: "no-store",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json; charset=UTF-8",
      },
      mode: "same-origin",
      redirect: "error",
      signal: undefined,
    });
  });

  it("ends a session through DELETE and accepts an empty 204 response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    const client = new ChatApiClient(fetcher);

    await expect(client.endSession()).resolves.toBeUndefined();

    expect(fetcher).toHaveBeenCalledWith("/api/v1/session", {
      method: "DELETE",
      cache: "no-store",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      mode: "same-origin",
      redirect: "error",
      signal: undefined,
    });
  });

  it("surfaces a typed API error without creating a session or retrying the message", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "session_expired",
            message: "The session has ended.",
            retryable: false,
          },
          server_time: session.server_time,
        },
        { status: 410 },
      ),
    );
    const client = new ChatApiClient(fetcher);

    const error = await client
      .sendMessage({ message: "Do not replay this" })
      .catch((reason: unknown) => reason);

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({
      code: "session_expired",
      message: "The session has ended.",
      retryable: false,
      status: 410,
      serverTime: session.server_time,
      retryAfterSeconds: null,
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v1/messages");
  });

  it("captures Retry-After metadata but never retries a retryable response automatically", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: "rate_limited",
            message: "Too many requests were received. Try again later.",
            retryable: true,
          },
          server_time: session.server_time,
        },
        { status: 429, headers: { "Content-Type": "application/json", "Retry-After": "17" } },
      ),
    );
    const client = new ChatApiClient(fetcher);

    const error = await client.getSession().catch((reason: unknown) => reason);

    expect(error).toMatchObject({
      code: "rate_limited",
      retryable: true,
      retryAfterSeconds: 17,
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("rejects malformed success payloads with a fixed error that does not expose response content", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(jsonResponse({ transcript: "private response content" }));
    const client = new ChatApiClient(fetcher);

    const error = await client.getSession().catch((reason: unknown) => reason);

    expect(error).toMatchObject({
      code: "invalid_response",
      message: "The service returned an invalid response.",
      retryable: true,
      status: 200,
    });
    expect(String(error)).not.toContain("private response content");
  });

  it("rejects otherwise valid payloads containing fields outside the public contract", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        ...answer,
        provider_request_id: "must-not-cross-the-client-boundary",
      }),
    );
    const client = new ChatApiClient(fetcher);

    const error = await client
      .sendMessage({ message: "Show the supported answer" })
      .catch((reason: unknown) => reason);

    expect(error).toMatchObject({
      code: "invalid_response",
      message: "The service returned an invalid response.",
      status: 200,
    });
    expect(String(error)).not.toContain("provider_request_id");
  });

  it("sanitizes network failures and forwards an abort signal without retrying", async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("secret upstream detail"));
    const client = new ChatApiClient(fetcher);
    const controller = new AbortController();

    const error = await client
      .getSession({ signal: controller.signal })
      .catch((reason: unknown) => reason);

    expect(error).toMatchObject({
      code: "network_error",
      message: "The service could not be reached.",
      retryable: true,
      status: 0,
    });
    expect(String(error)).not.toContain("secret upstream detail");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0]?.[1]?.signal).toBe(controller.signal);
  });

  it("reports cancellation with a fixed error and does not resubmit", async () => {
    const controller = new AbortController();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (_input, init) => {
      controller.abort();
      init?.signal?.throwIfAborted();
      return jsonResponse(session);
    });
    const client = new ChatApiClient(fetcher);

    const error = await client
      .getSession({ signal: controller.signal })
      .catch((reason: unknown) => reason);

    expect(error).toMatchObject({
      code: "request_aborted",
      message: "The request was cancelled.",
      retryable: false,
      status: 0,
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
