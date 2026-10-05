import { useState } from "react";

import { CONTEXT_FIELD_LABELS, includesPastDeadline, selectedContext } from "./contextPresentation";
import type { ChatContext, ContextField } from "./types";

interface AnswerContextProps {
  context: ChatContext;
  disabled?: boolean;
  onCorrect?: (field: ContextField, value: string) => void;
}

export function PastDeadlineLabel({ text, serverTime }: { text: string; serverTime: string }) {
  return includesPastDeadline(text, serverTime) ? (
    <span className="chat-answer__past-label">Past deadline</span>
  ) : null;
}

export function AnswerContext({ context, disabled = false, onCorrect }: AnswerContextProps) {
  const entries = selectedContext(context);
  const [editingField, setEditingField] = useState<ContextField | null>(null);
  const [value, setValue] = useState("");

  if (entries.length === 0) {
    return null;
  }

  function beginCorrection(field: ContextField, currentValue: string) {
    setEditingField(field);
    setValue(currentValue);
  }

  function cancelCorrection() {
    setEditingField(null);
    setValue("");
  }

  function submitCorrection(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const correctedValue = value.trim();
    if (editingField === null || !correctedValue || disabled || !onCorrect) {
      return;
    }
    onCorrect(editingField, correctedValue);
    cancelCorrection();
  }

  return (
    <aside className="chat-answer-context" aria-label="Answer context">
      <p className="chat-answer-context__title">This answer applies to</p>
      <dl className="chat-answer-context__values">
        {entries.map(([field, contextValue]) => (
          <div className="chat-answer-context__value" key={field}>
            <dt>{CONTEXT_FIELD_LABELS[field]}</dt>
            <dd>{contextValue}</dd>
            {onCorrect ? (
              <button
                className="chat-answer-context__change"
                type="button"
                disabled={disabled}
                aria-label={`Change ${CONTEXT_FIELD_LABELS[field].toLowerCase()}`}
                onClick={() => beginCorrection(field, contextValue)}
              >
                Change
              </button>
            ) : null}
          </div>
        ))}
      </dl>
      {editingField !== null ? (
        <form className="chat-answer-context__correction" onSubmit={submitCorrection}>
          <label htmlFor={`context-correction-${editingField}`}>
            New {CONTEXT_FIELD_LABELS[editingField].toLowerCase()}
          </label>
          <div>
            <input
              id={`context-correction-${editingField}`}
              value={value}
              maxLength={120}
              disabled={disabled}
              onChange={(event) => setValue(event.target.value)}
            />
            <button
              className="chat-button chat-button--secondary"
              type="submit"
              disabled={disabled || !value.trim()}
            >
              Save correction
            </button>
            <button
              className="chat-button chat-button--quiet"
              type="button"
              disabled={disabled}
              onClick={cancelCorrection}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : null}
    </aside>
  );
}
