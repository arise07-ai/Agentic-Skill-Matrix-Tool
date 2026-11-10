"""PDF parsing: tables first (same pipeline as Excel), then 'Key: value' text profiles,
then the optional LLM extractor (app/ai/tasks.py) for anything unstructured.
Scanned PDFs (images only) are not OCR'd; add OCR in `extract_text` if you need it."""
import re
from pathlib import Path

import pdfplumber

from ...ai import AINotConfigured, tasks
from . import common

KEY_FIELDS = {norm: f for f, syns in common.SYNONYMS.items() for norm in map(common.norm, syns)}


def extract(path: Path) -> tuple[list[list[list]], str]:
    tables, text = [], []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables.extend(page.extract_tables() or [])
            text.append(page.extract_text() or "")
    return tables, "\n".join(text)


def _from_tables(tables: list[list[list]]) -> tuple[list[str], list[dict]] | None:
    columns: list[str] | None = None
    first_header: list[str] = []
    rows: list[dict] = []
    for t in tables:
        if not t:
            continue
        t = [[common.unwrap_cell(c) for c in row] for row in t]
        if columns is None:
            hdr = common.find_header(t, search=3)
            if hdr is None:
                continue
            columns, r = common.table_from_raw(t, hdr)
            first_header = [common.clean(c) for c in t[hdr]]
            rows.extend(r)
        else:  # continuation of the table on a later page: drop a repeated header row
            body = t[1:] if [common.clean(c) for c in t[0]] == first_header else t
            _, r = common.table_from_raw([columns] + body, 0)
            rows.extend(r)
    return (columns, rows) if columns else None


def _parse_text_profiles(text: str) -> list[dict]:
    blocks: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    field: str | None = None
    for line in text.splitlines():
        if not line.strip():
            continue
        left, sep, right = line.partition(":")
        f = KEY_FIELDS.get(common.norm(left)) if sep else None
        if f:
            if f in cur:  # same field again => next profile
                blocks.append(cur)
                cur = {}
            cur[f] = right.strip()
            field = f
        elif field:
            cur[field] = (cur[field] + "\n" + line.strip()).strip()
    if cur:
        blocks.append(cur)

    records = []
    for b in blocks:
        if not (b.get("name") or b.get("employee_no")) or len(b) < 2:
            continue
        skills, _ = common.parse_skills_cell(b.get("skills", ""), b.get("ratings", ""))
        records.append({
            "employee_no": b.get("employee_no") or None,
            "name": b.get("name") or b.get("employee_no", ""),
            "roles": common.split_list(b.get("roles", "")),
            "background": b.get("background", "").replace("\n", " "),
            "skills": skills,
            "domain_tags": common.split_list(b.get("domain_tags", "")),
            "domain_tags_provided": "domain_tags" in b,
        })
    return records


CARD_HEADER = re.compile(r"^(?P<name>.+?)\s*[·•|]\s*(?P<no>[A-Za-z]{0,6}-?\d{2,}[\w-]*)$")
CARD_RATING = re.compile(r"^([0-4])\s*/\s*4$")  # levels are 0-4; a chip such as "4/5" is from the old scale and is not read as a rating


def _lines(words: list[dict], tol: float = 3.0) -> list[list[dict]]:
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if out and abs(out[-1][0]["top"] - w["top"]) <= tol:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(ln, key=lambda w: w["x0"]) for ln in out]


