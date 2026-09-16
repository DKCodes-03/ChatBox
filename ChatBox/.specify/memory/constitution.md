<!--
Sync Impact Report
- Version change: uninitialized template -> 1.0.0
- Modified principles: none; the prior file contained only unresolved placeholders
- Added principles: I. Grounded Answers; II. Fail Safely; III. Requirements Before
  Implementation; IV. Testable Changes; V. Maintainable and Transparent Engineering
- Added sections: Product Constraints; Development Workflow
- Removed sections: none
- Follow-up TODOs: RATIFICATION_DATE remains unknown and requires confirmation
-->

# ChatBox Constitution

## Core Principles

### I. Grounded Answers
All answers about university policies, rules, deadlines, and procedures MUST be
grounded in approved university information. The system MUST distinguish approved
sources from general guidance and MUST NOT present unsupported information as
official university policy. This protects users from treating generated content as
authoritative when it is not.

### II. Fail Safely
When the system lacks sufficient reliable information, it MUST clearly state that
it cannot provide a reliable answer. It MUST prefer acknowledging uncertainty,
saying "I don't know," or directing the user to an appropriate university office
over generating an unsupported answer. This reduces the risk of harmful decisions
based on fabricated or stale guidance.

### III. Requirements Before Implementation
Each feature MUST have clear, reviewable, and testable requirements before
implementation begins. Important ambiguities MUST be resolved by humans rather than
silently decided by AI. This ensures that implementation work can be evaluated
against an agreed outcome and that consequential product decisions remain
accountable.

### IV. Testable Changes
Changes MUST include tests or another documented, reviewable validation method
appropriate to their risk and surface area. User-facing behavior, source-grounding
rules, safe-failure behavior, and requirement-driven logic MUST have explicit
coverage. A change MUST NOT be considered complete when its expected behavior
cannot be verified.

### V. Maintainable and Transparent Engineering
Implementation MUST favor simple, readable designs that preserve existing behavior
unless a requirement explicitly changes it. Errors and uncertainty MUST be
surfaced through the established application interfaces rather than silently
discarded. Documentation and naming MUST make important assumptions, data sources,
and user-visible constraints discoverable.

## Product Constraints

The product MUST treat university information as a source-governance problem:
approved sources, source freshness, and the boundary between official policy and
general assistance MUST remain explicit. User-facing responses MUST avoid implying
official endorsement when the supporting information is unavailable, ambiguous, or
not approved.

Privacy, security, and accessibility requirements applicable to the project MUST
be preserved. New dependencies, external services, or data collection MUST be
justified in the feature requirements and reviewed for their effect on users and
operational support.

## Development Workflow

Before implementation, the feature record MUST identify its purpose, user-visible
behavior, acceptance criteria, information sources where applicable, and unresolved
decisions. Reviewers MUST be able to trace each material implementation choice to
those requirements.

Every change MUST receive review appropriate to its risk. Review MUST check
grounding and safe-failure behavior for policy-related features, requirements and
acceptance criteria for all features, and the relevant tests or validation evidence.
Changes that alter policy-answer behavior MUST identify the source and explain any
remaining uncertainty.

## Governance

This constitution governs product behavior and engineering decisions when more
specific project guidance conflicts with it. Amendments MUST be proposed in
writing, explain the affected principles and rationale, and be reviewed by the
project owners before they are adopted. The amendment MUST update the version,
last-amended date, and Sync Impact Report, and MUST preserve or explicitly migrate
existing requirements.

Versions use semantic versioning. A MAJOR increment is required for a
backward-incompatible removal or redefinition of a principle. A MINOR increment is
required for a new principle or materially expanded governance requirement. A PATCH
increment is used for clarifications, wording fixes, and other non-semantic
refinements.

Compliance MUST be reviewed during feature planning and code review. Reviewers
MUST block changes that present unsupported university policy as fact, fail to
handle insufficient information safely, or begin implementation without
reviewable and testable requirements. The constitution MUST be reconsidered when
the product, its information sources, or applicable university obligations change.

**Version**: 1.0.0 | **Ratified**: TODO(RATIFICATION_DATE): confirm original adoption date | **Last Amended**: 2026-09-14
