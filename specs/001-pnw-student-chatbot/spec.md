# Feature Specification: PNW Student Information Chatbot

**Feature Branch**: `main` (existing branch; no branch hook configured)

**Created**: 2026-09-14

**Status**: Clarified — ready for planning

**Input**: Build a Purdue University Northwest chatbot for students, based on interviews with
Dean of Students staff and students and an initial review of university information sources.
Students need direct, reliable answers without searching through many webpages; staff need
fewer repetitive inquiries. Incorrect policy information is unacceptable.

## Clarifications

### Session 2026-09-16

- Q: Which topics should the chatbot cover in its first release? → A: All identified topics,
  limited to general information; personal record access, transactions, and individual degree
  planning are excluded.

- Q: Who should approve the chatbot’s university sources and resolve conflicting information?
  → A: The Dean of Students coordinates approval; responsible university offices approve
  information within their areas and resolve conflicts.

- Q: What rule should determine when approved university information needs review before the
  chatbot can keep using it? → A: Responsible offices set a review/expiry date for each source;
  deadlines require term-specific approval. Expired sources are withheld until approval is renewed.

- Q: May the chatbot retain students’ questions and answers after a conversation ends?
  → A: Conversation content is retained only during the active session and deleted when it
  ends. Only aggregate usage and performance statistics without message text or student
  identifiers may be retained afterward.

- Q: When should a conversation session end and its content be deleted?
  → A: When the student selects “End chat,” or after 30 minutes without a student message,
  whichever comes first.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get a supported answer and next steps (Priority: P1)

As a student, I want to ask a university question in ordinary language and receive a clear
answer with official supporting sources so I can understand what to do next.

**Why this priority**: Direct, grounded answers address the primary student and sponsor need.

**Independent Test**: Provide approved information for one supported procedure, ask the question,
and compare the explanation, steps, qualifications, and source links with that information.

**Acceptance Scenarios**:

1. **Given** approved applicable sources contain a complete procedure, **When** a student asks
   how to complete it, **Then** the chatbot gives a plain-language answer and ordered steps,
   including required approvals and deadlines where supported, with links to supporting sources.
2. **Given** the answer spans an approved main page, linked page, and PDF, **When** the student
   asks the question, **Then** the chatbot combines supported information into one explanation
   and identifies the supporting documents instead of returning only a list of links.
3. **Given** approved information explicitly establishes whether a program exists for a stated
   catalog context, **When** asked about that program, **Then** the chatbot answers directly;
   absence from incomplete search results alone does not justify answering that it does not exist.
4. **Given** a question asks for several facts and only some are supported, **When** answered,
   **Then** supported facts are distinguished from unanswered parts and the missing parts receive
   the safe fallback from Story 2.

### User Story 2 - Receive an honest limitation and useful referral (Priority: P1)

As a student, I want the chatbot to say when it cannot reliably answer and identify an
appropriate office when known so I do not act on invented guidance.

**Why this priority**: Safe failure is a constitutional requirement and essential to trust.

**Independent Test**: Ask questions using missing, unapproved, expired, and conflicting source
fixtures and verify that no unsupported official answer or contact is supplied.

**Acceptance Scenarios**:

1. **Given** no sufficient reliable source exists, **When** a student asks a question, **Then**
   the chatbot clearly says it cannot provide a reliable answer and does not guess.
2. **Given** equally applicable approved sources disagree and no approved resolution exists,
   **When** asked about the disputed fact, **Then** the chatbot explains the uncertainty,
   withholds a definitive answer, and links to the conflicting sources when available.
3. **Given** an approved source identifies the responsible office, **When** the chatbot cannot
   answer reliably, **Then** it provides that office and the supported contact route; if no
   verified referral exists, it says so rather than inventing one.
4. **Given** a registration error requires personal records or staff action, **When** the student
   describes it, **Then** the chatbot offers only supported general guidance, explains its limit,
   and refers appropriately without claiming to inspect records or resolve the error.

