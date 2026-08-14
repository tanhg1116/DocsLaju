"""Validation and normalization for versioned structured extraction data."""

from __future__ import annotations

import math


def _nullable_text(value: object, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _nullable_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Amounts must be numbers")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Amounts must be numbers") from exc
    if not math.isfinite(number) or not (-1_000_000_000_000 < number < 1_000_000_000_000):
        raise ValueError("Amount is outside the supported range")
    return round(number, 6)


def normalize_template_data(data: object, template: dict) -> dict:
    """Keep only schema-defined values and normalize each declared field type."""
    if not isinstance(data, dict):
        raise ValueError("Structured extraction must be an object")
    schema = template["schema"]

    def normalize(value: object, field_schema: dict, depth: int = 0) -> object:
        if depth > 4:
            raise ValueError("Structured extraction is nested too deeply")
        raw_type = field_schema.get("type")
        allowed_types = raw_type if isinstance(raw_type, list) else [raw_type]
        non_null_type = next((item for item in allowed_types if item != "null"), None)
        if value is None:
            return [] if non_null_type == "array" else None
        if non_null_type == "string":
            return _nullable_text(value, 4000)
        if non_null_type == "number":
            return _nullable_number(value)
        if non_null_type == "boolean":
            if isinstance(value, bool):
                return value
            raise ValueError("Boolean fields must be true or false")
        if non_null_type == "array":
            if not isinstance(value, list):
                raise ValueError("Repeated fields must be lists")
            item_schema = field_schema.get("items", {})
            normalized_items = [
                normalize(item, item_schema, depth + 1) for item in value[:500]
            ]
            item_type = item_schema.get("type")
            if item_type == "string" or (
                isinstance(item_type, list) and "string" in item_type
            ):
                unique_items: list[str] = []
                seen: set[str] = set()
                for item in normalized_items:
                    if not isinstance(item, str) or not item:
                        continue
                    identity = item.casefold()
                    if identity in seen:
                        continue
                    seen.add(identity)
                    unique_items.append(item)
                return unique_items
            return normalized_items
        if non_null_type == "object":
            if not isinstance(value, dict):
                raise ValueError("Structured sections must be objects")
            properties = field_schema.get("properties", {})
            return {
                key: normalize(value.get(key), child_schema, depth + 1)
                for key, child_schema in properties.items()
            }
        return None

    normalized = {
        key: normalize(data.get(key), field_schema)
        for key, field_schema in schema["properties"].items()
    }
    normalized["document_type"] = template["id"]
    return normalized
