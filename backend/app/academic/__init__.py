"""Source-backed academic guidance helpers."""

from app.academic.authorization import AcademicAuthorization, authorize_academic_evidence
from app.academic.prerequisites import (
    CorequisiteRequirement,
    CoursePrerequisites,
    InvalidPrerequisiteDataError,
    PrerequisiteGroup,
    PrerequisiteOperator,
    PrerequisiteRepository,
    PrerequisiteRequirement,
    PrerequisiteRetrievalError,
    parse_course_relations,
)
from app.academic.programs import (
    AcademicEvidenceProfile,
    AcademicLimitation,
    AcademicScopeDecision,
    AcademicScopeOutcome,
    AcademicScopeRules,
    AcademicTopic,
    AvailabilityBasis,
    InvalidAcademicScopeError,
    ProgramStatus,
)

__all__ = [
    "AcademicAuthorization",
    "AcademicEvidenceProfile",
    "AcademicLimitation",
    "AcademicScopeDecision",
    "AcademicScopeOutcome",
    "AcademicScopeRules",
    "AcademicTopic",
    "AvailabilityBasis",
    "CorequisiteRequirement",
    "CoursePrerequisites",
    "InvalidAcademicScopeError",
    "InvalidPrerequisiteDataError",
    "PrerequisiteGroup",
    "PrerequisiteOperator",
    "PrerequisiteRepository",
    "PrerequisiteRequirement",
    "PrerequisiteRetrievalError",
    "ProgramStatus",
    "authorize_academic_evidence",
    "parse_course_relations",
]
