"""Read an attendance sheet photo with free, offline tools: RapidOCR for text, rapidfuzz for names.

Nothing here writes to disk. The image bytes are read, turned into text lines, and dropped.
"""

import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont
from rapidfuzz import fuzz

# token_sort_ratio scores (0-100). Typos cost a few points, so a clean-enough match is accepted outright
# and a weaker one is shown to the professor to confirm.
ACCEPT_SCORE = 80
REVIEW_SCORE = 60
# Weaker than REVIEW_SCORE: shown as a suggestion on lines that could not be matched at all.
SUGGESTION_SCORE = 50
MIN_NAME_CHARS = 5
# A numbered list item such as "4)" starts a new row, even when the OCR joined it onto the previous one.
ITEM_START = re.compile(r"\s+(?=\d{1,2}\s*\))")
ROW_NUMBER = re.compile(r"\W*(\d{1,2})\s*[\)\.]")
GROUP_BONUS = 3
# Words printed on the sheet that are never names.
STOPWORDS = {"nume", "prenume", "grupa", "data", "semnatura", "prezenta", "nr", "crt", "erasmus", "sesiunea", "sesiune", "ora"}
MONTHS = {
    "ian": 1, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "mai": 5, "may": 5, "iun": 6, "jun": 6,
    "iul": 7, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "noi": 11, "nov": 11, "dec": 12,
}
GROUP_TOKEN = re.compile(r"\d{3,4}[a-z]{0,2}")
SESSION_MARKER = re.compile(r"s\s*([12])")
NUMERIC_DATE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4}|\d{2})\b")
WORD_DATE = re.compile(r"\b(\d{1,2})\s*([a-z]{3,})\b")


@dataclass(frozen=True)
class OcrBox:
    text: str
    x: float
    y: float
    height: float
    # left, top, right, bottom on the (2x) image the OCR read.
    rect: tuple[float, float, float, float] = (0, 0, 0, 0)


@dataclass(frozen=True)
class OcrLine:
    text: str
    y: float
    boxes: tuple[OcrBox, ...] = ()


@dataclass(frozen=True)
class RosterStudent:
    id: str
    name: str
    keys: tuple[str, ...]
    # Group codes of every class the student is in ("1241EA"), and the class names.
    groups: tuple[str, ...]
    classes: tuple[str, ...]


