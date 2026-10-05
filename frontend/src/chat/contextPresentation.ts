import type { ChatContext, ContextField } from "./types";

export const CONTEXT_FIELD_LABELS: Record<ContextField, string> = {
  campus: "Campus",
  term: "Term",
  year: "Year",
  session: "Session",
  program: "Program",
  student_level: "Student level",
  catalog_year: "Catalog year",
};

const CONTEXT_FIELDS = Object.keys(CONTEXT_FIELD_LABELS) as ContextField[];
const MONTHS: Record<string, number> = {
  january: 0,
  february: 1,
  march: 2,
  april: 3,
  may: 4,
  june: 5,
  july: 6,
  august: 7,
  september: 8,
  october: 9,
  november: 10,
  december: 11,
};
const MONTH_DATE_RE = new RegExp(
  `\\b(${Object.keys(MONTHS).join("|")})\\s+(\\d{1,2}),\\s+(\\d{4})\\b`,
  "gi",
);
const ISO_DATE_RE = /\b(\d{4})-(\d{2})-(\d{2})\b/g;

export function selectedContext(context: ChatContext): Array<[ContextField, string]> {
  return CONTEXT_FIELDS.flatMap((field) => {
    const value = context[field]?.trim();
    return value ? [[field, value]] : [];
  });
}

function validUtcDate(year: number, month: number, day: number): number | null {
  const value = Date.UTC(year, month, day);
  const date = new Date(value);
  return date.getUTCFullYear() === year && date.getUTCMonth() === month && date.getUTCDate() === day
    ? value
    : null;
}

function datesIn(text: string): number[] {
  const dates: number[] = [];
  for (const match of text.matchAll(MONTH_DATE_RE)) {
    const value = validUtcDate(Number(match[3]), MONTHS[match[1].toLowerCase()], Number(match[2]));
    if (value !== null) {
      dates.push(value);
    }
  }
  for (const match of text.matchAll(ISO_DATE_RE)) {
    const value = validUtcDate(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    if (value !== null) {
      dates.push(value);
    }
  }
  return dates;
}

export function includesPastDeadline(text: string, serverTime: string): boolean {
  if (!/\bdeadline(?:s)?\b/i.test(text)) {
    return false;
  }
  const observedAt = new Date(serverTime);
  if (Number.isNaN(observedAt.getTime())) {
    return false;
  }
  const observedDate = Date.UTC(
    observedAt.getUTCFullYear(),
    observedAt.getUTCMonth(),
    observedAt.getUTCDate(),
  );
  return datesIn(text).some((date) => date < observedDate);
}
