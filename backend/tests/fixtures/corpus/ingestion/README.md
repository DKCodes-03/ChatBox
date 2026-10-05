# Synthetic ingestion and source-authority corpus

Every file in this directory is fictional, contains no student data, and is marked
**SYNTHETIC TEST FIXTURE — NOT PNW POLICY**. Reserved `/__test__/ingestion/` URLs identify local
fixture responses. Tests must never request these URLs from the public internet.

The corpus provides deterministic inputs for the User Story 5 ingestion pipeline:

1. `linked-overview.html`, `linked-details.html`, and `linked-form.pdf` form a three-document
   procedure. Each linked document must become a separate source and qualify independently.
2. `malformed-table.html` preserves an uneven table while requiring quarantine with the
   `malformed_table` reason.
3. `duplicate-policy.html` and `duplicate-policy-repeat.html` are byte-identical responses for
   the same canonical source. A repeated fetch must reuse one immutable version.
4. `campus-hammond.html` and `campus-westville.html` carry contrasting campus applicability.
   Unknown campus scope must never be treated as applying to both.
5. `prerequisite-groups.html` contains one protected prerequisite expression with an AND branch,
   an OR branch, minimum grades, and a corequisite.
6. `stale-policy.html` has an expired qualification at the fixture observation time and must not
   be retrievable until a fresh automated qualification succeeds.
7. `conflict-eight.html` and `conflict-ten.html` are equally applicable sources with incompatible
   conclusions. Their unresolved conflict must block both conclusions.

`manifest.json` follows the operator source-import contract. `fixture-metadata.json` maps URLs to
local files and records stable IDs, timestamps, expected states, relationships, and bounded reason
codes. `expected-results.json` lists scenario-level outcomes for T060 and T061 without encoding
implementation-specific SQL or generated wording.

