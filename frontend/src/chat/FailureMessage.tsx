import { ApiClientError } from "../api/client";
import type { AnswerEnvelope, Citation, ReasonCode } from "./types";

interface FailureMessageProps {
  answer?: AnswerEnvelope;
  error?: unknown;
  isRetrying: boolean;
  onRetry?: () => void;
}

const REASON_TITLES: Record<ReasonCode, string> = {
  missing_evidence: "Reliable information unavailable",
  conflict: "University sources disagree",
  expired_source: "Current information unavailable",
  context_required: "More context is needed",
  personal_case: "Help with your personal case",
  out_of_scope: "Question outside this chatbot’s scope",
  source_unavailable: "University source unavailable",
  processing_unavailable: "Answer service temporarily unavailable",
};

function safeUrl(value: string, protocols: ReadonlySet<string>): string | null {
  try {
    const url = new URL(value);
    return protocols.has(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function citationLabel(citation: Citation): string {
  const location = [citation.section, citation.page ? `page ${citation.page}` : undefined].filter(
    Boolean,
  );
  return location.length > 0
    ? `${citation.source_title}, ${location.join(", ")}`
    : citation.source_title;
}

function CitationLinks({
  citationIds,
  citations,
}: {
  citationIds: string[];
  citations: Map<string, Citation>;
}) {
  const referenced = citationIds.flatMap((id) => {
    const citation = citations.get(id);
    return citation ? [citation] : [];
  });

  if (referenced.length === 0) {
    return null;
  }

  return (
    <span className="chat-citations" aria-label="Sources">
      {referenced.map((citation) => {
        const href = safeUrl(citation.url, new Set(["https:"]));
        const label = citationLabel(citation);
        return href ? (
          <a key={citation.id} href={href} target="_blank" rel="noreferrer noopener">
            {label}
          </a>
        ) : (
          <span key={citation.id}>{label}</span>
        );
      })}
    </span>
  );
}

function RetryAction({ isRetrying, onRetry }: { isRetrying: boolean; onRetry: () => void }) {
  return (
    <div className="chat-failure__retry">
      <p>The question will only be sent again if you select Retry.</p>
      <button
        className="chat-button chat-button--secondary"
        type="button"
        disabled={isRetrying}
        onClick={onRetry}
      >
        {isRetrying ? "Retrying…" : "Retry"}
      </button>
    </div>
  );
}

function RequestFailure({
  error,
  isRetrying,
  onRetry,
}: {
  error: unknown;
  isRetrying: boolean;
  onRetry?: () => void;
}) {
  const apiError = error instanceof ApiClientError ? error : null;
  const isRateLimited = apiError?.code === "rate_limited";
  const isProviderOutage = apiError?.code === "processing_unavailable";
  const retryable = apiError?.retryable ?? true;
  const title = isRateLimited
    ? "Answer service is busy"
    : isProviderOutage
      ? "Answer service temporarily unavailable"
      : "Response unavailable";
  const message = isRateLimited
    ? "The service cannot process another request yet. Your question was not retried."
    : isProviderOutage
      ? "The answer service is temporarily unavailable. Your question was not retried."
      : "We could not get a response. Your question was not retried.";

  return (
    <section className="chat-message chat-failure chat-failure--request" role="alert">
      <p className="chat-message__eyebrow">Request limitation</p>
      <h2>{title}</h2>
      <p>{message}</p>
      {apiError?.retryAfterSeconds ? (
        <p className="chat-failure__retry-after">
          The service recommends waiting {apiError.retryAfterSeconds} seconds before retrying.
        </p>
      ) : null}
      {retryable && onRetry ? <RetryAction isRetrying={isRetrying} onRetry={onRetry} /> : null}
    </section>
  );
}

function UnableAnswer({
  answer,
  isRetrying,
  onRetry,
}: {
  answer: AnswerEnvelope;
  isRetrying: boolean;
  onRetry?: () => void;
}) {
  const citations = new Map(answer.citations.map((citation) => [citation.id, citation]));
  const reasonCode = answer.reason_code ?? "missing_evidence";
  const referralHref = answer.referral
    ? safeUrl(answer.referral.contact_url, new Set(["https:", "mailto:", "tel:"]))
    : null;

  return (
    <article
      className={`chat-message chat-message--assistant chat-failure chat-failure--${reasonCode}`}
      aria-label="Chatbot response"
    >
      <p className="chat-message__eyebrow">What I could verify</p>
      <h2>{REASON_TITLES[reasonCode]}</h2>
      {answer.segments.map((segment, index) => (
        <p className="chat-answer__limitation" key={`${segment.kind}-${index}`}>
          <span>{segment.text}</span>
          <CitationLinks citationIds={segment.citation_ids} citations={citations} />
        </p>
      ))}

      {answer.referral ? (
        <aside className="chat-failure__referral" aria-label="Verified university contact">
          <p className="chat-message__eyebrow">Verified university contact</p>
          <p className="chat-failure__office">{answer.referral.office_name}</p>
          <p>
            {referralHref ? (
              <a
                href={referralHref}
                target={referralHref.startsWith("https:") ? "_blank" : undefined}
                rel={referralHref.startsWith("https:") ? "noreferrer noopener" : undefined}
              >
                {answer.referral.contact_label}
              </a>
            ) : (
              <span>{answer.referral.contact_label}</span>
            )}
            <CitationLinks citationIds={answer.referral.citation_ids} citations={citations} />
          </p>
        </aside>
      ) : null}

      {onRetry ? <RetryAction isRetrying={isRetrying} onRetry={onRetry} /> : null}
    </article>
  );
}

export function FailureMessage({ answer, error, isRetrying, onRetry }: FailureMessageProps) {
  if (answer?.outcome === "unable") {
    return <UnableAnswer answer={answer} isRetrying={isRetrying} onRetry={onRetry} />;
  }

  return <RequestFailure error={error} isRetrying={isRetrying} onRetry={onRetry} />;
}
