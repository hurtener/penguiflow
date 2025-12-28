"""Pydantic models for rich output tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RenderComponentArgs(BaseModel):
    component: str = Field(..., description="Registry component name")
    props: dict[str, Any] = Field(default_factory=dict, description="Component props")
    id: str | None = Field(default=None, description="Optional stable component id")
    title: str | None = None
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class RenderComponentResult(BaseModel):
    ok: bool = True


class DescribeComponentArgs(BaseModel):
    name: str = Field(..., description="Component name")

    model_config = ConfigDict(extra="forbid")


class DescribeComponentResult(BaseModel):
    component: dict[str, Any]


class FormFieldOption(BaseModel):
    value: str
    label: str

    model_config = ConfigDict(extra="forbid")


class FormFieldValidation(BaseModel):
    min: float | None = None
    max: float | None = None
    min_length: int | None = Field(default=None, alias="minLength")
    max_length: int | None = Field(default=None, alias="maxLength")
    pattern: str | None = None
    message: str | None = None

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


FormFieldType = Literal[
    "text",
    "number",
    "email",
    "password",
    "url",
    "tel",
    "textarea",
    "select",
    "multiselect",
    "checkbox",
    "radio",
    "switch",
    "date",
    "datetime",
    "time",
    "file",
    "range",
    "color",
]


class FormField(BaseModel):
    name: str
    type: FormFieldType
    label: str | None = None
    placeholder: str | None = None
    required: bool = False
    disabled: bool = False
    default: Any = None
    options: list[str | FormFieldOption] | None = None
    validation: FormFieldValidation | None = None
    help_text: str | None = Field(default=None, alias="helpText")
    width: Literal["full", "half", "third"] = "full"

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class UIFormArgs(BaseModel):
    title: str | None = None
    description: str | None = None
    fields: list[FormField]
    submit_label: str = Field(default="Submit", alias="submitLabel")
    cancel_label: str | None = Field(default=None, alias="cancelLabel")
    layout: Literal["vertical", "horizontal", "inline"] = "vertical"

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class UIConfirmArgs(BaseModel):
    title: str | None = None
    message: str
    confirm_label: str = Field(default="Confirm", alias="confirmLabel")
    cancel_label: str = Field(default="Cancel", alias="cancelLabel")
    variant: Literal["info", "warning", "danger", "success"] = "info"
    details: str | None = None

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class SelectOptionItem(BaseModel):
    value: str
    label: str
    description: str | None = None
    icon: str | None = None
    disabled: bool = False
    metadata: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class UISelectOptionArgs(BaseModel):
    title: str | None = None
    description: str | None = None
    options: list[SelectOptionItem]
    multiple: bool = False
    min_selections: int = Field(default=1, alias="minSelections")
    max_selections: int | None = Field(default=None, alias="maxSelections")
    layout: Literal["list", "grid", "cards"] = "list"
    searchable: bool = False

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class UIInteractionResult(BaseModel):
    ok: bool = True


__all__ = [
    "RenderComponentArgs",
    "RenderComponentResult",
    "DescribeComponentArgs",
    "DescribeComponentResult",
    "FormField",
    "UIFormArgs",
    "UIConfirmArgs",
    "SelectOptionItem",
    "UISelectOptionArgs",
    "UIInteractionResult",
]
