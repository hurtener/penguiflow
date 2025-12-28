from __future__ import annotations

from penguiflow.rich_output.tools import (
    FormField,
    FormFieldValidation,
    RenderComponentArgs,
    SelectOptionItem,
    UIConfirmArgs,
    UIFormArgs,
    UISelectOptionArgs,
)


def test_render_component_args_defaults() -> None:
    args = RenderComponentArgs(component="markdown")
    assert args.props == {}
    assert args.model_dump(by_alias=True)["props"] == {}


def test_form_field_validation_aliases() -> None:
    validation = FormFieldValidation(minLength=2, maxLength=5)
    dumped = validation.model_dump(by_alias=True)
    assert dumped["minLength"] == 2
    assert dumped["maxLength"] == 5


def test_ui_form_args_accepts_aliases() -> None:
    args = UIFormArgs(
        title="Upload",
        fields=[FormField(name="email", type="email")],
        submitLabel="Send",
        cancelLabel="Skip",
    )
    dumped = args.model_dump(by_alias=True)
    assert dumped["submitLabel"] == "Send"
    assert dumped["cancelLabel"] == "Skip"


def test_ui_confirm_args_defaults() -> None:
    args = UIConfirmArgs(message="Continue?")
    assert args.confirm_label == "Confirm"
    assert args.cancel_label == "Cancel"


def test_ui_select_option_aliases() -> None:
    args = UISelectOptionArgs(
        options=[SelectOptionItem(value="a", label="Option A")],
        minSelections=2,
        maxSelections=3,
    )
    dumped = args.model_dump(by_alias=True)
    assert dumped["minSelections"] == 2
    assert dumped["maxSelections"] == 3
