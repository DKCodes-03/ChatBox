import type { FormEvent } from "react";

import { MAX_MESSAGE_CHARACTERS } from "./types";

interface ComposerProps {
  value: string;
  isSubmitting: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
}

export function Composer({ value, isSubmitting, onChange, onSubmit }: ComposerProps) {
  const trimmedValue = value.trim();

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!trimmedValue || isSubmitting) {
      return;
    }
    onSubmit();
  }

  return (
    <form className="chat-composer" onSubmit={handleSubmit}>
      <label className="chat-composer__label" htmlFor="chat-question">
        Ask a PNW question
      </label>
      <div className="chat-composer__controls">
        <textarea
          id="chat-question"
          name="message"
          rows={3}
          maxLength={MAX_MESSAGE_CHARACTERS}
          placeholder="For example: How do I appeal a parking ticket?"
          value={value}
          disabled={isSubmitting}
          onChange={(event) => onChange(event.target.value)}
        />
        <button
          className="chat-button chat-button--primary"
          type="submit"
          disabled={!trimmedValue || isSubmitting}
        >
          Send
        </button>
      </div>
      <p className="chat-composer__hint">
        Do not include student IDs, account details, or other private information.
        <span aria-hidden="true"> · </span>
        <span>
          {value.length.toLocaleString()} / {MAX_MESSAGE_CHARACTERS.toLocaleString()}
        </span>
      </p>
    </form>
  );
}
