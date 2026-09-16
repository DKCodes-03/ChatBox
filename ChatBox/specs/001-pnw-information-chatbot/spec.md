# Feature Specification: PNW Student Information Chatbot

**Feature Branch**: `001-pnw-information-chatbot`

**Created**: 2026-09-14

**Status**: Draft

**Input**: User description: "I'm building a chatbot to answer students questions about school information, policy etc."

## Clarifications

### Session 2026-09-16
- Q: What should the first deployment look like for the chatbot? → A: A - Public web chat on an official PNW page
- Q: Which source approval model should govern the chatbot’s answers before launch? → A: B - Any public PNW page that appears official
- Q: Should the chatbot remember earlier context within the same conversation when a student asks follow-up questions? → A: B - Keep context throughout the conversation
- Q: Should the first release keep chat conversations ephemeral, or should it retain them for quality review and monitoring? → A: A - No transcript retention; chats are ephemeral and not stored
- Q: What source formats should the first release be able to answer from? → A: A - Official web pages and linked PDFs/documents

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask a university information question (Priority: P1)

As a currently registered Purdue University Northwest undergraduate or graduate
student, I want to ask a question about university information, policy, deadlines,
or procedures in plain language so that I can get a clear answer without searching
through multiple webpages.

**Why this priority**: Finding basic university information is the primary problem
identified by the Dean of Students office and all student interviews.

**Independent Test**: Ask representative questions about parking tickets,
registration, academic standing, grade appeals, financial aid deadlines, and
program availability. The chatbot provides a concise answer, identifies the
relevant campus or term when needed, and links to the supporting official source.

**Acceptance Scenarios**:

1. **Given** the approved information includes an answer to a student's question,
   **When** the student asks the question in natural language, **Then** the
   chatbot returns a concise answer and at least one direct official source link.
2. **Given** a question concerns multiple campuses or academic terms, **When** the
   student asks it without enough context, **Then** the chatbot asks for the
   missing campus or term before giving a campus- or term-specific answer.
3. **Given** the answer is assembled from multiple approved university pages,
   **When** the chatbot responds, **Then** it presents the information as one
   coherent answer and identifies the relevant sources.

---

### User Story 2 - Find structured academic and procedural information (Priority: P2)

As a student researching a program, course, prerequisite, registration process,
deadline, or graduation procedure, I want the chatbot to summarize information
that is spread across pages, tables, expandable sections, and documents so that I
can understand the next step without manually following a chain of links.

**Why this priority**: Interviews identified course prerequisites, campus tags,
academic schedules, plans of study, program availability, and graduation
procedures as especially difficult to locate and interpret.

**Independent Test**: Ask questions requiring information from an academic catalog,
academic schedule, graduate studies page, parking rules, student handbook, or
related policy document. Confirm that the answer preserves dates, conditions,
campus distinctions, and required next steps.

**Acceptance Scenarios**:

1. **Given** an official source contains a table of dates or deadlines, **When**
   the student asks about a date, **Then** the chatbot presents the applicable
   term, event, date, and any stated conditions without mixing table rows.
2. **Given** an official course or program page contains prerequisites or campus
   distinctions in separate sections, **When** the student asks about them, **Then**
   the chatbot combines the relevant details and identifies the campus or program
   scope.
3. **Given** a procedure requires a form, portal, office, or linked document,
   **When** the student asks how to complete it, **Then** the chatbot explains the
   available steps and links directly to the relevant official destination.

---

### User Story 3 - Receive a safe response when information is unavailable (Priority: P1)

As a student, I want the chatbot to acknowledge uncertainty and direct me to the
right university office when it cannot reliably answer so that I do not make an
academic, financial, or administrative decision using unsupported information.

**Why this priority**: The Dean of Students office identified incorrect policy
information as the most important risk.

**Independent Test**: Ask questions outside the approved corpus, ask for
personalized decisions that require a student's record, and ask about conflicting
or stale sources. Confirm that the chatbot does not invent an answer and provides
an appropriate escalation path.