### User Story 3 - Get information for the right context (Priority: P1)

As a student, I want answers that apply to my campus, term, program, and student level so I do
not follow a deadline or requirement meant for someone else.

**Why this priority**: Interviews identify campus confusion, obsolete information, and
fragmented course requirements as causes of incorrect decisions.

**Independent Test**: Supply contrasting campus, term, and catalog fixtures and verify context
questions and answer selection without requiring personal student records.

**Acceptance Scenarios**:

1. **Given** campus changes an answer and is unknown, **When** asked about availability or a
   procedure, **Then** the chatbot asks for campus before giving the affected answer.
2. **Given** a deadline depends on term, year, session, or course duration, **When** required
   context is missing, **Then** the chatbot asks for it and does not silently choose a term.
3. **Given** an approved schedule table contains distinct add/drop dates and refund percentages,
   **When** a student supplies context, **Then** the answer preserves the correct row, headings,
   conditions, and date-to-percentage relationships and labels the applicable period.
4. **Given** a student corrects the campus or term in a follow-up, **When** answering again,
   **Then** the chatbot uses the corrected context and rechecks which information applies.
5. **Given** a relevant deadline has passed, **When** it is reported, **Then** it is clearly
   labeled as past; no later deadline or exception is invented.

### User Story 4 - Understand general academic requirements (Priority: P2)

As a student, I want understandable explanations of published academic requirements and
procedures so I can prepare for a conversation with my advisor.

**Why this priority**: Students described difficulty with program discovery, prerequisites,
plans of study, and graduation procedures. These topics are included in the first release.

**Independent Test**: Use an approved catalog and procedure fixture to ask about prerequisites
or a plan of study; compare the explanation with source conditions and scope.

**Acceptance Scenarios**:

1. **Given** approved prerequisite information is spread across course entries, **When** asked
   for prerequisites, **Then** the chatbot summarizes the published relationships and preserves
   alternatives, minimum grades, concurrent enrollment rules, and catalog applicability.
2. **Given** approved instructions describe plan-of-study or graduation submission, **When**
   asked how to proceed, **Then** the chatbot explains supported steps and approvals without
   deciding the student's personal course eligibility or graduation status.
3. **Given** a catalog describes typical offerings but not current availability, **When** asked
   whether a course is available now, **Then** the chatbot does not equate the catalog entry
   with confirmed availability and provides a supported referral or limitation.

### User Story 5 - Keep answer sources accountable (Priority: P1)

As a university content owner, I want only approved, applicable information used in answers
so students receive guidance whose authority and review status can be checked.

**Why this priority**: Grounding requires an explicit source authority and lifecycle.

**Independent Test**: Review source records and change one from approved to withdrawn; verify
that subsequent answers do not rely on it as authority. No particular administrative interface
is required by this story.

**Acceptance Scenarios**:

1. **Given** a candidate source, **When** approval by the responsible university office is
   recorded through coordination with the Dean of Students,
   **Then** its approved scope, version, owner, and review validity are available for review.
2. **Given** a source is withdrawn, past its recorded approval expiry, or changed without renewed approval,
   **When** a new answer is requested, **Then** the affected information is not used as approved
   authority; other valid evidence may support the answer or the chatbot fails safely.
3. **Given** linked material is needed to complete an answer, **When** that material has not
   been approved, **Then** approval of the parent page does not automatically authorize it.

### Edge Cases

- A source is unavailable, a PDF is unreadable, or an expandable section cannot be accessed:
  withhold unsupported details and explain the limitation; never fill missing text by guessing.
- A page contains navigation, sharing controls, duplicate content, or instructions directed at
  the chatbot: these are not evidence of university policy and cannot override answer rules.
- A source applies only to concurrent enrollment, graduate students, or an older catalog:
  do not generalize it to all PNW students or treat it as current without applicable approval.
- A question refers to another Purdue institution: clarify the institution rather than silently
  applying its information to PNW.
