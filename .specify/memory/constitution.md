<!--
Sync Impact Report
Version change: unratified template → 1.0.0 (initial adoption)
Modified principles:
- Placeholder principle 1 → I. Grounded Answers
- Placeholder principle 2 → II. Fail Safely
- Placeholder principle 3 → III. Requirements Before Implementation
Added sections: Information Authority; Requirements and Compliance Review; Governance
Removed sections: unused placeholder principles 4 and 5
Follow-up TODOs: none. Each relevant feature must define its approved information sources
and resolve material ambiguities with a human before implementation.
-->
# ChatBox Constitution

## Core Principles

### I. Grounded Answers

All answers about university policies, rules, deadlines, and procedures MUST be grounded in
approved university information. The system MUST NOT present unsupported information as
official university policy. Claims presented as official MUST be traceable to approved
information that supports them. This protects users from acting on invented university guidance.

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

Requirements for features that answer university questions MUST identify the approved
university information they may use and how that approval is established. If source approval
or applicability is unclear, a human MUST resolve it before the source is treated as authority.
Insufficient, conflicting, or potentially outdated information MUST trigger the fail-safe rule
whenever a reliable answer cannot be established.

## Requirements and Compliance Review

Before implementation, each feature specification MUST include acceptance criteria that can
be checked through tests or review. For university-answering features, those criteria MUST
cover supported answers, insufficient reliable information, and prevention of unsupported
claims presented as official policy.

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

**Version**: 1.0.0 | **Ratified**: 2026-09-14 | **Last Amended**: 2026-09-14
