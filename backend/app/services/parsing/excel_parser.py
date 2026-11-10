"""Excel (.xlsx/.xlsm) parsing with auto-detected header row, columns and layout."""
from pathlib import Path

import pandas as pd

from . import common


def read_sheets(path: Path) -> dict[str, list[list]]:
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    return {name: df.where(df.notna(), None).values.tolist() for name, df in sheets.items()}


def parse_excel(path: Path, sheet: str | None = None, mapping: dict | None = None, layout: str | None = None) -> dict:
    sheets = read_sheets(path)
    names = list(sheets)
    warnings: list[str] = []

    if sheet is None:  # first sheet with a recognisable header
        sheet = next((n for n in names if common.find_header(sheets[n]) is not None), names[0] if names else None)
    if sheet is None:
        return {"sheets": [], "sheet": None, "warnings": ["Workbook has no sheets"], "columns": [], "mapping": {},
                "layout": "wide", "records": [], "preview_rows": []}

    raw = sheets[sheet]
    hdr = common.find_header(raw)
    if hdr is None:
        hdr = 0
        warnings.append("No recognisable header row found; using the first row. Map columns manually.")
    columns, rows = common.table_from_raw(raw, hdr) if raw else ([], [])

    detected = common.detect_mapping(columns)
    use_mapping = {k: v for k, v in (mapping or detected).items() if v in columns}
    use_layout = layout or common.detect_layout(rows, use_mapping)
    records, w = common.build_records(rows, use_mapping, use_layout)
    return {
        "sheets": names, "sheet": sheet, "columns": columns, "mapping": use_mapping, "detected_mapping": detected,
        "layout": use_layout, "records": records, "warnings": warnings + w, "preview_rows": rows[:8],
    }
