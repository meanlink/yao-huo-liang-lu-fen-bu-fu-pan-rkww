from pydantic import BaseModel, Field, model_validator


class GroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    recipe: str = Field(min_length=1, max_length=500)
    batches: list[str] = Field(min_length=1, max_length=80)


class TrialInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    line: str = Field(min_length=1, max_length=60)
    groups: list[GroupInput] = Field(min_length=2, max_length=2)


class InspectionInput(BaseModel):
    blind_code: str = Field(min_length=3, max_length=30)
    fired: int = Field(ge=1, le=100000)
    passed: int = Field(ge=0, le=100000)
    revision: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def check_counts(self):
        if self.passed > self.fired:
            raise ValueError('合格件数不能大于烧成件数')
        return self


class SealInput(BaseModel):
    revision: int = Field(ge=0)
