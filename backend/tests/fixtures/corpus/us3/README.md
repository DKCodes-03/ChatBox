# US3 synthetic context and relationship corpus

This directory contains deterministic fixtures for User Story 3. Every document is fictional,
contains no student data, and is marked **SYNTHETIC TEST FIXTURE — NOT PNW POLICY**. Reserved
`/__test__/` URLs identify local fixtures and must never be fetched from the public internet.

The schedule corpus intentionally varies one or more context dimensions:

1. Hammond, Fall 2030, Full Term.
2. Hammond, Fall 2030, First 8 Weeks.
3. Westville, Fall 2030, Full Term.
4. Hammond, Spring 2031, Full Term.

Each schedule is a complete HTML table with event, deadline, refund percentage, conditions, and
time-zone columns. A row remains one semantic unit with its headings and footnote. Tests use the
fixed observation time `2030-09-20T12:00:00Z`, making selected Fall 2030 dates deterministically
past while later dates remain future.

The catalog fixture defines the fictional `SYN 25000` prerequisite as either both `SYN 11000`
and `SYN 12000`, or `SYN 13000`; all prerequisite alternatives require a minimum grade of C. It
also requires the corequisite `SYN 25001`. The relationship applies to the Hammond campus and the
2030-2031 catalog. It describes published requirements only and never claims personal eligibility
or current course availability.

`manifest.json` lists source identities. `fixture-metadata.json` records stable source, version,
qualification, evidence, applicability, table, and course-relation data. `expected-results.json`
defines context, correction, deadline, refund-row, and prerequisite expectations for T041 without
prescribing generated wording.