- A prerequisite chain is incomplete or circular: identify the gap and do not claim completeness.
- A student cannot provide necessary context: explain which part cannot be answered reliably
  and offer a verified referral where available.
- A student requests a payment, registration change, appeal submission, PIN, account status,
  room change confirmation, or personal degree audit: explain the informational boundary.
- A student supplies a personal identifier: do not request more identifiers or repeat it in the
  answer; public guidance must not require access to personal university records.
- A student returns after inactivity expiry: start a new conversation, explain that the prior
  session ended, and request any context needed for the new question without restoring old content.
- A question is outside supported topics or asks the chatbot to ignore sources: state the scope
  or evidence limitation without fabricating university guidance.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Students MUST be able to ask plain-language questions and follow-up questions in
  one conversation. Answers MUST use explicit relevant context from that conversation and
  accept corrections. Acceptance: Stories 1 and 3.
- **FR-002**: Supported answers MUST directly explain the requested information in plain
  language, with ordered steps for procedures and without omitting material exceptions or
  conditions. Acceptance: Story 1 scenarios 1–2 and Story 4.
- **FR-003**: Every university policy, rule, deadline, or procedural claim MUST be supported by
  approved applicable evidence and accompanied by a source title and link. Sources MUST be
  associated with the claims they support; section or page references MUST be included where
  available. Acceptance: compare every factual claim in Stories 1, 3, and 4 with its evidence.
- **FR-004**: The chatbot MUST clearly decline unsupported answers, distinguish supported parts
  from unknown parts, and never infer nonexistence solely from missing search results.
  Acceptance: Story 1 scenarios 3–4 and Story 2 scenarios 1–2.
- **FR-005**: Referrals MUST identify an appropriate office and contact route only when supported
  by approved information. Acceptance: Story 2 scenarios 3–4, including missing-contact tests.
- **FR-006**: The chatbot MUST ask for missing campus, term/year, session, program, student level,
  or catalog context when that context changes the answer; answers MUST state applicable
  context. Acceptance: Story 3 and the population-specific edge case.
- **FR-007**: Dates MUST retain their associated event, term, session, conditions, and any stated
  time zone. Past dates MUST be labeled; unknown date applicability MUST trigger clarification
  or safe failure. Acceptance: Story 3 scenarios 2–5.
- **FR-008**: Answers MUST preserve meaning across approved linked pages, PDFs, tables, and
  expandable content, including prerequisite relationships and table headings. Navigation and
  duplicate text MUST NOT be treated as additional policy evidence. Acceptance: Story 1
  scenario 2, Story 3 scenario 3, Story 4 scenario 1, and source-access edge cases.
- **FR-009**: The chatbot MUST distinguish published general guidance from personal eligibility,
  account status, and staff decisions. It MUST NOT claim to execute transactions or access
  student records. Acceptance: Story 2 scenario 4, Story 4 scenarios 2–3, and transaction tests.
- **FR-010**: Each usable source MUST have a recorded identity/link, version or content reference,
  approving owner, approved audience/topic scope, applicable period where relevant, approval
  status, and review validity. Withdrawn or expired sources MUST NOT support subsequent
  answers. The Dean of Students MUST coordinate source approval. Responsible university
  offices MUST approve information within their areas and resolve conflicts; unresolved
  conflicts MUST continue to trigger safe failure. Acceptance: Story 5; approval records
  identify the responsible office, and a conflicting claim remains withheld until the
  responsible offices record a resolution coordinated by the Dean of Students.
- **FR-011**: The responsible university office MUST set a review/expiry date for each approved
  source. Approval MUST expire at the recorded expiry unless the office renews it; expired
  sources MUST be withheld from subsequent answers until renewal is recorded. Deadlines MUST
  have explicit approval for the applicable term. Missing expiry dates, missing term-specific
  deadline approval, unresolved conflicts, and unconfirmed applicability MUST cause safe
  failure for affected claims. Acceptance: Stories 2, 3, and 5, including checks immediately
  before and at expiry, after renewal, and with absent or mismatched term approval.
