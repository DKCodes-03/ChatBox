<!--
Sync Impact Report
Version change: 1.0.0 → 2.0.0
Rationale: replaces mandatory human source authorization with rule-based source eligibility
for the initial version, an incompatible redefinition of information-authority governance.
Modified principles:
- I. Grounded Answers: approved information now explicitly includes rule-qualified sources.
- II. Fail Safely: unchanged; referrals remain subject to the same source eligibility rules.
- III. Requirements Before Implementation: unchanged.
Modified sections: Information Authority; Requirements and Compliance Review
Added sections: none
Removed sections: none
Follow-up alignment required (not modified by this constitution command):
- specs/001-pnw-student-chatbot/spec.md: supersede per-document office approval, renewal,
  term-specific sign-off and human conflict-resolution gates with documented eligibility,
  applicability and freshness rules; preserve fail-safe behavior for unresolved conflicts.
- specs/001-pnw-student-chatbot/plan.md, research.md, data-model.md, contracts/ and quickstart.md:
  align approval workflows, source states, release gates and validation with the revised spec.
- Revalidate specs/001-pnw-student-chatbot/checklists/requirements.md after spec alignment.
Deferred placeholders: none.
-->
# ChatBox Constitution

## Core Principles

### I. Grounded Answers

All answers about university policies, rules, deadlines, and procedures MUST be grounded in
approved university information. The system MUST NOT present unsupported information as
official university policy. Claims presented as official MUST be traceable to approved
information that supports them. For the initial version, approved information means information
from sources meeting the documented eligibility rules in Information Authority; it does not
require explicit human review or sign-off of each ingested document. This protects users from
acting on invented university guidance while allowing automated source qualification.

### II. Fail Safely

When the system lacks sufficient reliable information, it MUST clearly state that it cannot
provide a reliable answer. It MUST prefer acknowledging that it does not know or directing the
user to an appropriate university office over generating an unsupported answer. Any office
referral MUST itself be grounded in approved information; the system MUST NOT invent contacts
or procedures to fill an information gap.

### III. Requirements Before Implementation

Each feature MUST have clear, reviewable, and testable requirements before implementation
begins. Important ambiguities MUST be resolved by humans rather than silently decided by AI.
An ambiguity is important when its resolution changes expected behavior, information authority,
user outcomes, or acceptance criteria. Such ambiguities MUST be recorded and the affected
implementation MUST wait for a documented human decision.

## Information Authority

Requirements for features that answer university questions MUST define testable rules for
eligible official university sources, including source provenance, scope and applicability,
content completeness, and freshness. The initial version MUST allow documents that satisfy
these rules to be ingested and used without explicit human document review, office sign-off,
or per-document approval or renewal. Document and deadline applicability MUST be established
by the documented rules rather than mandatory human sign-off.

The system MUST record source provenance and the evidence used to determine eligibility.
Ingestion alone MUST NOT establish authority: a document must satisfy the eligibility rules
before it supports an official claim. When eligibility or applicability cannot be established,
the system MUST withhold the affected claim and follow the fail-safe rule. Insufficient,
unresolved conflicting, or potentially outdated information MUST trigger the same behavior
whenever a reliable answer cannot be established; human review is not required to return a
safe limitation or a supported referral.

Important ambiguities in the eligibility requirements themselves remain subject to Principle
III. Removing document review does not authorize AI to silently invent source-selection rules.

## Requirements and Compliance Review

Before implementation, each feature specification MUST include acceptance criteria that can
be checked through tests or review. For university-answering features, those criteria MUST
cover supported answers, insufficient reliable information, and prevention of unsupported
claims presented as official policy. Source qualification checks MAY be automated; acceptance
MUST NOT depend on explicit human review of each ingested document in the initial version.

Reviewers MUST check requirements for unresolved important ambiguities before implementation.
Before a change is accepted, review MUST verify compliance with all applicable principles and
record the relevant validation evidence. Violations MUST be corrected before acceptance.

## Governance

This constitution governs project specifications, plans, implementation, and reviews.
Conflicting project guidance MUST be brought into alignment with these principles.

Amendments MUST document the proposed change, its rationale, and its impact on existing work.
A human project maintainer MUST approve amendments. Each adopted amendment MUST update this
file, its Sync Impact Report, its version, and its last-amended date. Any necessary follow-up
alignment work MUST be recorded.

Versions MUST follow semantic versioning: MAJOR for incompatible principle removals or
redefinitions, MINOR for new principles or materially expanded guidance, and PATCH for
clarifications or wording corrections that do not change obligations. Compliance MUST be
reviewed during requirements review and before changes are accepted.

**Version**: 2.0.0 | **Ratified**: 2026-09-14 | **Last Amended**: 2026-09-16
