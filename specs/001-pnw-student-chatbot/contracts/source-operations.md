# Source Operations Contract

A controlled operator CLI configures ingestion and inspects automated qualification. No public
administrative endpoints and no per-document human review, office sign-off or approval commands.
Operator credentials are isolated from the public API. Qualification runs automatically after
fetching and extraction; CLI invocation is not required for each document.

```text
python -m app.cli sources import --manifest /work/corpus.json
python -m app.cli sources fetch --source-id UUID
python -m app.cli sources qualify --source-id UUID
python -m app.cli sources inspect --source-id UUID
python -m app.cli sources withdraw --source-id UUID --reason CODE
python -m app.cli sources restore --source-id UUID
python -m app.cli sources refresh-due
python -m app.cli evaluate --cases /work/evaluation.json --output /work/report.json
```

Manifest: `sources[]` containing `{url, title, topics, allowed_child_hosts}`. Initial boundaries
are the supplied corpus and bounded public links on www.pnw.edu, pnw.edu and catalog.pnw.edu.
Matching the host is necessary but insufficient. Import creates candidates; every child document
must qualify independently. Other hosts remain ineligible until source requirements change.

Fetch checks every redirect and resolved address against private/reserved-network exclusions,
limits traversal to 3 levels, 100 URLs/run and 20 MiB/document, and accepts public HTML/PDF only.
No authenticated student portals or student-provided URL fetching. Inaccessible expanded content,
unreadable scans or incomplete tables are quarantined with machine-readable reasons.

Qualification records `{version_id, rule_version, checked_at, valid_until, check_results,
provenance_evidence, applicability_evidence}`. Checks require official provenance, complete
supporting content, source-backed scope, applicable catalog/term/effective dates and no unresolved
conflict. Unknown metadata is not a universal wildcard. Deadlines need term/session evidence,
not office sign-off. Passing checks makes evidence eligible automatically.

Daily refresh rechecks sources. `valid_until` is at most checked_at + 24 hours and no later than
known applicability end. An unsuccessful refresh invalidates use immediately; a successful fetch
alone cannot renew qualification. Material changes replace eligibility only after full checks.
Query-time checks enforce staleness even if the scheduler is down. Explicit source-backed
supersession may resolve a conflict; dates of fetch and retrieval rank may not.

Withdrawal is an optional operator safeguard that invalidates eligibility transactionally.
Restore removes that override but requires fresh automated qualification before use. Repeated
fetches of identical content are idempotent; qualification history retains rule/result evidence.
A source restore is not an approval or an override of failed checks.

JSON output: `{status, source_id, version_id, qualification_id, reason_codes}` as applicable.
Exit codes: 0 success; 2 invalid input; 3 unauthorized operator; 4 eligibility/precondition failure;
5 dependency failure. Never print secrets or student data. Evaluation accepts synthetic or
human-authored nonpersonal cases; student transcript imports are prohibited.
