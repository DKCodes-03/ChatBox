# US1 synthetic multi-document procedure

This directory contains a deterministic corpus for User Story 1. Every document is fictional,
contains no student data, and is marked **SYNTHETIC TEST FIXTURE — NOT PNW POLICY**. The reserved
`/__test__/` URLs are identifiers for local fixture serving; tests must never fetch them from the
public internet.

The procedure is deliberately split across three independently qualifying sources:

1. `procedure-overview.html` defines scope, required materials, and links to the other sources.
2. `eligibility-and-steps.html` supplies steps 1–2 and a material condition.
3. `replacement-form.pdf` supplies steps 3–5, including the final submission condition.

`manifest.json` follows the source-import contract. `fixture-metadata.json` maps those canonical
URLs to local files and records stable source/version/evidence IDs, source relationships,
qualification expectations, and citation anchors. `expected-answer.json` defines the supported
and partial-answer assertions intended for T031 without prescribing exact generated wording.

The complete supported answer must use all three documents. The partial case intentionally omits
the PDF from its eligible evidence set; it may report supported prerequisites and steps 1–2, but
must withhold steps 3–5 and the completion claim.

