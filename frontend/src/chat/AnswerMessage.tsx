import { AnswerContext, PastDeadlineLabel } from "./AnswerContext";
import type { AnswerEnvelope, AnswerSegment, Citation, ContextField } from "./types";

interface AnswerMessageProps {
  answer: AnswerEnvelope;
  contextControlsDisabled?: boolean;
  onCorrectContext?: (field: ContextField, value: string) => void;
}

type SegmentBlock =
  { kind: "step-group"; segments: AnswerSegment[] } | { kind: "single"; segment: AnswerSegment };

function groupSegments(segments: AnswerSegment[]): SegmentBlock[] {
  const blocks: SegmentBlock[] = [];

  for (const segment of segments) {
    const previous = blocks.at(-1);
    if (segment.kind === "step") {
      if (previous?.kind === "step-group") {
        previous.segments.push(segment);
      } else {
        blocks.push({ kind: "step-group", segments: [segment] });
      }
    } else {
      blocks.push({ kind: "single", segment });
    }
  }

  return blocks;
}

function citationLabel(citation: Citation): string {
  const location = [citation.section, citation.page ? `page ${citation.page}` : undefined].filter(
    Boolean,
  );
  return location.length > 0
    ? `${citation.source_title}, ${location.join(", ")}`
    : citation.source_title;
}

function safeSourceUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function SegmentCitations({
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
        const href = safeSourceUrl(citation.url);
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

function SegmentContent({
  segment,
  citations,
  serverTime,
}: {
  segment: AnswerSegment;
  citations: Map<string, Citation>;
  serverTime: string;
}) {
  return (
    <>
      <PastDeadlineLabel text={segment.text} serverTime={serverTime} />
      <span>{segment.text}</span>
      <SegmentCitations citationIds={segment.citation_ids} citations={citations} />
    </>
  );
}

export function AnswerMessage({
  answer,
  contextControlsDisabled = false,
  onCorrectContext,
}: AnswerMessageProps) {
  const citations = new Map(answer.citations.map((citation) => [citation.id, citation]));
  const blocks = groupSegments(answer.segments);

  return (
    <article
      className={`chat-message chat-message--assistant chat-answer chat-answer--${answer.outcome}`}
      aria-label="Chatbot response"
    >
      <p className="chat-message__eyebrow">
        {answer.outcome === "partial"
          ? "Supported information and limitations"
          : answer.outcome === "unable"
            ? "What I could verify"
            : "PNW information"}
      </p>
      <AnswerContext
        context={answer.context}
        disabled={contextControlsDisabled}
        onCorrect={onCorrectContext}
      />
      {blocks.map((block, blockIndex) =>
        block.kind === "step-group" ? (
          <ol className="chat-answer__steps" key={`steps-${blockIndex}`}>
            {block.segments.map((segment, segmentIndex) => (
              <li key={`${blockIndex}-${segmentIndex}`}>
                <SegmentContent
                  segment={segment}
                  citations={citations}
                  serverTime={answer.server_time}
                />
              </li>
            ))}
          </ol>
        ) : (
          <p
            className={
              block.segment.kind === "limitation"
                ? "chat-answer__limitation"
                : "chat-answer__explanation"
            }
            key={`${block.segment.kind}-${blockIndex}`}
          >
            <SegmentContent
              segment={block.segment}
              citations={citations}
              serverTime={answer.server_time}
            />
          </p>
        ),
      )}
    </article>
  );
}
