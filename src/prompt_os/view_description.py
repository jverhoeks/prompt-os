from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class TextBlock(BaseModel):
    type: Literal["text"]
    markdown: str


class MetricBlock(BaseModel):
    type: Literal["metric"]
    label: str
    value: str
    detail: str | None = None


class ListBlock(BaseModel):
    type: Literal["list"]
    title: str | None = None
    items: list[str] = Field(min_length=1)


class TableBlock(BaseModel):
    type: Literal["table"]
    title: str | None = None
    columns: list[str] = Field(min_length=1)
    rows: list[list[str]]

    @model_validator(mode="after")
    def rows_match_columns(self) -> "TableBlock":
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("every table row must match the number of columns")
        return self


class BarDatum(BaseModel):
    label: str
    value: float
    display: str | None = None


class BarChartBlock(BaseModel):
    type: Literal["bar-chart"]
    title: str | None = None
    series: list[BarDatum] = Field(min_length=1)


ViewBlock = Annotated[
    TextBlock | MetricBlock | ListBlock | TableBlock | BarChartBlock,
    Field(discriminator="type"),
]


class ViewDescription(BaseModel):
    title: str | None = None
    blocks: list[ViewBlock] = Field(min_length=1)
