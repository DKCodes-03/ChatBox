import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { chatApiClient } from "../api/client";
import { AcademicAnswer } from "./AcademicAnswer";
import { AnswerMessage } from "./AnswerMessage";
import { Composer } from "./Composer";
import { ContextPrompt } from "./ContextPrompt";
import { CONTEXT_FIELD_LABELS } from "./contextPresentation";
import { FailureMessage } from "./FailureMessage";
import { ChatSessionController, type ChatSessionApi, type SessionLifecycleState } from "./session";
import type { AnswerEnvelope, ChatContext, ContextField, MessageRequest } from "./types";
import "./chat.css";

interface ChatPageProps {
  client?: ChatSessionApi;
}

type TranscriptEntry =
  | { id: number; author: "student"; text: string }
  | { id: number; author: "assistant"; answer: AnswerEnvelope; request: MessageRequest };

interface FailedRequest {
  request: MessageRequest;
  error: unknown;
}

interface RequestAnswerOptions {
  appendStudentMessage: boolean;
  context?: ChatContext;
  studentMessage?: string;
}

function hasContext(context: ChatContext | undefined): context is ChatContext {
  return context !== undefined && Object.values(context).some((value) => Boolean(value?.trim()));
}

function isAcademicAnswer(answer: AnswerEnvelope): boolean {
  return Boolean(
    answer.context.program || answer.context.student_level || answer.context.catalog_year,
  );
}