**Acceptance Scenarios**:

1. **Given** no approved source supports an answer, **When** the student asks the
   question, **Then** the chatbot clearly states that it cannot provide a reliable
   answer and directs the student to an appropriate office or advisor.
2. **Given** approved sources conflict or their freshness cannot be established,
   **When** the student asks about the conflicting topic, **Then** the chatbot
   identifies the uncertainty, avoids choosing an unsupported policy, and directs
   the student to confirm with the responsible office.
3. **Given** a question requires access to a student's personal record or a
   binding academic decision, **When** the student asks the chatbot to decide,
   **Then** the chatbot explains the limitation and refers the student to the
   appropriate authorized staff member.

### Edge Cases

- A student asks about a campus, term, program, or population that is not
  identified in the question.
- A source contains different dates for different terms, campuses, or student
  categories.
- A page links to a PDF, expandable section, table, or child page containing the
  actual answer.
- A source is unavailable, malformed, inaccessible, or missing a meaningful
  update date.
- Multiple approved sources provide materially different instructions.
- A student asks for a personalized registration, degree-audit, financial-aid, or
  disciplinary decision.
- A student asks about a topic outside the approved university information scope.
- A source contains descriptive or marketing content but no actionable procedure.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support currently registered Purdue University
  Northwest undergraduate and graduate students as its initial audience.
- **FR-002**: The system MUST accept natural-language questions about approved
  university information, including policies, rules, procedures, deadlines,
  registration, academic standing, grade appeals, financial aid, programs,
  courses, student services, and contacts.
- **FR-003**: The system MUST answer policy, rule, deadline, and procedure
  questions only from approved university information.
- **FR-004**: Each answer based on approved information MUST identify the
  supporting official source or sources and provide direct links where available.
- **FR-005**: The system MUST distinguish official university policy from general
  explanatory content and MUST NOT present unsupported content as official policy.
- **FR-006**: The system MUST preserve the meaning of structured information,
  including table rows, dates, percentages, prerequisites, conditions, and
  campus-specific labels.
- **FR-007**: The system MUST combine relevant information from linked university
  pages and documents when a complete answer is distributed across sources.
- **FR-008**: The system MUST identify the applicable campus, academic term,
  program, student level, or other scope when that context affects the answer.
- **FR-009**: When required context is missing, the system MUST ask a focused
  follow-up question before providing a scope-specific answer.
- **FR-010**: The system MUST communicate source freshness or the available
  last-updated information when it is relevant to the reliability of an answer.
- **FR-011**: The system MUST detect or surface materially conflicting, stale, or
  insufficient source information rather than silently selecting an answer.
- **FR-012**: When reliable information is unavailable, the system MUST say that
  it cannot provide a reliable answer and MUST avoid fabricating details.
- **FR-013**: For unanswered, conflicting, or personalized questions, the system
  MUST direct the student to an appropriate university office, advisor, or other
  authoritative contact when one can be identified.
- **FR-014**: The system MUST NOT make binding decisions about a student's
  individual academic record, financial aid, registration eligibility, conduct
  status, or graduation completion.
- **FR-015**: The system MUST provide a clear explanation of its limitation when
  it cannot answer and MUST preserve the student's ability to continue seeking
  help through the provided official destination.
- **FR-016**: The approved information scope MUST include, at minimum, the
  identified PNW webpages, academic catalog content, registrar schedules,
  university policies, student handbook content, and linked official documents
  relevant to the supported questions.
- **FR-017**: The system MUST exclude navigation elements, decorative content, and
  unrelated page material from the factual basis of an answer.
- **FR-018**: The system MUST present answers in clear, student-oriented language
  while preserving policy conditions and required qualifications.
- **FR-019**: The system MUST support validation using representative questions
  for parking, programs, registration errors, plans of study, prerequisites,
  deadlines, academic integrity, accessibility, and student services.
- **FR-020**: Requirements, source scope, answer behavior, and safe-failure
  behavior MUST remain reviewable before implementation begins.