- **FR-012**: The first release MUST support general information on add/drop,
  registration and general error guidance, academic standing, grade appeals, financial aid
  deadlines, office contacts, parking payment/appeal guidance, programs, prerequisites, plans
  of study, graduation, graduate admissions, accessibility services, academic integrity,
  classroom behavior, and information-services policies. Approval is required for answer
  evidence in every topic. Acceptance: each listed topic has a supported-answer and
  unsupported-answer case; topics outside this list receive a scope limitation.
- **FR-013**: Before implementation, human decisions on scope, source authority, and freshness
  MUST be recorded and reflected in acceptance cases. Before acceptance, reviewers MUST record constitutional compliance and
  validation evidence. Acceptance: document review finds no unresolved material decision and
  maps each requirement to the applicable scenario or review evidence.

- **FR-014**: Conversation content MUST be retained only during the active session and deleted
  when the session ends. Students MUST have an “End chat” action. A session MUST end on that
  action or after 30 minutes without a student message, whichever comes first. Each student
  message resets the inactivity period; chatbot replies and background activity MUST NOT
  reset it. After expiry, a new message MUST start a new conversation without prior context.
  Retained usage and performance statistics MUST be aggregate and
  contain neither message text nor student identifiers. This restriction MUST apply to
  conversation storage, logs, and any service processing conversations on the chatbot's behalf.
  Acceptance: after a session ends, its questions, answers, and conversational context are
  unavailable for restoration or staff review; retained statistics and logs contain no
  conversation content or student identifiers. Verify both explicit termination and inactivity
  expiry, including a message before the timeout and one at or after the timeout.

### Key Entities *(include if feature involves data)*

- **Student question and context**: Requested topic, explicit campus, term/session, program,
  student level, catalog context, and follow-up corrections; not a personal student record.
  This conversation content exists only during the active session and is deleted when it ends.
- **University source**: Candidate or approved document with location, content/version reference,
  responsible approving office, approval status, applicability, review/expiry date,
  renewal record, and term-specific deadline approval where relevant.
- **Supporting evidence**: Specific passage, table relationship, or document section supporting
  a claim; linked to its source and applicability.
- **Answer**: Direct explanation, supported claims, source references, context, limitations,
  and optional verified next steps or referral.
- **Office referral**: Office name, responsibilities, contact route, and approving source.
- **Human decision**: Scope, authority, freshness, or conflict resolution with decision maker,
  decision date, rationale, and affected requirements or sources.

## Success Criteria *(mandatory)*

### Measurable Outcomes

Targets below are proposed acceptance targets, not measured results. Use a human-reviewed
set of at least 60 questions with at least two answerable cases per selected topic and at
least 20 combined missing-evidence, conflict, outdated-source, wrong-context, and personal-case
questions. Reviewers establish expected answers and permitted sources before evaluation.

- **SC-001**: 100% of official claims in evaluated answers have supporting approved evidence
  applicable to the question; zero fabricated policies, dates, requirements, or contacts occur.
- **SC-002**: 100% of evaluated insufficient-evidence or unresolved-conflict cases clearly
  withhold the unsupported conclusion; every referral provided has verified support.
- **SC-003**: At least 90% of answerable evaluation questions receive a correct direct answer
  containing all required steps and conditions, rather than only links or unnecessary refusal.
- **SC-004**: 100% of context-sensitive evaluation cases request missing necessary context or
  use supplied context correctly, with no cross-term, campus, or audience substitution.
- **SC-005**: In a usability evaluation with at least 10 students completing three representative
  tasks each, at least 80% of tasks reach a correct answer or appropriate verified referral
  within two minutes, including clarification time; at least 80% of students rate clarity
  at least 4 out of 5. At least two tasks per student MUST be answerable from approved sources.
