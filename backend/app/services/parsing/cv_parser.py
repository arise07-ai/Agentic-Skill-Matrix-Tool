"""One CV (PDF or Word .docx) -> a PROPOSED employee record. The model estimates, the code verifies, a person approves.

  1. extract_text()      reads the file's text (PDF: pdfplumber; .docx: the XML inside the zip, no extra library).
  2. tasks.extract_cv()  the model proposes name, roles, background and skills with a level and an exact quote each.
  3. verify_estimates()  CODE checks every quote against the CV text, caps the level, drops skills the CV never mentions.
  4. seniority.assess()  CODE computes Junior/Mid/Senior from the verified levels.
The person then reviews and edits the result and submits it to the approval queue; nothing reaches the matrix before it is approved.
"""
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from ...ai import tasks

MAX_CV_CHARS = 30000      # about eight pages; the rest is ignored and the preview says so
MAX_SKILLS = 30
CV_MAX_LEVEL = 3          # a CV can show Experienced; Mastered (4) is only ever given by a person
UNVERIFIED_LEVEL = 1      # a skill whose quote is not in the CV is kept at Learned and flagged for the reviewer
MIN_QUOTE_CHARS = 8
MAX_DOCX_BYTES = 20 * 1024 * 1024
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class CVError(ValueError):
    """A CV that cannot be read; the message is safe to show the user."""


def _docx_part_text(xml: bytes) -> str:
    head = xml[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in xml[:65536].lower():  # entity tricks are never in a real Word file
        raise CVError("This Word file contains unsupported XML.")
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter(W + "p"):
        parts = []
        for el in p.iter():
            if el.tag == W + "t" and el.text:
                parts.append(el.text)
            elif el.tag == W + "tab":
                parts.append("\t")
            elif el.tag in (W + "br", W + "cr"):
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            paras.append(line)
    return "\n".join(paras)


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "word/document.xml" not in names:
                raise CVError("This is not a Word (.docx) document.")
            if sum(i.file_size for i in z.infolist()) > MAX_DOCX_BYTES:
                raise CVError("This Word file is too large to read safely.")
            headers = sorted(n for n in names if re.fullmatch(r"word/header\d*\.xml", n))  # contact details are often in the header
            return "\n".join(_docx_part_text(z.read(n)) for n in headers + ["word/document.xml"])
    except zipfile.BadZipFile:
        raise CVError("This is not a valid .docx file (an old .doc must be saved as .docx first).")
    except ET.ParseError:
        raise CVError("The Word document could not be read.")


def _pdf_text(path: Path) -> str:
    import pdfplumber

    try:
        with pdfplumber.open(path) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages[:20])
    except Exception as e:  # noqa: BLE001
        raise CVError(f"The PDF could not be read ({type(e).__name__}).")


def extract_text(path: Path, filename: str) -> tuple[str, bool]:
    """-> (text, was_cut). Raises CVError when the file type is not supported or has no readable text (a scanned image)."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        text = _pdf_text(path)
    elif ext == ".docx":
        text = _docx_text(path)
    else:
        raise CVError("A CV must be a PDF or a Word .docx file.")
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < 80:
        raise CVError("No readable text was found in this CV (a scanned image?). Upload a text PDF or a .docx.")
    return text[:MAX_CV_CHARS], len(text) > MAX_CV_CHARS


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", s.lower()).strip()


def quote_in_text(quote: str, text_norm: str) -> bool:
    q = _norm(quote)
    return len(q) >= MIN_QUOTE_CHARS and q in text_norm


def verify_estimates(items: list[dict], cv_text: str) -> tuple[list[dict], list[str]]:
    """The model's skills -> [{"raw_skill", "rating", "evidence", "verified", "needs_review", "note"}] and warnings.
    verified = the quote really is in the CV. An unverified level is not trusted: it becomes 1 and is flagged. A skill the CV never mentions
    is dropped."""
    text_norm = _norm(cv_text)
    out, warns, seen = [], [], set()
    for it in items:
        skill = str(it.get("skill") or it.get("raw_skill") or "").strip()
        key = skill.lower()
        if not skill or key in seen:
            continue
        if _norm(skill) not in text_norm:
            warns.append(f"{skill}: the CV does not mention this skill, so it was left out.")
            continue
        seen.add(key)
        try:
            level = int(it.get("level", it.get("rating", 1)))
        except (TypeError, ValueError):
            level = 1
        level = max(1, min(level, CV_MAX_LEVEL))
        evidence = str(it.get("evidence") or "").strip()
        ok = quote_in_text(evidence, text_norm)
        note = ""
        if not ok:
            level, note = UNVERIFIED_LEVEL, "the quoted evidence was not found in the CV; level set to 1, please check"
        out.append({"raw_skill": skill, "rating": level, "evidence": evidence if ok else "", "verified": ok, "needs_review": not ok, "note": note})
        if len(out) >= MAX_SKILLS:
            break
    return out, warns


def read_cv(path: Path, filename: str) -> dict:
    """The whole preview for one CV file (without seniority, which the router adds)."""
    text, cut = extract_text(path, filename)
    proposed = tasks.extract_cv(text)
    skills, warns = verify_estimates(proposed["skills"], text)
    if cut:
        warns.insert(0, f"The CV is long; only the first {MAX_CV_CHARS:,} characters were read.")
    if not skills:
        warns.append("No skills could be verified in this CV.")
    return {"name": proposed["name"], "employee_no": proposed["employee_no"], "roles": proposed["roles"],
            "background": proposed["background"], "skills": skills, "warnings": warns, "text_chars": len(text)}
