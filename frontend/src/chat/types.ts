export const MAX_MESSAGE_CHARACTERS = 4_000;

export type AnswerOutcome = "answer" | "partial" | "clarification" | "unable";
export type SegmentKind = "explanation" | "step" | "limitation";
export type ContextField =
  "campus" | "term" | "year" | "session" | "program" | "student_level" | "catalog_year";
export type ReasonCode =
  | "missing_evidence"
  | "conflict"
  | "expired_source"
  | "context_required"
  | "personal_case"
  | "out_of_scope"
  | "source_unavailable"
  | "processing_unavailable";

export interface ChatContext {
  campus?: string;
  term?: string;
  year?: string;
  session?: string;
  program?: string;
  student_level?: string;
  catalog_year?: string;
}

export interface MessageRequest {
  message: string;
  context?: ChatContext;
}

export interface AnswerSegment {
  kind: SegmentKind;
  text: string;
  citation_ids: string[];
}

export interface Citation {
  id: string;
  source_title: string;
  url: string;
  section?: string;
  page?: number;
  version_id: string;
}

export interface Clarification {
  question: string;
  fields: ContextField[];
  options?: string[];
}

export interface Referral {
  office_name: string;
  contact_label: string;
  contact_url: string;
  citation_ids: string[];
}

export interface AnswerEnvelope {
  outcome: AnswerOutcome;
  segments: AnswerSegment[];
  citations: Citation[];
  context: ChatContext;
  clarification: Clarification | null;
  referral: Referral | null;
  reason_code: ReasonCode | null;
  expires_at: string;
  server_time: string;
}

export type SubmitMessage = (request: MessageRequest) => Promise<AnswerEnvelope>;
