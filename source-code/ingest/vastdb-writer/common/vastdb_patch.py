"""Monkey-patch VastDB SDK so predicate selects work on tables with vector columns."""
from __future__ import annotations

import logging

import pyarrow as pa
import vastdb._internal as _internal

logger = logging.getLogger(__name__)

_original_build_query_data_request = _internal.build_query_data_request


def _unsupported_vector_field(field: pa.Field) -> bool:
    field_type = str(field.type)
    if "fixed_size_list" in field_type:
        return True
    return "list<" in field_type and "float" in field_type


def _patched_build_query_data_request(schema, predicate, field_names):
    supported_fields = []
    unsupported = set()
    for field in schema:
        if _unsupported_vector_field(field):
            unsupported.add(field.name)
        else:
            supported_fields.append(field)
    if unsupported:
        logger.debug("[VastDB] Excluding unsupported columns from predicate query: %s", unsupported)
    filtered_names = [name for name in field_names if name not in unsupported]
    return _original_build_query_data_request(pa.schema(supported_fields), predicate, filtered_names)


_internal.build_query_data_request = _patched_build_query_data_request
