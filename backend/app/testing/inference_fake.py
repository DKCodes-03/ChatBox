"""Deterministic OpenAI-compatible inference service for synthetic browser tests."""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import Body, FastAPI, HTTPException, Query

from app.generation.schemas import (
    AnswerOutcome,
    AnswerSegment,
    ReasonCode,
    SegmentKind,
    StructuredAnswer,
)

app = FastAPI(title="Synthetic test inference fake", docs_url=None, redoc_url=None)

EVIDENCE_IDS = tuple(f"30000000-0000-4000-8000-{suffix:012d}" for suffix in range(1, 7))
CONTEXT_EVIDENCE_ANSWERS = {
    "32000000-0000-4000-8000-000000000001": (
        "The 80 percent refund deadline is September 13, 2030 at 5:00 p.m. Central Time."
    ),
    "32000000-0000-4000-8000-000000000003": (
        "The 80 percent refund deadline is September 15, 2030 at 5:00 p.m. Central Time."
    ),
}
ACADEMIC_EVIDENCE_IDS = {
    "program": "34000000-0000-4000-8000-000000000001",
    "graduate_admission": "34000000-0000-4000-8000-000000000002",
    "prerequisites": "34000000-0000-4000-8000-000000000003",
    "plan_of_study": "34000000-0000-4000-8000-000000000004",
    "graduation": "34000000-0000-4000-8000-000000000005",
}


def _answer() -> StructuredAnswer:
    evidence_ids = tuple(UUID(value) for value in EVIDENCE_IDS)
    return StructuredAnswer(
        outcome=AnswerOutcome.ANSWER,
        segments=(
            AnswerSegment(
                kind=SegmentKind.EXPLANATION,
                text=(
                    "This exercise applies only to a fictional permit printed with both the "
                    "words TEST ONLY and the blue-lantern symbol. Continue only when the "
                    "fictional placard displays both TEST ONLY and a blue-lantern symbol."
                ),
                evidence_ids=(evidence_ids[0], evidence_ids[2]),
            ),
            AnswerSegment(
                kind=SegmentKind.EXPLANATION,
                text="The exercise is complete only after all five supported steps are finished.",
                evidence_ids=(evidence_ids[1],),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Verify that both required marks appear on the fictional placard.",
                evidence_ids=(evidence_ids[3],),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text=(
                    "Record the fixture reference code BL-204, then open the linked PDF to "
                    "continue with step 3."
                ),
                evidence_ids=(evidence_ids[3],),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Enter Fixture Student and reference code BL-204.",
                evidence_ids=(evidence_ids[4],),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Attach the sample image labeled BLUE-LANTERN-SAMPLE.",
                evidence_ids=(evidence_ids[4],),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text=(
                    "Submit through the fixture-only submission route. Retain the synthetic "
                    "confirmation text TEST-COMPLETE."
                ),
                evidence_ids=(evidence_ids[5],),
            ),
        ),
    )


def _context_answer(evidence_id: str) -> StructuredAnswer:
    return StructuredAnswer(
        outcome=AnswerOutcome.ANSWER,
        segments=(
            AnswerSegment(
                kind=SegmentKind.EXPLANATION,
                text=CONTEXT_EVIDENCE_ANSWERS[evidence_id],
                evidence_ids=(UUID(evidence_id),),
            ),
        ),
    )


