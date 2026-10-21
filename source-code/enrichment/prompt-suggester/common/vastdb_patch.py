"""Omit vector columns from VastDB selects."""
import pyarrow as pa
import vastdb._internal as _internal

_original_build_query_data_request = _internal.build_query_data_request


def apply_vastdb_vector_column_patch() -> None:
    def patched(schema, predicate, field_names):
        supported, skip = [], set()
        for field in schema:
            t = str(field.type)
            if "fixed_size_list" in t or ("list<" in t and "float" in t):
                skip.add(field.name)
            else:
                supported.append(field)
        names = [n for n in field_names if n not in skip]
        return _original_build_query_data_request(pa.schema(supported), predicate, names)

    _internal.build_query_data_request = patched


apply_vastdb_vector_column_patch()
