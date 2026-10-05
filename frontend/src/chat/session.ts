import { ApiClientError } from "../api/client";
import type { RequestOptions, SessionEnvelope } from "../api/client";
import type { AnswerEnvelope, MessageRequest } from "./types";

const EXPIRY_WARNING_LEAD_MS = 2 * 60 * 1_000;

export interface ChatSessionApi {
  createSession(options?: RequestOptions): Promise<SessionEnvelope>;
  getSession(options?: RequestOptions): Promise<SessionEnvelope>;
  endSession(options?: RequestOptions): Promise<void>;
  sendMessage(request: MessageRequest, options?: RequestOptions): Promise<AnswerEnvelope>;
}

export type SessionEndedReason = "explicit" | "expired" | "unavailable";

export type SessionLifecycleState =
  | { phase: "starting" }
  | { phase: "active"; expiringSoon: boolean }
  | { phase: "checking" }
  | { phase: "ending" }
  | { phase: "ended"; reason: SessionEndedReason }
  | { phase: "unavailable"; recovery: "start" | "resume" };

type StateListener = (state: SessionLifecycleState) => void;

function isEndedSessionError(error: unknown): error is ApiClientError {
  return (
    error instanceof ApiClientError &&
    (error.code === "session_expired" || error.code === "unauthorized")
  );
}

function isAbortedRequest(error: unknown): error is ApiClientError {
  return error instanceof ApiClientError && error.code === "request_aborted";
}

export class ChatSessionController {
  readonly #api: ChatSessionApi;
  readonly #clearConversation: () => void;
  readonly #listeners = new Set<StateListener>();

  #state: SessionLifecycleState = { phase: "starting" };
  #messageController: AbortController | null = null;
  #lifecycleController: AbortController | null = null;
  #warningTimer: ReturnType<typeof setTimeout> | null = null;
  #expiryTimer: ReturnType<typeof setTimeout> | null = null;
  #generation = 0;
  #disposed = false;
  #resumePromise: Promise<void> | null = null;
  #clearedForResume = false;

  constructor(api: ChatSessionApi, clearConversation: () => void) {
    this.#api = api;
    this.#clearConversation = clearConversation;
  }

  get state(): SessionLifecycleState {
    return this.#state;
  }

  subscribe(listener: StateListener): () => void {
    this.#listeners.add(listener);
    return () => this.#listeners.delete(listener);
  }

  async startNewSession(): Promise<void> {
    if (this.#disposed) {
      return;
    }
    this.#invalidateRequests();
    this.#clearTimers();
    this.#clearedForResume = false;
    this.#setState({ phase: "starting" });

    const controller = new AbortController();
    this.#lifecycleController = controller;
    const generation = this.#generation;
    try {
      const metadata = await this.#api.createSession({ signal: controller.signal });
      if (!this.#isCurrent(generation, controller)) {
        return;
      }
      this.#lifecycleController = null;
      this.#armExpiry(metadata);
    } catch (error) {
      if (!this.#isCurrent(generation, controller) || isAbortedRequest(error)) {
        return;
      }
      this.#lifecycleController = null;
      this.#setState({ phase: "unavailable", recovery: "start" });
    }
  }

  async sendMessage(request: MessageRequest): Promise<AnswerEnvelope | null> {
    if (this.#state.phase !== "active" || this.#messageController !== null) {
      throw new Error("The chat session is not ready for a message.");
    }

    const controller = new AbortController();
    this.#messageController = controller;
    const generation = this.#generation;
    try {
      const answer = await this.#api.sendMessage(request, { signal: controller.signal });
      if (!this.#isCurrent(generation, controller)) {
        return null;
      }
      this.#messageController = null;
      this.#armExpiry(answer);
      return answer;
    } catch (error) {
      if (!this.#isCurrent(generation, controller) || isAbortedRequest(error)) {
        return null;
      }
      this.#messageController = null;
      if (isEndedSessionError(error)) {
        this.#finishEnded(error.code === "session_expired" ? "expired" : "unavailable");
        return null;
      }
      throw error;
    }
  }

  async endChat(): Promise<void> {
    if (this.#disposed || this.#state.phase === "ended" || this.#state.phase === "ending") {
      return;
    }

    this.#invalidateConversation();
    this.#setState({ phase: "ending" });
    const controller = new AbortController();
    this.#lifecycleController = controller;
    try {
      await this.#api.endSession({ signal: controller.signal });
    } catch {
      // The local conversation remains cleared and is never restored or retried automatically.
    } finally {
      if (!this.#disposed && this.#lifecycleController === controller) {
        this.#lifecycleController = null;
        this.#setState({ phase: "ended", reason: "explicit" });
      }
    }
  }

