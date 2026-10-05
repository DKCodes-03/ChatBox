"""Tests for reason-coded limitations and fail-closed referral assembly."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import pytest
from app.generation.safe_failure import (
    LIMITATIONS_BY_REASON,
    NO_VERIFIED_REFERRAL_LIMITATION,
    assemble_safe_failure,
)
from app.generation.schemas import AnswerOutcome, ReasonCode, SegmentKind
from app.retrieval.search import (
    EvidenceApplicability,
    RetrievalChannel,
    RetrievalScope,
    RetrievedEvidence,
)


@dataclass(frozen=True)
class ReferralCandidate:
    office_name: str
    responsibilities: str
    contact_label: str
    contact_url: str
    evidence_block_id: UUID
    scope: dict[str, object]


def _id(value: int) -> UUID:
    return UUID(f"10000000-0000-4000-8000-{value:012d}")


def _scope(*, campus: str | None = "Hammond") -> RetrievalScope:
    return RetrievalScope(
        topic="registration",
        institution="Purdue University Northwest",
        campus=campus,
    )


def _evidence(value: int = 1, *, campus: str | None = "Hammond") -> RetrievedEvidence:
    return RetrievedEvidence(
        evidence_id=_id(value),
        version_id=_id(100 + value),
        source_id=_id(200 + value),
        source_title="Synthetic registration help",
        canonical_url="https://www.pnw.edu/test/registration-help/",
        ordinal=value,
        heading_path=("Registration help",),
        page=None,
        anchor="contact",
        text="The Registrar helps with registration questions. Email registrar@example.edu.",
        structured_content={},
        topic_key="registration",
        scope={"campus": campus},
        applicability=EvidenceApplicability(
            topic="registration",
            institution="Purdue University Northwest",
            campus=campus,
            student_level=None,
            program=None,
            catalog_year=None,
            term=None,
            session=None,
        ),
        model_revision="fixture-minilm-v1",
        channel=RetrievalChannel.FULL_TEXT,
        rank=value,
        ranking_value=float(value),
    )


def _referral(
    evidence_id: UUID,
    *,
    office_name: str = "Office of the Registrar",
    contact_url: str = "mailto:registrar@example.edu",
    campus: str | None = "Hammond",
) -> ReferralCandidate:
    return ReferralCandidate(
        office_name=office_name,
        responsibilities="Registration questions",
        contact_label="Email the Registrar",
        contact_url=contact_url,
        evidence_block_id=evidence_id,
        scope={"campus": campus},
    )


def test_all_reason_codes_have_fixed_safe_limitations() -> None:
    assert set(LIMITATIONS_BY_REASON) == set(ReasonCode)

    for reason_code in ReasonCode:
        result = assemble_safe_failure(reason_code, evidence=(), scope=_scope())

        assert result.answer.outcome is AnswerOutcome.UNABLE
        assert result.answer.reason_code is reason_code
        assert result.answer.segments[0].kind is SegmentKind.LIMITATION
        assert result.answer.segments[0].text == LIMITATIONS_BY_REASON[reason_code]
        assert result.answer.segments[0].evidence_ids == ()
        assert result.answer.segments[1].text == NO_VERIFIED_REFERRAL_LIMITATION
        assert result.referral is None
        assert result.citations == ()
        assert result.related_citation_ids == ()
        assert result.citation_evidence_ids == ()


def test_eligible_scope_matched_referral_is_assembled_with_its_evidence() -> None:
    evidence = _evidence()

    result = assemble_safe_failure(
        ReasonCode.PERSONAL_CASE,
        evidence=(evidence,),
        scope=_scope(),
        referral_candidates=(_referral(evidence.evidence_id),),
    )

    assert result.answer.reason_code is ReasonCode.PERSONAL_CASE
    assert len(result.answer.segments) == 1
    assert result.referral is not None
    assert result.referral.office_name == "Office of the Registrar"
    assert result.referral.contact_url == "mailto:registrar@example.edu"
    assert result.referral.evidence_ids == (evidence.evidence_id,)
    assert tuple(citation.evidence_id for citation in result.citations) == (evidence.evidence_id,)
    assert result.related_citation_ids == ()
    assert result.citation_evidence_ids == (evidence.evidence_id,)


@pytest.mark.parametrize(
    ("candidate", "request_scope"),
    [
        (_referral(_id(99)), _scope()),
        (_referral(_id(1), campus="Westville"), _scope()),
        (_referral(_id(1), contact_url="http://example.edu/contact"), _scope()),
        (_referral(_id(1), contact_url="mailto:not-an-email"), _scope()),
    ],
)
def test_unverified_or_malformed_referrals_are_withheld(
    candidate: ReferralCandidate,
    request_scope: RetrievalScope,
) -> None:
    result = assemble_safe_failure(
        ReasonCode.MISSING_EVIDENCE,
        evidence=(_evidence(),),
        scope=request_scope,
        referral_candidates=(candidate,),
    )

    assert result.referral is None
    assert result.answer.segments[-1].text == NO_VERIFIED_REFERRAL_LIMITATION
    assert result.citation_evidence_ids == ()


def test_ambiguous_eligible_referrals_are_withheld_instead_of_selected() -> None:
    evidence = _evidence()
    result = assemble_safe_failure(
        ReasonCode.PERSONAL_CASE,
        evidence=(evidence,),
        scope=_scope(),
        referral_candidates=(
            _referral(evidence.evidence_id),
            _referral(
                evidence.evidence_id,
                office_name="Student Services",
                contact_url="https://www.pnw.edu/student-services/",
            ),
        ),
    )

    assert result.referral is None
    assert result.answer.segments[-1].text == NO_VERIFIED_REFERRAL_LIMITATION


def test_conflict_cites_only_scope_matched_eligible_evidence() -> None:
    matched = _evidence(1)
    wrong_campus = _evidence(2, campus="Westville")
    result = assemble_safe_failure(
        ReasonCode.CONFLICT,
        evidence=(matched, wrong_campus),
        scope=_scope(),
        related_evidence_ids=(matched.evidence_id, wrong_campus.evidence_id, _id(99)),
    )

    assert result.answer.reason_code is ReasonCode.CONFLICT
    assert result.related_citation_ids == (matched.evidence_id,)
    assert tuple(citation.evidence_id for citation in result.citations) == (matched.evidence_id,)
    assert result.citation_evidence_ids == (matched.evidence_id,)


def test_referral_scope_cannot_treat_unknown_request_context_as_universal() -> None:
    evidence = _evidence(campus=None)
    result = assemble_safe_failure(
        ReasonCode.MISSING_EVIDENCE,
        evidence=(evidence,),
        scope=_scope(campus=None),
        referral_candidates=(_referral(evidence.evidence_id, campus="Hammond"),),
    )

    assert result.referral is None
    assert result.answer.segments[-1].text == NO_VERIFIED_REFERRAL_LIMITATION
