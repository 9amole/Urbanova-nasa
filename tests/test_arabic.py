"""D1: Arabic planning documents (text layer) — values extracted, output in English, wording kept."""

import re
from pathlib import Path

import pytest

CASES = [
    ("نسبة البناء لا تزيد عن ٤٠٪ من صافي مساحة الأرض.", "max_building_coverage_pct", 40.0, "medium"),
    ("أقصى ارتفاع للمباني ١٨ متر.", "max_height_m", 18.0, "medium"),
    ("لا يزيد عدد الأدوار عن ٦ أدوار.", "max_floors", 6.0, "medium"),
    ("الارتفاع أرضي + ٥ أدوار.", "max_floors", 6.0, "low"),  # G+5 = 6 floors, interpretation noted
    ("الردود الجانبية لا تقل عن ٣ متر.", "min_setback_m", 3.0, "medium"),
    ("المساحات الخضراء لا تقل عن ٢٠٪ من مساحة الأرض.", "min_green_space_pct", 20.0, "medium"),
    ("المساحات المفتوحة لا تقل عن ٣٠٪.", "min_open_space_pct", 30.0, "medium"),
    ("معامل الاستغلال ١٫٥.", "max_far", 1.5, "medium"),
    ("مساحة الأرض ٤٢٬٠٠٠ م٢.", "plot_area_m2", 42000.0, "medium"),
    ("مساحة الأرض ١٠ فدان.", "plot_area_m2", 42008.3, "low"),  # converted from feddan, conversion noted
    ("بحد أقصى ٢ أدوار بدروم للانتظار.", "max_floors", 2.0, "low"),  # basement levels, flagged
    ("يفضل أن تكون نسبة البناء ٣٥٪ من مساحة الأرض.", "max_building_coverage_pct", 35.0, "low"),  # advisory wording
]


@pytest.mark.parametrize("text, rule_type, value, confidence", CASES)
def test_arabic_conditions_are_extracted(text, rule_type, value, confidence):
    from urbanova import documents

    hits = [c for c in documents.find_candidates([text]) if c["rule_type"] == rule_type]
    assert hits, documents.find_candidates([text])
    c = hits[-1] if confidence == "low" and len(hits) > 1 else hits[0]
    assert c["value"] == pytest.approx(value, abs=0.1) and c["confidence"] == confidence and c["language"] == "ar"
    # Notes are English; Arabic appears only inside quoted document wording ('...').
    assert all(not documents.ARABIC_LETTER.search(re.sub(r"'[^']*'", "", n)) for n in c["notes"]), c["notes"]


def test_text_values_keep_the_arabic_wording():
    from urbanova import documents

    [use] = [c for c in documents.find_candidates(["الاستخدام المسموح به: سكني مع محلات تجارية بالدور الأرضي."]) if c["rule_type"] == "permitted_land_use"]
    assert use["value_text"] == "سكني مع محلات تجارية بالدور الأرضي"
    [park] = documents.find_candidates(["يوفر موقف سيارة لكل وحدة سكنية."])
    assert park["rule_type"] == "parking_requirement"


def test_arabic_indic_digits_and_separators_are_normalised():
    from urbanova import documents

    text, spans = documents.normalize_page("مساحة الأرض ٤٢٬٠٠٠ م٢ والنسبة ٤٠٪ والمعامل ١٫٥")
    assert "42,000" in text and "40%" in text and "1.5" in text and spans == []


def _arabic_pdf(path: Path, lines: list[str]) -> Path:
    """A PDF whose Arabic text layer extracts in visual (reversed) order, as many real exports do."""
    import matplotlib
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    if "DejaVuAR" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("DejaVuAR", str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf")))
    c = canvas.Canvas(str(path))
    c.setFont("DejaVuAR", 12)
    for i, t in enumerate(lines):
        c.drawRightString(550, 780 - 20 * i, t)
    c.save()
    return path


def test_reversed_arabic_pdf_is_reordered_and_flagged(env, tmp_path):
    from pypdf import PdfReader

    from urbanova.engine import extract_rules

    pdf = _arabic_pdf(tmp_path / "ar.pdf", ["نسبة البناء لا تزيد عن ٤٠٪ من صافي مساحة الأرض.", "أقصى ارتفاع للمباني ١٨ متر.",
                                            "مساحة الأرض ٤٠٬٠٠٠ م٢.", "مساحة الأرض ٤٢٬٠٠٠ م٢ وفقا للمخطط."])
    raw = PdfReader(str(pdf)).pages[0].extract_text()
    assert "٪٠٤" in raw, "precondition: this PDF's text layer really is reversed"
    out = extract_rules("ar.pdf", pdf.read_bytes())
    got = {(c["rule_type"], c["value"]) for c in out["candidates"]}
    assert {("max_building_coverage_pct", 40.0), ("max_height_m", 18.0), ("plot_area_m2", 40000.0), ("plot_area_m2", 42000.0)} <= got
    assert all(c["confidence"] == "low" and any("reversed" in n for n in c["notes"]) for c in out["candidates"])
    assert out["conflicts"] and out["conflicts"][0]["values"] == [40000.0, 42000.0]


def test_english_document_unchanged_by_arabic_support(env):
    from conftest import SAMPLES

    from urbanova.engine import extract_rules

    out = extract_rules("c.pdf", (SAMPLES / "sample_planning_conditions.pdf").read_bytes())
    assert all(c["language"] == "en" for c in out["candidates"])
    assert len(out["candidates"]) == 10


def test_mixed_language_page():
    from urbanova import documents

    page = "Maximum building height: 18 m.\nنسبة البناء لا تزيد عن ٣٥٪ من صافي مساحة الأرض."
    got = {(c["rule_type"], c["value"], c["language"]) for c in documents.find_candidates([page])}
    assert {("max_height_m", 18.0, "en"), ("max_building_coverage_pct", 35.0, "ar")} <= got