  handlePageHide(): void {
    if (this.#disposed || this.#state.phase === "ended" || this.#state.phase === "ending") {
      return;
    }
    this.#invalidateConversation();
    this.#resumePromise = null;
    this.#clearedForResume = true;
    this.#setState({ phase: "checking" });
  }

  resume(): Promise<void> {
    if (
      this.#disposed ||
      this.#state.phase === "starting" ||
      this.#state.phase === "ended" ||
      this.#state.phase === "ending"
    ) {
      return Promise.resolve();
    }
    if (this.#resumePromise !== null) {
      return this.#resumePromise;
    }

    const revalidation = this.#revalidate();
    const trackedRevalidation = revalidation.finally(() => {
      if (this.#resumePromise === trackedRevalidation) {
        this.#resumePromise = null;
      }
    });
    this.#resumePromise = trackedRevalidation;
    return trackedRevalidation;
  }

  retryRecovery(): Promise<void> {
    if (this.#state.phase !== "unavailable") {
      return Promise.resolve();
    }
    return this.#state.recovery === "start" ? this.startNewSession() : this.resume();
  }

  dispose(): void {
    this.#disposed = true;
    this.#invalidateRequests();
    this.#clearTimers();
    this.#listeners.clear();
  }

  async #revalidate(): Promise<void> {
    this.#setState({ phase: "checking" });
    this.#lifecycleController?.abort();
    const controller = new AbortController();
    this.#lifecycleController = controller;
    const generation = this.#generation;

    try {
      const metadata = await this.#api.getSession({ signal: controller.signal });
      if (!this.#isCurrent(generation, controller)) {
        return;
      }
      this.#lifecycleController = null;
      this.#clearedForResume = false;
      this.#armExpiry(metadata);
    } catch (error) {
      if (!this.#isCurrent(generation, controller) || isAbortedRequest(error)) {
        return;
      }
      this.#lifecycleController = null;
      if (isEndedSessionError(error)) {
        this.#finishEnded(error.code === "session_expired" ? "expired" : "unavailable");
        return;
      }
      if (!this.#clearedForResume) {
        this.#clearConversation();
      }
      this.#clearedForResume = true;
      this.#clearTimers();
      this.#setState({ phase: "unavailable", recovery: "resume" });
    }
  }

  #armExpiry(metadata: SessionEnvelope): void {
    this.#clearTimers();
    const expiresAt = Date.parse(metadata.expires_at);
    const serverTime = Date.parse(metadata.server_time);
    const remaining = expiresAt - serverTime;

    if (!Number.isFinite(remaining) || remaining <= 0) {
      this.#finishEnded("expired");
      return;
    }

    this.#setState({ phase: "active", expiringSoon: remaining <= EXPIRY_WARNING_LEAD_MS });
    if (remaining > EXPIRY_WARNING_LEAD_MS) {
      this.#warningTimer = setTimeout(() => {
        if (!this.#disposed && this.#state.phase === "active") {
          this.#setState({ phase: "active", expiringSoon: true });
        }
      }, remaining - EXPIRY_WARNING_LEAD_MS);
    }
    this.#expiryTimer = setTimeout(() => this.#finishEnded("expired"), remaining);
  }

  #finishEnded(reason: SessionEndedReason): void {
    if (this.#disposed || this.#state.phase === "ended") {
      return;
    }
    this.#invalidateConversation();
    this.#setState({ phase: "ended", reason });
  }

  #invalidateConversation(): void {
    this.#generation += 1;
    this.#messageController?.abort();
    this.#messageController = null;
    this.#lifecycleController?.abort();
    this.#lifecycleController = null;
    this.#clearTimers();
    this.#clearConversation();
  }

  #invalidateRequests(): void {
    this.#generation += 1;
    this.#messageController?.abort();
    this.#messageController = null;
    this.#lifecycleController?.abort();
    this.#lifecycleController = null;
  }

  #clearTimers(): void {
    if (this.#warningTimer !== null) {
      clearTimeout(this.#warningTimer);
      this.#warningTimer = null;
    }
    if (this.#expiryTimer !== null) {
      clearTimeout(this.#expiryTimer);
      this.#expiryTimer = null;
    }
  }

  #isCurrent(generation: number, controller: AbortController): boolean {
    return (
      !this.#disposed &&
      generation === this.#generation &&
      (this.#messageController === controller || this.#lifecycleController === controller)
    );
  }

  #setState(state: SessionLifecycleState): void {
    if (this.#disposed) {
      return;
    }
    this.#state = state;
    for (const listener of this.#listeners) {
      listener(state);
    }
  }
}
