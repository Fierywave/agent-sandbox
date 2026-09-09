"""csv_query tool — medium risk, no approval required (but analyst+ only,
enforced by the permission check, not by this file).

Reads only from track-b-agent/demo_data/<dataset>.csv — same sandboxing
principle as file_reader: the dataset name is untrusted model output, so
it's resolved against a fixed folder, never an arbitrary path.
"""

import csv
from pathlib import Path

_DATA_ROOT = (Path(__file__).parent.parent / "demo_data").resolve()


class DatasetNotFoundError(ValueError):
    pass


class AmbiguousQueryError(ValueError):
    """Raised when a filter matches multiple rows and the tool needs the
    caller to disambiguate rather than silently picking one — this is what
    Phase 3's 'ask a clarifying question' behavior will catch."""


def _load_dataset(dataset: str) -> list[dict]:
    path = (_DATA_ROOT / f"{dataset}.csv").resolve()
    try:
        path.relative_to(_DATA_ROOT)
    except ValueError:
        raise DatasetNotFoundError(f"'{dataset}' resolves outside demo_data/")
    if not path.exists():
        raise DatasetNotFoundError(f"No dataset named '{dataset}' in demo_data/")
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def run(dataset: str, filter: dict) -> dict:
    """Contract: input_schema={dataset: str, filter: object} ->
    output_schema={rows: array}

    `filter` is a simple field->value equality match, e.g.
    {"customer_id": "C003"} or {"plan": "pro", "status": "active"}.
    """
    rows = _load_dataset(dataset)
    matches = [
        row for row in rows
        if all(row.get(k) == v for k, v in filter.items())
    ]
    return {"rows": matches}