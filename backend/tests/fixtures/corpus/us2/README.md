# US2 synthetic adverse-source corpus

This directory contains deterministic failure fixtures for User Story 2. Every document is
fictional, contains no student data, and is marked **SYNTHETIC TEST FIXTURE — NOT PNW POLICY**.
The reserved `/__test__/` URLs identify locally served fixtures; tests must never fetch them from
the public internet.

The corpus fixes the observation time at `2030-01-16T13:00:00Z` and covers these independent
conditions:

1. No matching source exists.
2. A formerly qualified source is stale and its qualification has expired.
3. A source is quarantined after failed provenance checks and contains an unverified contact.
4. An otherwise eligible source has failed extraction after a simulated fetch timeout.
5. Two current, equally applicable sources disagree and are joined by an unresolved conflict.
6. A current source supports a general personal-case boundary and one verified office referral.
7. Gemini quota, timeout, and malformed-response failures exhaust generation safely.

`manifest.json` lists the local source identities. `fixture-metadata.json` records stable IDs,
qualification state, applicability, conflicts, referrals, and bounded ingestion outcomes.
`expected-results.json` defines the required safe outcome for each scenario without prescribing
exact prose. `provider-failures.json` contains only bounded synthetic failure descriptors; it has
no credentials, provider payloads, prompts, or student content.

Stale, quarantined, and extraction-failed content must never support an answer or referral.
Conflicting sources may be linked from a limitation but must never establish either disputed
conclusion. The personal-case fixture may produce only its source-backed office contact and must
never claim that the chatbot inspected records or resolved the fictional error.