- **SC-006**: At least 95% of evaluation questions receive an answer, clarification request, or
  explicit limitation within 10 seconds after submission, excluding student response time.
- **SC-007**: 100% of withdrawal, expiry, and unapproved-source evaluation cases prevent use of
  the affected source as authority in subsequent answers.

- **SC-008**: In 100% of session-end retention checks, conversation content cannot be restored
  or reviewed after the session ends, and retained statistics contain no message text or
  student identifiers. Checks MUST cover “End chat,” expiry after 30 minutes without a student
  message, inactivity reset by student messages only, and a new conversation after expiry.
  Validation MUST include logs and services processing conversations.

## Assumptions

- This is an informational chatbot for PNW students. Sponsor: Dean of Students Office; Jane is
  the interviewed student service coordinator, not an assumed source approver.
- General guidance across all topics listed in FR-012 is the confirmed first-release boundary.
  Student record integration, transactions, individual scheduling or degree audits, live room
  changes, prerequisite visualizations, and replacement of advisor decisions are outside this
  feature.
- Initial interaction is English text with source links. No account creation or student-portal
  integration is required. Conversation content MUST NOT be saved beyond the active session;
  only aggregate usage and performance statistics without message text or student identifiers
  may be retained. Changes to this retention boundary require human-reviewed requirements.
- Corpus review observations are discovery evidence, not proof that a source is approved,
  complete, current, or applicable to all students. Linked documents need their own approval.
- Availability of university content owners, approved source material, verified office contacts,
  and human-reviewed evaluation cases are dependencies. The Dean of Students coordinates source
  approval with responsible university offices. Each office sets source review/expiry dates
  and explicitly approves deadlines for the applicable term. No source is approved merely
  by appearing in the list below.
- Speed and usability targets are proposed defaults. Reducing repetitive staff questions is a
  business objective; no percentage reduction is claimed without a staff inquiry baseline.
- Implementation choices for collecting and representing information are deferred to planning.
  The corpus review's suggested techniques are expressed here as information-fidelity outcomes.

### Candidate Corpus and Discovery Evidence

The interview and corpus review supplied by the user are the primary requirements evidence.
The following links retain the candidate source inventory. Web access was attempted on
2026-09-14; access success does not constitute approval or currency verification. Both catalog
links could not be retrieved during this check; their structural observations remain user-reported.

| Candidate source | Required review focus |
| --- | --- |
| [Parking regulations](https://www.pnw.edu/getting-to-pnw/parking-and-fees/regulations-and-enforcement/) | Complete procedures across linked resources; campus applicability |
| [Catalog program](https://catalog.pnw.edu/preview_program.php?catoid=5&poid=1338&returnto=271) | Catalog version, program scope, prerequisites, campus and offering distinctions |
| [Academic schedule](https://www.pnw.edu/registrar/academic-schedule/) | Term/session context and preserved deadline/table relationships |
| [Graduate admissions catalog](https://catalog.pnw.edu/content.php?catoid=4&navoid=154#admission-to-the-graduate-school) | Catalog applicability, linked requirements, student population |
| [Accessibility at PNW](https://www.pnw.edu/accessibility-at-purdue-university-northwest/) | Linked service details and verified contacts |
| [Academic integrity](https://www.pnw.edu/dean-of-students/policies/academic-integrity-policy/) | Complete policy sections, conditions, and linked details |
| [Student handbook PDF](https://www.pnw.edu/concurrent-enrollment/wp-content/uploads/sites/4/2020/02/STUDENT-HANDBOOK.pdf) | Confirm audience and currency; do not assume university-wide applicability |
| [Classroom behavior PDF](https://www.pnw.edu/faculty-senate/wp-content/uploads/sites/71/2021/01/Classroom-Behavior-Policy.pdf) | Version, headings, numbered requirements, and applicability |
| [Information Services policies](https://www.pnw.edu/information-services/policies/) | Approved linked policies and complete supporting content |