def _academic_answer(evidence_id: str) -> StructuredAnswer:
    evidence_uuid = UUID(evidence_id)
    segments: tuple[AnswerSegment, ...]
    outcome = AnswerOutcome.PARTIAL
    reason_code: ReasonCode | None = ReasonCode.PERSONAL_CASE
    if evidence_id == ACADEMIC_EVIDENCE_IDS["program"]:
        outcome = AnswerOutcome.ANSWER
        reason_code = None
        segments = (
            AnswerSegment(
                kind=SegmentKind.EXPLANATION,
                text=(
                    "The Synthetic Data Science, MS program is explicitly listed as offered by "
                    "Purdue University Northwest in the fictional 2030-2031 graduate catalog."
                ),
                evidence_ids=(evidence_uuid,),
            ),
        )
    elif evidence_id == ACADEMIC_EVIDENCE_IDS["graduate_admission"]:
        segments = (
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Review the requirements published for the intended graduate program.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Prepare the materials named by that program before submitting an application.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Submit the fictional graduate application for review by the graduate program.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.LIMITATION,
                text=(
                    "These steps are general guidance for graduate applicants. They do not decide "
                    "whether any person is admissible or guarantee an admission decision."
                ),
                evidence_ids=(evidence_uuid,),
            ),
        )
    elif evidence_id == ACADEMIC_EVIDENCE_IDS["prerequisites"]:
        segments = (
            AnswerSegment(
                kind=SegmentKind.STEP,
                text=(
                    "Complete either both SYN 21000 with a minimum grade of B and SYN 22000 "
                    "with a minimum grade of C, or SYN 23000 with a minimum grade of B."
                ),
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="SYN 35001 is a corequisite and may be taken concurrently with SYN 35000.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.LIMITATION,
                text=(
                    "These published relationships do not establish a student's personal "
                    "eligibility, registration approval, or current course availability."
                ),
                evidence_ids=(evidence_uuid,),
            ),
        )
    elif evidence_id == ACADEMIC_EVIDENCE_IDS["plan_of_study"]:
        segments = (
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Review the published program requirements and draft the proposed course list.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Discuss the draft with the assigned academic advisor.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Obtain the advisor's approval of the proposed plan.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Submit the approved plan through the fictional Graduate Plan portal.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.LIMITATION,
                text=(
                    "The procedure explains preparation and approval steps only. It does not "
                    "select courses for a student, replace advisor review, or determine whether "
                    "a personal plan is valid."
                ),
                evidence_ids=(evidence_uuid,),
            ),
        )
    elif evidence_id == ACADEMIC_EVIDENCE_IDS["graduation"]:
        segments = (
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Review the published graduation requirements for the applicable catalog.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Ask the academic advisor to review the planned completion term.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Submit the fictional graduation application through the student portal.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Monitor official university messages for any requested follow-up.",
                evidence_ids=(evidence_uuid,),
            ),
            AnswerSegment(
                kind=SegmentKind.LIMITATION,
                text=(
                    "Submitting an application does not establish that degree requirements are "
                    "complete. Only the appropriate university review can determine an "
                    "individual's graduation status."
                ),
                evidence_ids=(evidence_uuid,),
            ),
        )
    else:
        raise ValueError("unsupported synthetic academic evidence")
    return StructuredAnswer(
        outcome=outcome,
        segments=segments,
        reason_code=reason_code,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ready"}


@app.post("/v1/chat/completions")
def chat_completions(payload: Annotated[dict[str, Any], Body()]) -> dict[str, object]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise HTTPException(status_code=422, detail="invalid synthetic request")
    user_message = messages[1]
    if not isinstance(user_message, dict) or not isinstance(user_message.get("content"), str):
        raise HTTPException(status_code=422, detail="invalid synthetic request")
    try:
        prompt = json.loads(user_message["content"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="invalid synthetic request") from None
    eligible_evidence = prompt.get("eligible_evidence", []) if isinstance(prompt, dict) else []
    supplied_ids: set[str] = {
        evidence_id
        for item in eligible_evidence
        if isinstance(item, dict) and isinstance((evidence_id := item.get("evidence_id")), str)
    }
    if set(EVIDENCE_IDS).issubset(supplied_ids):
        answer = _answer()
    elif len(supplied_ids) == 1 and supplied_ids <= set(ACADEMIC_EVIDENCE_IDS.values()):
        answer = _academic_answer(next(iter(supplied_ids)))
    elif len(supplied_ids) == 1 and supplied_ids <= CONTEXT_EVIDENCE_ANSWERS.keys():
        answer = _context_answer(next(iter(supplied_ids)))
    else:
        raise HTTPException(status_code=422, detail="synthetic evidence is incomplete")
    return {
        "choices": [
            {
                "message": {
                    "content": answer.model_dump_json(),
                }
            }
        ]
    }


@app.post("/slots/{slot_id}")
def erase_slot(
    slot_id: int,
    action: Annotated[str, Query()],
) -> dict[str, int]:
    if slot_id != 0 or action != "erase":
        raise HTTPException(status_code=422, detail="invalid synthetic slot cleanup")
    return {"id_slot": slot_id, "n_erased": 1}
