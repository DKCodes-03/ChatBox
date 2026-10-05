interface ErrorMessageProps {
  isRetrying: boolean;
  onRetry: () => void;
}

export function ErrorMessage({ isRetrying, onRetry }: ErrorMessageProps) {
  return (
    <section className="chat-message chat-error" role="alert">
      <p className="chat-message__eyebrow">Response unavailable</p>
      <p>We could not get a response. Your question was not retried.</p>
      <button
        className="chat-button chat-button--secondary"
        type="button"
        disabled={isRetrying}
        onClick={onRetry}
      >
        {isRetrying ? "Retrying…" : "Retry"}
      </button>
    </section>
  );
}
