# US4 synthetic academic-guidance corpus

This directory contains deterministic fixtures for User Story 4. Every document is fictional,
contains no student data, and is marked **SYNTHETIC TEST FIXTURE — NOT PNW POLICY**. Reserved
`/__test__/` URLs identify local fixtures and must never be fetched from the public internet.

The corpus contains seven independent conditions:

1. An explicit 2030-2031 catalog entry establishing that a fictional graduate program exists.
2. Graduate-admission steps scoped to graduate students and the 2030-2031 catalog.
3. A prerequisite expression requiring either `(SYN 21000 with B AND SYN 22000 with C)` or
   `SYN 23000 with B`, plus concurrently permitted corequisite `SYN 35001`.
4. Ordered plan-of-study steps with an advisor-approval boundary.
5. Ordered graduation-application steps that do not decide personal graduation status.
6. An intentionally incomplete catalog inventory that cannot support a nonexistence claim.
7. A typical-offering statement that cannot confirm current course availability and instead
   points to a source-backed synthetic schedule route.

`manifest.json` lists the reserved source identities. `fixture-metadata.json` records stable IDs,
qualification and extraction states, applicability, structured academic profiles, evidence,
course relationships, and the one verified schedule referral. `expected-results.json` defines
the claims, scope, boundaries, and limitations that T047 will verify without prescribing exact
generated wording.

