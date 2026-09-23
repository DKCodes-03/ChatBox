from enum import Enum

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class AnswerType(str, Enum):
    SUPPORTED = "supported"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    CONFLICT = "conflict"
    ESCALATION = "escalation"


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1)
    session_id: str | None = Field(default=None, min_length=1)
    campus_hint: str | None = Field(default=None, min_length=1)


class CitationResponse(BaseModel):
    title: str = Field(min_length=1)
    url: AnyHttpUrl
    snippet: str = Field(min_length=1)


class ChatResponse(BaseModel):
    answer: str = Field(min_length=1)
    answer_type: AnswerType
    citations: list[CitationResponse] = Field(default_factory=list)
    escalation_target: str | None = Field(default=None, min_length=1)
    follow_up_question: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_citation_for_supported_answer(self) -> "ChatResponse":
        if self.answer_type is AnswerType.SUPPORTED and not self.citations:
            raise ValueError("supported answers require at least one citation")
        return self


class ResetRequest(BaseModel):
    session_id: str = Field(min_length=1)