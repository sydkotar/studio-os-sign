"""
Signed-contract PDF renderer for the public signing page.

A self-contained port of the main Studio OS app's contract_generator.py so the
client can download the SAME styled, paginated signed contract the moment they
finish signing -- without this public repo importing the private app or
hardcoding any personal data. Provider NIF and contact details are passed in
from Streamlit secrets by the caller (never committed here). Colors are the only
constants inlined (not sensitive).

Keep this in sync with the main app's contract_generator.py if that renderer
changes -- both fill the same contract_templates/*.txt with the same markup.
"""

import io
import re
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, ListFlowable, ListItem, Table, TableStyle, Image,
)

TEMPLATES_DIR = Path(__file__).parent / "contract_templates"
BEIGE = HexColor("#C9BFAE")
DARK = HexColor("#2B2B2B")

TEMPLATE_FILES = {
    "retreat": TEMPLATES_DIR / "retreat_template.txt",
    "event": TEMPLATES_DIR / "event_template.txt",
}

_H1 = ParagraphStyle(name="contract_h1", fontName="Helvetica-Bold", fontSize=16,
                     leading=20, alignment=1, textColor=DARK, spaceAfter=10)
_H2 = ParagraphStyle(name="contract_h2", fontName="Helvetica-Bold", fontSize=11,
                     leading=14, textColor=DARK, spaceBefore=12, spaceAfter=6)
_H3 = ParagraphStyle(name="contract_h3", fontName="Helvetica-BoldOblique", fontSize=9.5,
                     leading=12, textColor=DARK, spaceBefore=6, spaceAfter=3)
_BODY = ParagraphStyle(name="contract_body", fontName="Helvetica", fontSize=9.5,
                       leading=13, textColor=DARK, spaceAfter=6)
_BULLET = ParagraphStyle(name="contract_bullet", parent=_BODY, spaceAfter=2)
_TABLE_HEADER = ParagraphStyle(name="contract_table_header", fontName="Helvetica-Bold",
                               fontSize=8.5, leading=11, textColor=DARK)
_TABLE_CELL = ParagraphStyle(name="contract_table_cell", fontName="Helvetica",
                             fontSize=8.5, leading=11, textColor=DARK)


def _template_path(contract_type, language):
    base = TEMPLATE_FILES[contract_type]
    if (language or "en").lower().startswith("es"):
        es = base.with_name(f"{base.stem}_es{base.suffix}")
        if es.exists():
            return es
    return base


def _inline(text):
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def _build_table(rows):
    data = [[Paragraph(_inline(c), _TABLE_HEADER) for c in rows[0]]]
    for r in rows[1:]:
        data.append([Paragraph(_inline(c), _TABLE_CELL) for c in r])
    table = Table(data)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BEIGE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, DARK),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, DARK),
    ]))
    return table


def _signature_image_flowable(image_bytes):
    img = Image(io.BytesIO(image_bytes))
    max_width, max_height = 5 * cm, 2 * cm
    scale = min(max_width / img.imageWidth, max_height / img.imageHeight, 1.0)
    img.drawWidth = img.imageWidth * scale
    img.drawHeight = img.imageHeight * scale
    return img


def _parse_template(text, signature_image_bytes=None):
    flowables = []
    lines = text.split("\n")
    para_buffer = []

    def flush_paragraph():
        if para_buffer:
            joined = " ".join(l.strip() for l in para_buffer).strip()
            if joined:
                flowables.append(Paragraph(_inline(joined), _BODY))
            para_buffer.clear()

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "" or stripped == "---":
            flush_paragraph(); i += 1; continue
        if stripped.startswith("### "):
            flush_paragraph(); flowables.append(Paragraph(_inline(stripped[4:].strip()), _H3)); i += 1; continue
        if stripped.startswith("## "):
            flush_paragraph(); flowables.append(Paragraph(_inline(stripped[3:].strip()), _H2)); i += 1; continue
        if stripped.startswith("# "):
            flush_paragraph(); flowables.append(Paragraph(_inline(stripped[2:].strip()), _H1)); i += 1; continue
        if stripped == "[[SIGNATURE_IMAGE]]":
            flush_paragraph()
            if signature_image_bytes:
                flowables.append(_signature_image_flowable(signature_image_bytes))
            i += 1; continue
        if stripped == "[[TABLE]]":
            flush_paragraph(); i += 1
            rows = []
            while i < len(lines) and lines[i].strip() != "[[/TABLE]]":
                rows.append([c.strip() for c in lines[i].split("|")]); i += 1
            i += 1
            flowables.append(_build_table(rows)); continue
        if stripped.startswith("- "):
            flush_paragraph()
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(ListItem(Paragraph(_inline(lines[i].strip()[2:].strip()), _BULLET))); i += 1
            flowables.append(ListFlowable(items, bulletType="bullet", leftIndent=14, spaceAfter=8)); continue
        para_buffer.append(lines[i]); i += 1

    flush_paragraph()
    return flowables


def render_signed_contract_bytes(
    contract_type, client_name, provider_nif,
    client_company=None, client_address=None, quotation_reference=None,
    event_location=None, signed_by_name=None, signed_date=None,
    signature_image_bytes=None, language="en",
    contact_email=None, contact_phone=None,
):
    """Render the filled (and, when a signature is given, signed) contract to PDF
    bytes. `provider_nif` is required (from st.secrets). contact_email/phone are
    optional -- included in the footer only when provided, so no PII is needed in
    source. Mirrors contract_generator.render_contract_pdf's fill logic exactly."""
    template_text = _template_path(contract_type, language).read_text(encoding="utf-8")
    is_es = (language or "en").lower().startswith("es")
    filled = template_text
    filled = filled.replace("{{provider_nif}}", provider_nif or "")
    filled = filled.replace("{{client_name}}", client_name)
    filled = filled.replace("{{client_company_line}}", client_company or "_")
    filled = filled.replace("{{client_address}}", client_address or "_")
    quote_label = "Referencia del presupuesto" if is_es else "Quotation reference"
    quote_line = f"{quote_label}: {quotation_reference}" if quotation_reference else ""
    filled = filled.replace("{{quotation_reference_line}}", quote_line)
    if contract_type == "retreat":
        filled = filled.replace("{{location}}", event_location or "_")
        filled = filled.replace("{{ubicacion}}", event_location or "_")
    filled = filled.replace("{{client_signature_line}}", "" if signed_by_name else "_")
    filled = filled.replace("{{client_signature_date}}", signed_date or "_")

    flowables = _parse_template(filled, signature_image_bytes=signature_image_bytes)

    footer_left = f"{contact_email} · {contact_phone}" if (contact_email and contact_phone) else \
        (contact_email or contact_phone or "")

    def _header_footer(canvas_obj, doc):
        width, _h = A4
        canvas_obj.saveState()
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.setFillColor(DARK)
        if footer_left:
            canvas_obj.drawString(2 * cm, 1.3 * cm, footer_left)
        canvas_obj.drawRightString(width - 2 * cm, 1.3 * cm, f"Page {doc.page}")
        canvas_obj.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=2.3 * cm, bottomMargin=2 * cm, leftMargin=2.2 * cm, rightMargin=2.2 * cm,
    )
    doc.build(flowables, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return buf.getvalue()
