import { AnswerContext, PastDeadlineLabel } from "./AnswerContext";
import type { AnswerEnvelope, AnswerSegment, Citation, ContextField, Referral } from "./types";

interface AcademicAnswerProps {
  answer: AnswerEnvelope;
  contextControlsDisabled?: boolean;
  onCorrectContext?: (field: ContextField, value: string) => void;
}

type SegmentBlock =
  | { kind: "requirement-or-step-group"; segments: AnswerSegment[] }
  | { kind: "single"; segment: AnswerSegment };

const PREREQUISITE_LANGUAGE =
  /\b(prerequisite|corequisite|minimum grade|concurrent(?:ly)?|either|alternative)\b/i;

function groupSegments(segments: AnswerSegment[]): SegmentBlock[] {
  const blocks: SegmentBlock[] = [];

  for (const segment of segments) {
    const previous = blocks.at(-1);
    if (segment.kind === "step") {
      if (previous?.kind === "requirement-or-step-group") {
        previous.segments.push(segment);
      } else {
        blocks.push({ kind: "requirement-or-step-group", segments: [segment] });
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

function safeUrl(value: string, protocols: ReadonlySet<string>): string | null {
  try {
    const url = new URL(value);
    return protocols.has(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
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
      <CitationLinks citationIds={segment.citation_ids} citations={citations} />
    </>
  );
}

function RequirementOrStepGroup({
  segments,
  citations,
  serverTime,
}: {
  segments: AnswerSegment[];
  citations: Map<string, Citation>;
  serverTime: string;
}) {
  const presentsPrerequisites = segments.some((segment) =>
    PREREQUISITE_LANGUAGE.test(segment.text),
  );
  const title = presentsPrerequisites ? "Published prerequisite groups" : "Published steps";
  const List = presentsPrerequisites ? "ul" : "ol";

  return (
    <section className="chat-academic__group" aria-label={title}>
      <h2>{title}</h2>
      <List
        className={
          presentsPrerequisites
            ? "chat-academic__requirements"
            : "chat-answer__steps chat-academic__steps"
        }
      >
        {segments.map((segment, index) => (
          <li key={index}>
            <SegmentContent segment={segment} citations={citations} serverTime={serverTime} />
          </li>
        ))}
      </List>
    </section>
  );
}

function VerifiedReferral({
  referral,
  citations,
}: {
  referral: Referral;
  citations: Map<string, Citation>;
}) {
  const href = safeUrl(referral.contact_url, new Set(["https:", "mailto:", "tel:"]));

  return (
    <aside className="chat-academic__referral" aria-label="Verified university next step">
      <p className="chat-message__eyebrow">Verified next step</p>
      <p className="chat-academic__office">{referral.office_name}</p>
      <p>
        {href ? (
          <a
            href={href}
            target={href.startsWith("https:") ? "_blank" : undefined}
            rel={href.startsWith("https:") ? "noreferrer noopener" : undefined}
          >
            {referral.contact_label}
          </a>
        ) : (
          <span>{referral.contact_label}</span>
        )}
        <CitationLinks citationIds={referral.citation_ids} citations={citations} />
      </p>
    </aside>
  );
}

export function AcademicAnswer({
  answer,
  contextControlsDisabled = false,
  onCorrectContext,
}: AcademicAnswerProps) {
  const citations = new Map(answer.citations.map((citation) => [citation.id, citation]));
  const blocks = groupSegments(answer.segments);

  return (
    <article
      className={`chat-message chat-message--assistant chat-answer chat-academic chat-answer--${answer.outcome}`}
      aria-label="Chatbot response"
    >
      <p className="chat-message__eyebrow">
        {answer.outcome === "partial"
          ? "Published information and limitations"
          : "Published academic information"}
      </p>

      <section className="chat-academic__conditions" aria-label="Academic source conditions">
        <h2>Source conditions</h2>
        <AnswerContext
          context={answer.context}
          disabled={contextControlsDisabled}
          onCorrect={onCorrectContext}
        />
      </section>

      {blocks.map((block, blockIndex) =>
        block.kind === "requirement-or-step-group" ? (
          <RequirementOrStepGroup
            segments={block.segments}
            citations={citations}
            serverTime={answer.server_time}
            key={`group-${blockIndex}`}
          />
        ) : block.segment.kind === "limitation" ? (
          <aside
            className="chat-academic__source-limitation"
            aria-label="Source limitation"
            key={`limitation-${blockIndex}`}
          >
            <p className="chat-message__eyebrow">Source limitation</p>
            <p>
              <SegmentContent
                segment={block.segment}
                citations={citations}
                serverTime={answer.server_time}
              />
            </p>
          </aside>
        ) : (
          <p className="chat-answer__explanation" key={`explanation-${blockIndex}`}>
            <SegmentContent
              segment={block.segment}
              citations={citations}
              serverTime={answer.server_time}
            />
          </p>
        ),
      )}

      <aside className="chat-academic__boundary" aria-label="Academic advising boundary">
        <p className="chat-message__eyebrow">Confirm your situation</p>
        <p>
          This explains published requirements. It cannot determine your personal course
          eligibility, approve a plan of study, or determine your graduation status. Confirm how the
          requirements apply to your record with your academic advisor or the appropriate university
          office.
        </p>
      </aside>

      {answer.referral ? (
        <VerifiedReferral referral={answer.referral} citations={citations} />
      ) : null}
    </article>
  );
}
