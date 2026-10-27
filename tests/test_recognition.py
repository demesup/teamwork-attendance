from datetime import date

from app.recognition import (
    OcrBox,
    analyse,
    find_date,
    fold,
    group_into_lines,
    match_names,
    roster_student,
    session_marker,
)


def student(student_id: str, first: str, last: str, group: str = "Grupa 1281"):
    return roster_student(student_id, first, last, group)


ROSTER = [
    student("khalifa", "Tawfek", "KHALIFA"),
    student("chintoiu", "Vlăduț-Andrei", "CHINTOIU L.C.", "1241EA"),
    student("fekete", "Anna-Maria", "FEKETE S.", "1241EA"),
    student("budu", "Octavian-Andrei", "BUDU N.", "1241EA"),
    student("ozkan", "Şerife Hale", "ÖZKAN", "1281"),
    student("uner", "Kardelen", "ÜNER", "1281"),
]


def box(text: str, x: float, y: float, height: float = 20) -> OcrBox:
    return OcrBox(text=text, x=x, y=y, height=height)


def test_fold_removes_accents_and_punctuation():
    assert fold("ÖZKAN Şerife-Hale") == "ozkan serife hale"
    assert fold("Ș.A. Corina") == "s a corina"


def test_typos_still_match_the_right_student():
    lines = group_into_lines([box("CHINTOIU VEADUT -ANDREI", 0, 10), box("() FEKCTE AaNA-MARIA", 0, 40)])
    result = match_names(lines, ROSTER)
    matched = {entry["student_id"] for entry in result["matched"]}
    assert matched == {"chintoiu", "fekete"}
    assert result["unmatched"] == []


def test_group_code_on_the_line_breaks_ties():
    roster = [student("a", "Ioana", "POP", "1241EA"), student("b", "Ioana", "POP", "1241EB")]
    result = match_names(group_into_lines([box("Ioana POP 1241EB", 0, 10)]), roster)
    assert [entry["student_id"] for entry in result["matched"]] == ["b"]


def test_a_student_is_matched_once_and_the_rest_go_to_review_with_options():
    lines = group_into_lines([box("Tawfek KHALIFA", 0, 10), box("KHALIFA Tawfek", 0, 40)])
    result = match_names(lines, ROSTER)
    assert len(result["matched"]) == 1
    assert len(result["review"]) == 1
    assert result["review"][0]["candidates"][0]["student_id"] == "khalifa"


def test_numbered_items_on_one_ocr_box_are_split_into_rows():
    lines = group_into_lines([box("2) BUDU OCTAVIAN-ANDREI 3) KHALIFA TAWFEK", 0, 10)])
    assert [line.text for line in lines] == ["2) BUDU OCTAVIAN-ANDREI", "3) KHALIFA TAWFEK"]


def test_nearby_rows_stay_separate():
    lines = group_into_lines([box("7) MITRAN", 0, 241, 35), box("8) DAOIDO", 0, 258, 35)])
    assert [line.text for line in lines] == ["7) MITRAN", "8) DAOIDO"]


def test_session_markers_are_recognised():
    assert session_marker(group_into_lines([box("S2", 0, 10)])[0]) == "S2"
    assert session_marker(group_into_lines([box("s 1", 0, 10)])[0]) == "S1"
    assert session_marker(group_into_lines([box("S3", 0, 10)])[0]) is None


def test_sheet_with_two_sessions_is_split_at_the_second_marker():
    boxes = [
        box("Khalifa Tawfek", 0, 100),
        box("S2", 0, 300),
        box("Ozkan Serife Hale", 0, 320),
        box("Uner Kardelen", 0, 340),
    ]
    result = analyse(boxes, ROSTER, date(2026, 10, 26), default_year=2026)
    assert [part["label"] for part in result["sessions"]] == ["S1", "S2"]
    assert [m["student_id"] for m in result["sessions"][0]["matched"]] == ["khalifa"]
    assert {m["student_id"] for m in result["sessions"][1]["matched"]} == {"ozkan", "uner"}


def test_dates_are_read_from_numeric_or_month_name_text():
    assert find_date(group_into_lines([box("26.10.2026", 0, 10)]), 2020) == date(2026, 10, 26)
    assert find_date(group_into_lines([box("26 Oct", 0, 10)]), 2026) == date(2026, 10, 26)
    assert find_date(group_into_lines([box("nothing here", 0, 10)]), 2026) is None


def test_numbered_rows_without_a_name_are_reported_and_gaps_are_listed():
    lines = group_into_lines(
        [
            box("1) Khalifa Tawfek", 0, 10),
            box("2) ", 0, 40),  # the number is there, the name is not
            box("4) Uner Kardelen", 0, 100),
        ]
    )
    result = match_names(lines, ROSTER)
    assert [entry["row"] for entry in result["unnamed"]] == [2]
    assert result["missing_rows"] == [3]


def test_students_the_sheet_does_not_name_are_listed_as_unassigned():
    boxes = [box("Khalifa Tawfek", 0, 100), box("Uner Kardelen", 0, 120)]
    result = analyse(boxes, ROSTER, date(2026, 10, 26), default_year=2026)
    unassigned = {entry["student_id"] for entry in result["sessions"][0]["unassigned"]}
    assert unassigned == {"chintoiu", "fekete", "budu", "ozkan"}


def test_unrecognised_rows_are_flagged_with_their_boxes_and_drawn_in_red():
    import io

    from PIL import Image

    from app.recognition import annotate

    rect = (100.0, 100.0, 300.0, 140.0)
    boxes = [OcrBox(text="Nobody Known", x=100, y=120, height=40, rect=rect)]
    result = analyse(boxes, ROSTER, date(2026, 10, 26), default_year=2026)
    assert [mark["rect"] for mark in result["flagged"]] == [rect]

    picture = Image.open(io.BytesIO(annotate(_blank_png(), [], [{"rect": rect, "label": None}]))).convert("RGB")
    assert picture.getpixel((96, 120)) == (220, 0, 0)  # on the left edge, drawn 4px outside the box (2x image)
    assert picture.getpixel((200, 120)) == (255, 255, 255)  # inside it: untouched


def test_read_rows_are_confirmed_in_green_and_unclear_rows_name_the_best_guess():
    rect_ok = (100.0, 100.0, 400.0, 140.0)
    rect_unclear = (100.0, 200.0, 400.0, 240.0)
    boxes = [
        OcrBox(text="Khalifa Tawfek", x=100, y=120, height=40, rect=rect_ok),
        OcrBox(text="Tawfk Khalfa", x=100, y=220, height=40, rect=rect_unclear),
    ]
    result = analyse(boxes, ROSTER, date(2026, 10, 26), default_year=2026)
    assert result["confirmed"] == [rect_ok]
    assert [mark["rect"] for mark in result["flagged"]] == [rect_unclear]
    assert result["flagged"][0]["label"] == "KHALIFA Tawfek"


def _blank_png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (400, 300), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()