export function ChatPage({ client = chatApiClient }: ChatPageProps) {
  const nextEntryId = useRef(0);
  const [draft, setDraft] = useState("");
  const [entries, setEntries] = useState<TranscriptEntry[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [failedRequest, setFailedRequest] = useState<FailedRequest | null>(null);

  const clearConversation = useCallback(() => {
    setEntries([]);
    setDraft("");
    setFailedRequest(null);
    setIsSubmitting(false);
  }, []);

  const session = useMemo(
    () => new ChatSessionController(client, clearConversation),
    [client, clearConversation],
  );
  const [sessionState, setSessionState] = useState<SessionLifecycleState>(session.state);

  useEffect(() => {
    const unsubscribe = session.subscribe(setSessionState);
    void session.startNewSession();
    return () => {
      unsubscribe();
      session.dispose();
    };
  }, [session]);

  useEffect(() => {
    const handlePageHide = () => session.handlePageHide();
    const handleResume = () => {
      if (document.visibilityState === "visible") {
        void session.resume();
      }
    };

    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("pageshow", handleResume);
    window.addEventListener("focus", handleResume);
    document.addEventListener("visibilitychange", handleResume);
    return () => {
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("pageshow", handleResume);
      window.removeEventListener("focus", handleResume);
      document.removeEventListener("visibilitychange", handleResume);
    };
  }, [session]);

  function entryId() {
    nextEntryId.current += 1;
    return nextEntryId.current;
  }

  async function requestAnswer(
    message: string,
    { appendStudentMessage, context, studentMessage }: RequestAnswerOptions,
  ) {
    if (session.state.phase !== "active") {
      return;
    }
    setFailedRequest(null);
    setIsSubmitting(true);
    if (appendStudentMessage) {
      setEntries((current) => [
        ...current,
        { id: entryId(), author: "student", text: studentMessage ?? message },
      ]);
    }

    const request: MessageRequest = hasContext(context) ? { message, context } : { message };

    try {
      const answer = await session.sendMessage(request);
      if (answer !== null && session.state.phase === "active") {
        setEntries((current) => [
          ...current,
          { id: entryId(), author: "assistant", answer, request },
        ]);
      }
    } catch (error) {
      if (session.state.phase === "active") {
        setFailedRequest({ request, error });
      }
    } finally {
      setIsSubmitting(false);
    }
  }

  function handleSubmit() {
    const message = draft.trim();
    if (!message || isSubmitting || sessionState.phase !== "active") {
      return;
    }
    setDraft("");
    void requestAnswer(message, { appendStudentMessage: true });
  }

  function handleRetry() {
    if (!failedRequest || isSubmitting || sessionState.phase !== "active") {
      return;
    }
    void requestAnswer(failedRequest.request.message, {
      appendStudentMessage: false,
      context: failedRequest.request.context,
    });
  }

  function submitContext(
    entry: Extract<TranscriptEntry, { author: "assistant" }>,
    updates: ChatContext,
    studentMessage: string,
  ) {
    if (isSubmitting || sessionState.phase !== "active") {
      return;
    }
    void requestAnswer(entry.request.message, {
      appendStudentMessage: true,
      context: { ...entry.answer.context, ...updates },
      studentMessage,
    });
  }

  function correctContext(
    entry: Extract<TranscriptEntry, { author: "assistant" }>,
    field: ContextField,
    value: string,
  ) {
    submitContext(
      entry,
      { [field]: value },
      `${CONTEXT_FIELD_LABELS[field]} corrected to ${value}`,
    );
  }

  const isActive = sessionState.phase === "active";
  const latestAssistantEntryId = [...entries]
    .reverse()
    .find((entry) => entry.author === "assistant")?.id;

  return (
    <main className="chat-shell">
      <header className="chat-header">
        <div>
          <p className="chat-header__institution">Purdue University Northwest</p>
          <h1>PNW Student Chatbot</h1>
        </div>
        <button
          className="chat-button chat-button--quiet"
          type="button"
          disabled={!isActive}
          onClick={() => void session.endChat()}
        >
          End chat
        </button>
      </header>

      <section className="chat-notice" aria-labelledby="chat-notice-title">
        <p className="chat-notice__label" id="chat-notice-title">
          Before you ask
        </p>
        <p>
          Get general PNW information supported by university sources. This chat cannot access your
          records, make decisions, or complete transactions.
        </p>
        <p>
          Messages stay only in this active conversation and are deleted when you end the chat or
          after 30 minutes without a student message.
        </p>
      </section>

      <section className="chat-transcript" aria-label="Conversation">
        {sessionState.phase === "starting" ? (
          <SessionStatus message="Starting a private chat…" />
        ) : null}
        {sessionState.phase === "checking" ? (
          <SessionStatus message="Checking that your chat is still active…" />
        ) : null}
        {sessionState.phase === "ending" ? (
          <SessionStatus message="Ending this chat and clearing its messages…" />
        ) : null}
        {sessionState.phase === "ended" ? (
          <EndedSession
            reason={sessionState.reason}
            onStart={() => void session.startNewSession()}
          />
        ) : null}
        {sessionState.phase === "unavailable" ? (
          <UnavailableSession
            recovery={sessionState.recovery}
            onRetry={() => void session.retryRecovery()}
          />
        ) : null}

        {isActive && sessionState.expiringSoon ? (
          <section className="chat-session-warning" role="status">
            <div>
              <p className="chat-message__eyebrow">Session ending soon</p>
              <p>Send a message to keep this conversation active.</p>
            </div>
            <button
              className="chat-button chat-button--secondary"
              type="button"
              disabled={isSubmitting}
              onClick={() =>
                void requestAnswer("Continue this conversation", { appendStudentMessage: true })
              }
            >
              Continue this conversation
            </button>
          </section>
        ) : null}

        {isActive && entries.length === 0 ? (
          <div className="chat-welcome">
            <p className="chat-message__eyebrow">Ask in your own words</p>
            <h2>What can I help you find?</h2>
            <p>I’ll show the university sources that support the information I provide.</p>
          </div>
        ) : null}

        {isActive
          ? entries.map((entry) =>
              entry.author === "student" ? (
                <section
                  className="chat-message chat-message--student"
                  aria-label="Your question"
                  key={entry.id}
                >
                  <p>{entry.text}</p>
                </section>
              ) : entry.answer.outcome === "clarification" && entry.answer.clarification ? (
                <ContextPrompt
                  clarification={entry.answer.clarification}
                  context={entry.answer.context}
                  disabled={isSubmitting || entry.id !== latestAssistantEntryId}
                  key={entry.id}
                  onSubmit={(updates, summary) => submitContext(entry, updates, summary)}
                />
              ) : entry.answer.outcome === "unable" ? (
                <FailureMessage
                  answer={entry.answer}
                  isRetrying={isSubmitting}
                  key={entry.id}
                  onRetry={
                    entry.answer.reason_code === "processing_unavailable"
                      ? () =>
                          void requestAnswer(entry.request.message, {
                            appendStudentMessage: false,
                            context: entry.request.context,
                          })
                      : undefined
                  }
                />
              ) : isAcademicAnswer(entry.answer) ? (
                <AcademicAnswer
                  answer={entry.answer}
                  contextControlsDisabled={isSubmitting}
                  key={entry.id}
                  onCorrectContext={
                    entry.id === latestAssistantEntryId
                      ? (field, value) => correctContext(entry, field, value)
                      : undefined
                  }
                />
              ) : (
                <AnswerMessage
                  answer={entry.answer}
                  contextControlsDisabled={isSubmitting}
                  key={entry.id}
                  onCorrectContext={
                    entry.id === latestAssistantEntryId
                      ? (field, value) => correctContext(entry, field, value)
                      : undefined
                  }
                />
              ),
            )
          : null}

        {isActive && isSubmitting ? (
          <div className="chat-loading" role="status" aria-live="polite">
            <span className="chat-loading__dots" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            Looking for supported information…
          </div>
        ) : null}

        {isActive && failedRequest ? (
          <FailureMessage
            error={failedRequest.error}
            isRetrying={isSubmitting}
            onRetry={handleRetry}
          />
        ) : null}
      </section>

      {isActive ? (
        <Composer
          value={draft}
          isSubmitting={isSubmitting}
          onChange={setDraft}
          onSubmit={handleSubmit}
        />
      ) : null}
    </main>
  );
}

function SessionStatus({ message }: { message: string }) {
  return (
    <div className="chat-session-state" role="status" aria-live="polite">
      <span className="chat-loading__dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      {message}
    </div>
  );
}

function EndedSession({
  reason,
  onStart,
}: {
  reason: "explicit" | "expired" | "unavailable";
  onStart: () => void;
}) {
  const message =
    reason === "explicit"
      ? "This chat ended and its messages were cleared."
      : reason === "expired"
        ? "This chat ended after 30 minutes without a student message. Messages and drafts were cleared."
        : "The previous chat is no longer available. Its messages and drafts were cleared.";

  return (
    <section className="chat-session-panel" role="status" aria-live="polite">
      <p className="chat-message__eyebrow">Session ended</p>
      <h2>Your previous conversation is closed.</h2>
      <p>{message}</p>
      <p>Start a new chat, then submit your question again if you still need help.</p>
      <button className="chat-button chat-button--primary" type="button" onClick={onStart}>
        Start new chat
      </button>
    </section>
  );
}

function UnavailableSession({
  recovery,
  onRetry,
}: {
  recovery: "start" | "resume";
  onRetry: () => void;
}) {
  return (
    <section className="chat-session-panel chat-session-panel--error" role="alert">
      <p className="chat-message__eyebrow">Chat unavailable</p>
      <h2>
        {recovery === "start"
          ? "A private chat could not be started."
          : "This chat could not be confirmed."}
      </h2>
      <p>
        {recovery === "resume"
          ? "Messages and drafts were cleared before attempting to restore the page."
          : "No message has been sent or saved."}
      </p>
      <button className="chat-button chat-button--secondary" type="button" onClick={onRetry}>
        Try again
      </button>
    </section>
  );
}