def fold(text: str) -> str:
    """Lowercase, accents removed, punctuation turned into spaces: 'Ș.A. Ö-Mer' becomes 's a o mer'."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def group_code(text: str) -> str | None:
    match = GROUP_TOKEN.search(fold(text))
    return match.group(0) if match else None


def roster_student(student_id: str, first_name: str, last_name: str, section_names: str | list[str]) -> RosterStudent:
    classes = [section_names] if isinstance(section_names, str) else list(section_names)
    groups = tuple(code for code in (group_code(name) for name in classes) if code)
    return RosterStudent(
        id=student_id,
        name=f"{last_name} {first_name}".strip(),
        keys=(fold(f"{last_name} {first_name}"), fold(f"{first_name} {last_name}")),
        groups=groups,
        classes=tuple(classes),
    )


@lru_cache(maxsize=1)
def _ocr_engine():
    # The first call loads the models (a few seconds); later scans reuse the engine.
    from rapidocr import RapidOCR

    return RapidOCR()


def _upscaled_png(image_bytes: bytes) -> bytes:
    """Handwriting is found much more reliably at 2x; the coordinates are only compared with each other."""
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if max(image.size) >= 2000:
        return image_bytes
    buffer = io.BytesIO()
    image.resize((image.width * 2, image.height * 2), Image.LANCZOS).save(buffer, format="PNG")
    return buffer.getvalue()


def read_boxes(image_bytes: bytes) -> list[OcrBox]:
    result = _ocr_engine()(_upscaled_png(image_bytes), text_score=0.3)
    if result is None or result.txts is None:
        return []
    boxes = []
    for points, text in zip(result.boxes, result.txts):
        if not text.strip():
            continue
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        boxes.append(
            OcrBox(
                text=text.strip(),
                x=min(xs),
                y=(min(ys) + max(ys)) / 2,
                height=max(ys) - min(ys),
                rect=(min(xs), min(ys), max(xs), max(ys)),
            )
        )
    return boxes


def group_into_lines(boxes: list[OcrBox]) -> list[OcrLine]:
    """Join boxes that sit on the same row of the sheet, left to right."""
    rows: list[list[OcrBox]] = []
    for box in sorted(boxes, key=lambda item: item.y):
        if rows:
            row = rows[-1]
            row_y = sum(item.y for item in row) / len(row)
            row_height = max(item.height for item in row)
            # Handwritten rows sit close together, so only boxes almost level with the row join it.
            if abs(box.y - row_y) <= 0.3 * max(row_height, box.height):
                row.append(box)
                continue
        rows.append([box])
    lines = []
    for row in rows:
        ordered = sorted(row, key=lambda item: item.x)
        row_y = sum(item.y for item in row) / len(row)
        for part in ITEM_START.split(" ".join(item.text for item in ordered)):
            lines.append(OcrLine(text=part, y=row_y, boxes=tuple(ordered)))
    return lines


def session_marker(line: OcrLine) -> str | None:
    """A line that is just 'S1' or 'S2' (the sheet's session headings)."""
    match = SESSION_MARKER.fullmatch(fold(line.text))
    return f"S{match.group(1)}" if match else None


def find_date(lines: list[OcrLine], default_year: int) -> date | None:
    """Accepts 26.10.2026 and '26 Oct' (year defaults to the current one). Returns None when nothing matches."""
    for line in lines:
        numeric = NUMERIC_DATE.search(line.text)
        if numeric:
            day, month, year = (int(part) for part in numeric.groups())
            year = year + 2000 if year < 100 else year
            return _valid_date(year, month, day)
        for word in WORD_DATE.finditer(fold(line.text)):
            month = MONTHS.get(word.group(2)[:3])
            if month:
                return _valid_date(default_year, month, int(word.group(1)))
    return None


def _valid_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def clean_tokens(text: str) -> tuple[list[str], str | None]:
    """Name tokens of a line, without numbering, group codes, or sheet words. Also returns the group code if present."""
    group = None
    tokens = []
    for token in fold(text).split():
        if GROUP_TOKEN.fullmatch(token) and len(token) >= 4 and group is None:
            group = token
        elif len(token) > 1 and not token.isdigit() and token not in STOPWORDS:
            tokens.append(token)
    return tokens, group


def row_number(text: str) -> int | None:
    """The list number at the start of a row, such as '4)' or '12.'."""
    match = ROW_NUMBER.match(text.strip())
    return int(match.group(1)) if match else None


def _group_adjustment(line_group: str | None, student: RosterStudent) -> float:
    if line_group is None or not student.groups:
        return 0
    if any(fuzz.ratio(line_group, group) >= 80 for group in student.groups):
        return GROUP_BONUS
    return -GROUP_BONUS


def match_names(lines: list[OcrLine], roster: list[RosterStudent]) -> dict:
    """Assign each sheet line to at most one student, best matches first. Typos lower the score but still match."""
    per_line: list[list[tuple[float, int]]] = []
    for line in lines:
        tokens, line_group = clean_tokens(line.text)
        options: list[tuple[float, int]] = []
        if sum(len(token) for token in tokens) >= MIN_NAME_CHARS:
            text = " ".join(tokens)
            for index, student in enumerate(roster):
                score = max(fuzz.token_sort_ratio(text, key) for key in student.keys)
                score += _group_adjustment(line_group, student)
                if score >= SUGGESTION_SCORE:
                    options.append((min(score, 100.0), index))
        per_line.append(sorted(options, reverse=True)[:3])

    taken_lines: set[int] = set()
    taken_students: set[int] = set()
    assignments = sorted(
        (
            (score, line_index, student_index)
            for line_index, options in enumerate(per_line)
            for score, student_index in options
            if score >= REVIEW_SCORE
        ),
        reverse=True,
    )
    matched, review = [], []
    for score, line_index, student_index in assignments:
        if line_index in taken_lines or student_index in taken_students:
            continue
        taken_lines.add(line_index)
        taken_students.add(student_index)
        student = roster[student_index]
        entry = {
            "student_id": student.id,
            "name": student.name,
            "classes": list(student.classes),
            "score": round(score, 1),
            "line": lines[line_index].text,
        }
        (matched if score >= ACCEPT_SCORE else review).append(entry)

    unmatched = []
    unnamed = []
    for line_index, line in enumerate(lines):
        if line_index in taken_lines:
            continue
        number = row_number(line.text)
        if number is not None and not clean_tokens(line.text)[0]:
            # A numbered row with no readable name, e.g. the student wrote only a signature.
            unnamed.append({"row": number, "line": line.text})
        elif per_line[line_index]:
            # Only a student who was already claimed by another line: show the options instead of guessing.
            review.append(
                {
                    "line": line.text,
                    "candidates": [
                        {
                            "student_id": roster[index].id,
                            "name": roster[index].name,
                            "classes": list(roster[index].classes),
                            "score": round(score, 1),
                        }
                        for score, index in per_line[line_index]
                    ],
                }
            )
        elif any(len(token) >= 3 and token.isalpha() for token in clean_tokens(line.text)[0]):
            # A word of letters: a name the app could not place. Dates such as "0ct" are not names.
            unmatched.append(line.text)
    numbers = {row_number(line.text) for line in lines} - {None}
    missing = sorted(set(range(min(numbers), max(numbers) + 1)) - numbers) if numbers else []
    return {"matched": matched, "review": review, "unmatched": unmatched, "unnamed": unnamed, "missing_rows": missing}


def analyse(boxes: list[OcrBox], roster: list[RosterStudent], session_date: date | None, default_year: int) -> dict:
    """Split the sheet into sessions by their S1/S2 markers, then match names inside each part."""
    lines = group_into_lines(boxes)
    markers = sorted((line.y, session_marker(line), index) for index, line in enumerate(lines) if session_marker(line))
    marker_rows = {index for _, _, index in markers}
    body = [line for index, line in enumerate(lines) if index not in marker_rows]

    segments: list[tuple[str | None, list[OcrLine]]] = []
    if not markers:
        segments.append((None, body))
    else:
        # A sheet whose first marker is S2 had its S1 part above it, so that part is labelled S1 too.
        if markers[0][1] == "S2":
            segments.append(("S1", [line for line in body if line.y < markers[0][0]]))
        for position, (y, label, _) in enumerate(markers):
            end = markers[position + 1][0] if position + 1 < len(markers) else float("inf")
            segments.append((label, [line for line in body if y <= line.y < end]))

    detected = find_date(lines, default_year)
    sessions = []
    for label, part in segments:
        result = match_names(part, roster)
        # Roster students this part of the sheet did not name: the professor picks them for blank rows.
        named = {entry["student_id"] for entry in result["matched"]}
        named |= {entry["student_id"] for entry in result["review"] if "student_id" in entry}
        result["unassigned"] = [{"student_id": s.id, "name": s.name, "classes": list(s.classes)} for s in roster if s.id not in named]
        sessions.append({"label": label, **result})
    # Green: rows read with confidence. Red: rows that need a look, labelled with the best guess.
    confirmed_text: set[str] = set()
    flagged: dict[str, str | None] = {}
    for part in sessions:
        confirmed_text |= {entry["line"] for entry in part["matched"]}
        for entry in part["review"]:
            candidates = entry.get("candidates") or [{"name": entry.get("name")}]
            flagged[entry["line"]] = candidates[0].get("name")
        for entry in part["unnamed"]:
            flagged[entry["line"]] = f"row {entry['row']}: no name"
        for line_text in part["unmatched"]:
            flagged.setdefault(line_text, None)
    confirmed_text -= flagged.keys()
    confirmed = [box.rect for line in lines if line.text in confirmed_text for box in line.boxes]
    marks = [{"rect": box.rect, "label": flagged[line.text]} for line in lines if line.text in flagged for box in line.boxes]
    return {
        "session_date": (session_date or detected).isoformat() if (session_date or detected) else None,
        "sessions": sessions,
        "lines": [line.text for line in lines],
        "confirmed": confirmed,
        "flagged": marks,
    }


GREEN = (30, 150, 60)
RED = (220, 0, 0)


def _label_font(image_width: int):
    size = max(14, image_width // 70)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow older than 10.1 has only the fixed-size default font
        return ImageFont.load_default()


def annotate(image_bytes: bytes, confirmed: list, flagged: list[dict]) -> bytes:
    """The sheet as PNG: green boxes for rows read, red boxes with the best guess for rows to check.

    Coordinates match read_boxes, which reads the same upscaled image.
    """
    image = Image.open(io.BytesIO(_upscaled_png(image_bytes))).convert("RGB")
    draw = ImageDraw.Draw(image)
    width = max(2, image.width // 400)
    font = _label_font(image.width)
    placed: list[tuple[float, float, float, float]] = []
    for rect in confirmed:
        _outline(draw, rect, GREEN, width)
    for mark in flagged:
        left, top = mark["rect"][0], mark["rect"][1]
        _outline(draw, mark["rect"], RED, width)
        if mark.get("label"):
            text = f"? {_ascii(mark['label'])}"  # the default font has no accented letters
            y = max(0, top - font.size - 12)
            box = draw.textbbox((left, y), text, font=font)
            while any(_overlaps(box, other) for other in placed):  # two guesses for neighbouring rows
                y += font.size + 4
                box = draw.textbbox((left, y), text, font=font)
            placed.append(box)
            draw.text((left, y), text, fill=RED, font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _ascii(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _overlaps(first, second) -> bool:
    return first[0] < second[2] and second[0] < first[2] and first[1] < second[3] and second[1] < first[3]


def _outline(draw: ImageDraw.ImageDraw, rect, colour, width: int) -> None:
    left, top, right, bottom = rect
    draw.rectangle((left - 4, top - 4, right + 4, bottom + 4), outline=colour, width=width)
