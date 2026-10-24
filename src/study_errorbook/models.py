"""Input contracts shared by MCP tools and durable study records."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Region(Input):
    page: int = Field(ge=1, le=20)
    # Original-page normalized bounding box, never an unlabelled OCR coordinate.
    box: list[float] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def ordered_box(self):
        left, top, right, bottom = self.box
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError("box must satisfy 0 <= left < right <= 1 and top < bottom")
        return self


class CoverageQuestion(Input):
    question_label: str = Field(min_length=1, max_length=100)
    record_id: str | None = Field(default=None, min_length=1, max_length=100, description="关联已保存记录；填写skip_reason时必须省略record_id，不能同时关联并跳过。")
    skip_reason: str = Field(default="", max_length=400, description="本轮跳过理由；有理由时省略record_id，原草稿仍保留。")

    @model_validator(mode="after")
    def exclusive_disposition(self):
        if not self.question_label.strip() or (self.record_id and self.skip_reason.strip()):
            raise ValueError("use a real label and either a linked record or a skip reason")
        return self


class CoveragePage(Input):
    page: StrictInt = Field(ge=1, le=20)
    # A whole-page evidence ID records the host's visual inventory, not OCR completeness.
    evidence_id: str = Field(min_length=1, max_length=100)
    questions: list[CoverageQuestion] = Field(default_factory=list, max_length=100)
    note: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def unique_inventory(self):
        labels = [q.question_label for q in self.questions]
        if len(set(labels)) != len(labels) or (not labels and not self.note.strip()):
            raise ValueError("labels must be unique per page; an empty page requires an observation")
        return self


class Annotation(Input):
    text: str = Field(max_length=4000)
    kind: Literal["answer", "correction", "tick", "cross", "circle", "score", "other"]
    author: Literal["student", "teacher", "unknown"] = "unknown"
    color: Literal["red", "black", "blue", "pencil", "unknown"] = "unknown"
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class Analysis(Input):
    explanation: str = Field(min_length=1, max_length=8000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    error_type: str = Field(max_length=200)
    knowledge_points: list[str] = Field(default_factory=list, max_length=20)
    uncertainty: str = Field(default="", max_length=2000)
    diagnostic_question: str = Field(default="", max_length=2000)


class Exercise(Input):
    kind: Literal["variant", "original_text"] = Field(default="variant", description="variant为变式；original_text为依据完整原图核实的原题文字重练，答案仍独立保存。")
    prompt: str = Field(min_length=1, max_length=4000)
    answer: str = Field(min_length=1, max_length=4000)
    explanation: str = Field(default="", max_length=4000)
    targets_error: str = Field(min_length=1, max_length=400)
    # A check ID proves only the supplied algebraic expressions, not a natural-language problem.
    check_id: str | None = None


class StudyRecord(Input):
    document_id: str
    question_label: str = Field(min_length=1, max_length=100)
    subject: str = Field(default="", max_length=100)
    stem: str = Field(min_length=1, max_length=12000)
    student_answer: str = Field(default="", max_length=8000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)
    annotations: list[Annotation] = Field(default_factory=list, max_length=40)
    clean_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    teacher_verdict: Literal["correct", "wrong", "partial", "unmarked", "uncertain"] = "uncertain"
    agent_verdict: Literal["correct", "wrong", "partial", "unanswered", "uncertain"] = "uncertain"
    analysis: Analysis | None = None
    exercises: list[Exercise] = Field(default_factory=list, max_length=10)


class PracticeFeedback(Input):
    agent_verdict: Literal["correct", "wrong", "partial", "uncertain"]
    feedback: str = Field(min_length=1, max_length=4000)
    next_step: str = Field(min_length=1, max_length=2000)
    user_verdict: Literal["unreported", "correct", "wrong", "partial", "uncertain"] = "unreported"
    user_note: str = Field(default="", max_length=2000, description="只记录用户明确自判或撤回意见；user_verdict=unreported且未撤回时必须省略。模拟来源或作答声明写入feedback，不写此字段。")
    withdraw_user_opinion: bool = Field(default=False, strict=True,
        description="Only on an explicit user withdrawal; requires unreported and a nonempty withdrawal note.")

    @model_validator(mode="after")
    def explicit_feedback(self):
        if not self.feedback.strip() or not self.next_step.strip():
            raise ValueError("feedback and next_step must contain an explanation and an actionable next step")
        if self.withdraw_user_opinion:
            if self.user_verdict != "unreported" or not self.user_note.strip():
                raise ValueError("withdrawal requires unreported and a real user withdrawal note")
        elif (self.user_verdict == "unreported") != (not self.user_note.strip()):
            raise ValueError("a reported user verdict requires a real user_note; unreported keeps user_note empty")
        return self


class ReviewPlanChange(Input):
    action: Literal["schedule", "complete", "cancel"]
    user_note: str = Field(min_length=1, max_length=2000)
    due_at: AwareDatetime | None = None
    practice_kind: Literal["original", "exercise"] = "original"
    exercise_index: StrictInt | None = None
    attempt_id: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def explicit_plan(self):
        if not self.user_note.strip():
            raise ValueError("user_note must record the user's requested plan or action")
        if self.action == "schedule":
            if self.due_at is None or self.attempt_id is not None:
                raise ValueError("schedule requires a timezone-aware due_at and no attempt_id")
            if (self.practice_kind == "original") != (self.exercise_index is None):
                raise ValueError("original has no exercise_index; exercise requires an index")
        elif self.due_at is not None or self.exercise_index is not None or self.practice_kind != "original":
            raise ValueError("complete/cancel keep the scheduled target; omit due_at and target fields")
        if (self.action == "complete") != (self.attempt_id is not None):
            raise ValueError("only complete requires an attempt_id")
        return self


class DomainError(Exception):
    """Safe, user-actionable error; no provider body or credentials."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