def _parse_cards(path: Path) -> list[dict]:
    """'Card' layout: a header line 'Name · EMP001', a 'Role — Department' line, then skills on the left with an
    'n/4' rating chip each and the biography as a text column on the right. Columns are told apart by x position,
    because the skill rows and the biography lines do not share baselines."""
    cards: list[dict] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = [w for w in page.extract_words(x_tolerance=2, y_tolerance=2) if w["top"] < page.height - 40]
            heads = []
            for ln in _lines(words):
                if ln[0]["x0"] > page.width * 0.2:
                    continue
                m = CARD_HEADER.match(" ".join(w["text"] for w in ln))
                if m:
                    heads.append((ln[0]["top"], m.group("name").strip(), m.group("no"), ln))
            starts = [h[0] for h in heads]
            regions = [(None, None, 0.0, starts[0] if starts else page.height)]  # continuation of the previous page's card
            regions += [(h[1], h[2], h[0], starts[i + 1] if i + 1 < len(starts) else page.height) for i, h in enumerate(heads)]
            for name, no, top, bottom in regions:
                head_ids = {id(w) for h in heads if h[0] == top for w in h[3]}
                body = [w for w in words if top <= w["top"] < bottom and id(w) not in head_ids]
                ratings = sorted((w for w in body if CARD_RATING.match(w["text"])), key=lambda w: w["top"])
                if name is None and (not cards or not ratings):
                    continue
                if name is not None:
                    cards.append({"employee_no": no, "name": name, "roles": [], "background": "", "skills": [],
                                  "domain_tags": [], "domain_tags_provided": False})
                card = cards[-1]
                if not ratings:
                    continue
                rx0 = min(w["x0"] for w in ratings)
                rx1 = max(w["x1"] for w in ratings)
                first = ratings[0]["top"] - 8
                if name is not None:  # the line between the header and the first skill: 'Role — Department'
                    role_line = [w["text"] for ln in _lines([w for w in body if w["top"] < first]) for w in ln]
                    role = re.split(r"\s[—–-]\s", " ".join(role_line))[0].strip()
                    card["roles"] = common.split_list(role)
                bounds = [first] + [(a["top"] + b["top"]) / 2 for a, b in zip(ratings, ratings[1:])] + [bottom]
                for i, rt in enumerate(ratings):
                    cell = [w for w in body if w["x1"] <= rx0 + 1 and bounds[i] <= w["top"] < bounds[i + 1]]
                    skill = " ".join(w["text"] for ln in _lines(cell, 4.0) for w in ln).strip()
                    if skill:
                        card["skills"].append({"raw_skill": skill, "rating": int(CARD_RATING.match(rt["text"]).group(1))})
                bio = [w for w in body if w["x0"] >= rx1 - 1 and w["top"] >= first]
                text = " ".join(w["text"] for ln in _lines(bio) for w in ln)
                card["background"] = (card["background"] + " " + text).strip()
    return [c for c in cards if c["skills"]]


def parse_pdf(path: Path, mapping: dict | None = None, layout: str | None = None) -> dict:
    tables, text = extract(path)
    warnings: list[str] = []

    t = _from_tables(tables)
    if t:
        columns, rows = t
        detected = common.detect_mapping(columns)
        use_mapping = {k: v for k, v in (mapping or detected).items() if v in columns}
        listy = {use_mapping.get("skills"), use_mapping.get("ratings")}
        for row in rows:  # newlines in name/roles/background cells are line wraps, not separators
            for col, val in row.items():
                if col not in listy:
                    row[col] = re.sub(r"\s*\n\s*", " ", val)
        use_layout = layout or common.detect_layout(rows, use_mapping)
        records, w = common.build_records(rows, use_mapping, use_layout)
        return {"source": "pdf-table", "columns": columns, "mapping": use_mapping, "detected_mapping": detected,
                "layout": use_layout, "records": records, "warnings": w, "preview_rows": rows[:8]}

    cards = _parse_cards(path)
    if cards:
        return {"source": "pdf-cards", "columns": [], "mapping": {}, "detected_mapping": {}, "layout": "wide",
                "records": cards, "warnings": [], "preview_rows": []}

    records = _parse_text_profiles(text)
    source = "pdf-text"
    if not records:
        if not text.strip():
            warnings.append("No extractable text (scanned PDF?). OCR is not included.")
        else:
            try:
                records = [
                    {"employee_no": e.get("employee_no"), "name": e.get("name", ""), "roles": e.get("roles", []),
                     "background": e.get("background", ""),
                     "domain_tags": e.get("domain_tags", []), "domain_tags_provided": bool(e.get("domain_tags")),
                     "skills": [{"raw_skill": s["skill"], "rating": common.normalize_rating(s.get("rating"))[0]}
                                for s in e.get("skills", []) if s.get("skill")]}
                    for e in tasks.extract_profiles_from_text(text)
                ]
                source = "pdf-llm"
                warnings.append("Profiles were extracted by the LLM; please review before importing.")
            except AINotConfigured:
                warnings.append("No tables or 'Name: ...' style profiles found. Configure the LLM (OPENAI_API_KEY) "
                                "to extract free-form PDFs, or upload Excel.")
    for r in records:
        if not r["skills"]:
            warnings.append(f"{r['name']}: no skills found")
    return {"source": source, "columns": [], "mapping": {}, "detected_mapping": {}, "layout": "wide",
            "records": records, "warnings": warnings, "preview_rows": []}