- **FR-021**: The initial release MUST be a public web chatbot hosted on an
  official PNW page without requiring a separate student login or staff-only
  access flow.
- **FR-022**: For version 1, the system MAY use any public PNW page that appears
  official as a supported source, provided the answer preserves the page's
  scope, date, and authority context.
- **FR-023**: Within a single conversation, the system MUST retain relevant
  context such as campus, academic term, and topic scope for follow-up questions
  unless the user explicitly resets the conversation or asks a new topic that
  clearly changes scope.
- **FR-024**: The initial release MUST keep chat conversations ephemeral and MUST
  NOT retain transcripts for quality review or monitoring unless a later
  requirement explicitly adds approved retention controls.
- **FR-025**: The initial release MUST support official PNW web pages and linked
  official PDF or document sources when those documents contain the answerable
  policy, deadline, or procedure information.

### Key Entities

- **Student Question**: A natural-language request from an eligible student,
  including optional campus, term, program, course, or personal-context details.
- **Approved Source**: An official PNW webpage, catalog entry, policy, handbook,
  schedule, form, or linked document authorized for answering questions.
- **Source Passage**: A relevant factual section, table row, document section, or
  linked page extracted from an approved source.
- **Answer**: A student-facing response containing an explanation, applicable
  scope, supporting sources, and any necessary limitation or escalation.
- **Escalation Destination**: An official office, department, advisor, or contact
  responsible for questions the chatbot cannot reliably resolve.
- **Source Freshness Record**: Available publication, update, term, or review
  information used to assess whether a source may be current.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In evaluation with at least 50 representative questions, at least
  90% of answers that are supported by approved sources include a correct direct
  source link.
- **SC-002**: In evaluation with at least 50 representative questions, at least
  95% of answers avoid unsupported claims when the approved sources do not contain
  enough information.
- **SC-003**: At least 85% of student testers can locate a useful answer or an
  appropriate escalation destination within 3 minutes for common information
  questions.
- **SC-004**: At least 90% of evaluated answers preserve the correct campus,
  term, date, prerequisite, and condition details when those details apply.
- **SC-005**: At least 80% of student testers rate the answer clarity and source
  usefulness as satisfactory or better.
- **SC-006**: The Dean of Students office reports a measurable reduction in
  repetitive information requests for the supported topics during an agreed
  pilot period.
- **SC-007**: No release is accepted if evaluation identifies an answer that
  presents unsupported university policy as official information.

## Assumptions

- The initial audience is limited to currently registered undergraduate and
  graduate students; faculty, staff, applicants, alumni, and public visitors are
  outside the initial scope.
- The initial release targets a public web chat on an official PNW page; a student
  login or staff-only admin experience is not required for version 1 unless a
  later requirement adds authenticated access.
- Version 1 may use public PNW pages that appear official as supporting sources,
  with the requirement that the chatbot preserves the source's scope, date, and
  authority context rather than treating every webpage as equally authoritative.
- Version 1 supports official webpages and linked official PDF or document files,
  while keeping conversations ephemeral and resetting context only when the user
  asks a clearly new topic or explicitly resets the chat.
- Version 1 provides general university information and guided referrals; it does
  not replace advisors, offices, student portals, degree audits, or case-specific
  decisions.
- Approved sources will be identified and maintained by authorized university
  stakeholders, with a process for reporting stale or conflicting information.
- The initial source set includes the PNW pages and documents identified in the
  stakeholder corpus review, plus directly linked official content needed to
  answer supported questions.
- Campus-specific questions refer at least to Hammond and Westville when those
  distinctions appear in the source material.
- Answers can include direct links and contact information, but the chatbot does
  not submit forms, change schedules, pay tickets, or perform student-account
  actions.
- A student may need to confirm a policy with an office when source freshness,
  conflict, or personal circumstances make a general answer insufficient.
- Accessibility and privacy requirements applicable to the university context
  remain in force throughout the feature.
