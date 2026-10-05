import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientError } from "../../src/api/client";
import {
  ChatSessionController,
  type ChatSessionApi,
  type SessionLifecycleState,
} from "../../src/chat/session";
import type { AnswerEnvelope, MessageRequest } from "../../src/chat/types";

const THIRTY_MINUTES = 30 * 60 * 1_000;
const session = {
  server_time: "2026-09-30T18:00:00.000Z",
  expires_at: "2026-09-30T18:30:00.000Z",
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
      version_id: "018f8418-bcd6-7000-8000-000000000001",
    },
  ],
  context: {},
  clarification: null,
  referral: null,
  reason_code: null,
  ...session,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function api(overrides: Partial<ChatSessionApi> = {}): ChatSessionApi {
  return {
    createSession: vi.fn().mockResolvedValue(session),
    getSession: vi.fn().mockResolvedValue(session),
    endSession: vi.fn().mockResolvedValue(undefined),
    sendMessage: vi.fn().mockResolvedValue(answer),
    ...overrides,
  };
}

describe("ChatSessionController", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("starts an in-memory session and warns at 28 minutes without polling", async () => {
    const client = api();
    const clearConversation = vi.fn();
    const controller = new ChatSessionController(client, clearConversation);
    const observed: SessionLifecycleState[] = [];
    controller.subscribe((state) => observed.push(state));

    await controller.startNewSession();
    expect(controller.state).toMatchObject({ phase: "active", expiringSoon: false });

    await vi.advanceTimersByTimeAsync(28 * 60 * 1_000 - 1);
    expect(controller.state).toMatchObject({ phase: "active", expiringSoon: false });

    await vi.advanceTimersByTimeAsync(1);
    expect(controller.state).toMatchObject({ phase: "active", expiringSoon: true });
    expect(client.getSession).not.toHaveBeenCalled();
    expect(clearConversation).not.toHaveBeenCalled();
    expect(observed.some((state) => state.phase === "active" && state.expiringSoon)).toBe(true);
  });

  it("clears content and ends locally at the server-derived expiry boundary", async () => {
    const clearConversation = vi.fn();
    const controller = new ChatSessionController(api(), clearConversation);
    await controller.startNewSession();

    await vi.advanceTimersByTimeAsync(THIRTY_MINUTES - 1);
    expect(controller.state.phase).toBe("active");

    await vi.advanceTimersByTimeAsync(1);
    expect(clearConversation).toHaveBeenCalledTimes(1);
    expect(controller.state).toEqual({ phase: "ended", reason: "expired" });
  });

  it("explicit End chat clears immediately, aborts pending work, and ignores a late answer", async () => {
    const pending = deferred<AnswerEnvelope>();
    let messageSignal: AbortSignal | undefined;
    const client = api({
      sendMessage: vi.fn((request: MessageRequest, options) => {
        expect(request.message).toBe("distinctive synthetic question");
        messageSignal = options?.signal;
        return pending.promise;
      }),
    });
    const clearConversation = vi.fn();
    const controller = new ChatSessionController(client, clearConversation);
    await controller.startNewSession();

    const response = controller.sendMessage({ message: "distinctive synthetic question" });
    const ending = controller.endChat();

    expect(clearConversation).toHaveBeenCalledTimes(1);
    expect(messageSignal?.aborted).toBe(true);
    expect(controller.state.phase).toBe("ending");

    pending.resolve(answer);
    await expect(response).resolves.toBeNull();
    await ending;

    expect(client.endSession).toHaveBeenCalledTimes(1);
    expect(controller.state).toEqual({ phase: "ended", reason: "explicit" });
  });

  it("does not replay a message or create a replacement session after a 410", async () => {
    const client = api({
      sendMessage: vi.fn().mockRejectedValue(
        new ApiClientError({
          code: "session_expired",
          message: "The session has ended.",
          status: 410,
          retryable: false,
        }),
      ),
    });
    const clearConversation = vi.fn();
    const controller = new ChatSessionController(client, clearConversation);
    await controller.startNewSession();

    await expect(controller.sendMessage({ message: "do not replay" })).resolves.toBeNull();

    expect(controller.state).toEqual({ phase: "ended", reason: "expired" });
    expect(clearConversation).toHaveBeenCalledTimes(1);
    expect(client.sendMessage).toHaveBeenCalledTimes(1);
    expect(client.createSession).toHaveBeenCalledTimes(1);
  });

  it("clears on pagehide and validates the session before becoming active after resume", async () => {
    const resumedSession = {
      server_time: "2026-09-30T18:05:00.000Z",
      expires_at: "2026-09-30T18:35:00.000Z",
    };
    const client = api({ getSession: vi.fn().mockResolvedValue(resumedSession) });
    const clearConversation = vi.fn();
    const controller = new ChatSessionController(client, clearConversation);
    await controller.startNewSession();

    controller.handlePageHide();
    expect(clearConversation).toHaveBeenCalledTimes(1);
    expect(controller.state.phase).toBe("checking");

    await controller.resume();
    expect(client.getSession).toHaveBeenCalledTimes(1);
    expect(controller.state).toEqual({ phase: "active", expiringSoon: false });
  });

  it("clears and shows an ended state when resume validation finds an unknown session", async () => {
    const client = api({
      getSession: vi.fn().mockRejectedValue(
        new ApiClientError({
          code: "unauthorized",
          message: "A valid session is required.",
          status: 401,
          retryable: false,
        }),
      ),
    });
    const clearConversation = vi.fn();
    const controller = new ChatSessionController(client, clearConversation);
    await controller.startNewSession();

    await controller.resume();

    expect(clearConversation).toHaveBeenCalledTimes(1);
    expect(controller.state).toEqual({ phase: "ended", reason: "unavailable" });
    expect(client.createSession).toHaveBeenCalledTimes(1);
  });
});
