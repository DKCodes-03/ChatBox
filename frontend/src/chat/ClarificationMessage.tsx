import type { Clarification } from "./types";

interface ClarificationMessageProps {
  clarification: Clarification;
}

export function ClarificationMessage({ clarification }: ClarificationMessageProps) {
  return (
    <section
      className="chat-message chat-message--assistant chat-clarification"
      aria-label="Clarification needed"
    >
      <p className="chat-message__eyebrow">One detail needed</p>
      <p className="chat-clarification__question">{clarification.question}</p>
      {clarification.options && clarification.options.length > 0 ? (
        <div className="chat-clarification__options" aria-label="Suggested answers">
          {clarification.options.map((option) => (
            <span className="chat-clarification__option" key={option}>
              {option}
            </span>
          ))}
        </div>
      ) : null}
      <p className="chat-clarification__hint">Add this detail in your next message.</p>
    </section>
  );
}
