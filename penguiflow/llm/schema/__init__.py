"""Schema transformation for the LLM layer.

Exports transformer classes and planning utilities.
"""

from .plan import OutputMode, SchemaPlan, choose_output_mode, plan_schema
from .transformer import (
    JsonSchemaTransformer,
    estimate_object_key_count,
    has_composition_keywords,
    has_refs,
)

__all__ = [
    "JsonSchemaTransformer",
    "OutputMode",
    "SchemaPlan",
    "choose_output_mode",
    "estimate_object_key_count",
    "has_composition_keywords",
    "has_refs",
    "plan_schema",
]
