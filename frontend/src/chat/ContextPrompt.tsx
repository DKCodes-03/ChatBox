import { useState } from "react";

import { CONTEXT_FIELD_LABELS } from "./contextPresentation";
import type { ChatContext, Clarification, ContextField } from "./types";

interface ContextPromptProps {
  clarification: Clarification;
  context: ChatContext;
  disabled?: boolean;
  onSubmit: (updates: ChatContext, summary: string) => void;
}

function updateFor(field: ContextField, value: string): ChatContext {
  return { [field]: value };
}

export function ContextPrompt({
  clarification,
  context,
  disabled = false,
  onSubmit,
}: ContextPromptProps) {
  const initialValues = Object.fromEntries(
    clarification.fields.map((field) => [field, context[field] ?? ""]),
  ) as Partial<Record<ContextField, string>>;
  const [values, setValues] = useState(initialValues);
  const [submitted, setSubmitted] = useState(false);
  const singleField = clarification.fields.length === 1 ? clarification.fields[0] : null;
  const options = clarification.options ?? [];
  const controlsDisabled = disabled || submitted;

  function selectOption(option: string) {
    if (singleField === null || controlsDisabled) {
      return;
    }
    setSubmitted(true);
    onSubmit(updateFor(singleField, option), `${CONTEXT_FIELD_LABELS[singleField]}: ${option}`);
  }

  function submitValues(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (controlsDisabled) {
      return;
    }
    const updates: ChatContext = {};
    const summary: string[] = [];
    for (const field of clarification.fields) {
      const value = values[field]?.trim();
      if (!value) {
        return;
      }
      Object.assign(updates, updateFor(field, value));
      summary.push(`${CONTEXT_FIELD_LABELS[field]}: ${value}`);
    }
    setSubmitted(true);
    onSubmit(updates, summary.join("; "));
  }

  const allValuesPresent = clarification.fields.every((field) => values[field]?.trim());

  return (
    <section
      className="chat-message chat-message--assistant chat-clarification"
      aria-label="Clarification needed"
    >
      <p className="chat-message__eyebrow">Context needed</p>
      <p className="chat-clarification__question">{clarification.question}</p>
      {singleField !== null && options.length > 0 ? (
        <div className="chat-clarification__options" aria-label="Suggested answers">
          {options.map((option) => (
            <button
              className="chat-clarification__option"
              type="button"
              disabled={controlsDisabled}
              aria-label={`Use ${option} for ${CONTEXT_FIELD_LABELS[singleField]}`}
              key={option}
              onClick={() => selectOption(option)}
            >
              {option}
            </button>
          ))}
        </div>
      ) : (
        <form className="chat-clarification__form" onSubmit={submitValues}>
          {clarification.fields.map((field) => (
            <label key={field} htmlFor={`clarification-${field}`}>
              {CONTEXT_FIELD_LABELS[field]}
              <input
                id={`clarification-${field}`}
                value={values[field] ?? ""}
                maxLength={120}
                disabled={controlsDisabled}
                onChange={(event) =>
                  setValues((current) => ({ ...current, [field]: event.target.value }))
                }
              />
            </label>
          ))}
          <button
            className="chat-button chat-button--secondary"
            type="submit"
            disabled={controlsDisabled || !allValuesPresent}
          >
            Use this context
          </button>
        </form>
      )}
      <p className="chat-clarification__hint">
        Your selection will be checked against eligible university information before an answer is
        shown.
      </p>
    </section>
  );
}
