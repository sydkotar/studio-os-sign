"""
Studio OS -- client-facing e-signature page.

Given a token (URL query param, e.g. ?token=abc123) plus a one-time code the
client was sent separately, shows one filled contract and captures a
signature (drawn + typed name). Writes the result back to the same Supabase
table that the main Studio OS app (running locally on Sydney's Mac) reads
from -- see that app's Studio Dashboard/esignature.py for the other half of
this flow, and the create-table SQL there for the exact schema.

This is a small, separate, public app on purpose: it never touches Sydney's
real client database, pricing rules, or any other business file. Its only
job is: token + code in, one contract shown, one signature captured.

The whole page (UI strings + the contract itself) renders in the language
carried on the pending_signatures row's `language` column ('en' or 'es'),
which the main app sets from the client's preferred language. Spanish uses
the `_es` sibling template; anything else falls back to English.

Secrets required (Streamlit Community Cloud -> App settings -> Secrets):
    SUPABASE_URL = "..."
    SUPABASE_ANON_KEY = "..."   -- the anon/publishable key, never the secret/service_role one
    PROVIDER_NIF = "..."       -- Sydney's NIF, kept as a secret (not hardcoded) since this
                                  repo is public -- a personal tax ID has no business being
                                  visible in source code anyone can read on GitHub.
"""

import base64
import io
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from PIL import Image
from streamlit_drawable_canvas import st_canvas
from supabase import create_client

import contract_pdf

st.set_page_config(page_title="Sign your contract / Firma tu contrato", page_icon="✍️")

TEMPLATES_DIR = Path(__file__).parent / "contract_templates"

# All client-facing copy in one place, keyed by language. `lang()` below picks
# 'es' when the row says so, else falls back to English -- so an unknown or
# missing language never leaves a blank string on the page.
STRINGS = {
    "en": {
        "missing_token": "This link is missing a signing token.",
        "invalid_link": "This signing link isn't valid, or has already expired.",
        "already_signed": "This contract was already signed by {name} on {date}.",
        "expired": "This signing link has expired. Please ask for a new one.",
        "title": "Sign your contract",
        "code_prompt": "Enter the one-time code you were sent separately (e.g. by WhatsApp or email).",
        "code_label": "One-time code",
        "continue": "Continue",
        "code_mismatch": "That code doesn't match. Double check and try again.",
        "sign_below": "Sign below",
        "full_name": "Your full legal name",
        "agree": "I have read and agree to the terms of this agreement.",
        "draw": "Draw your signature:",
        "submit": "Submit signature",
        "need_name": "Please type your full legal name.",
        "need_agree": "Please confirm you agree to the terms.",
        "need_drawing": "Please draw your signature.",
        "thanks": "Thank you! Your signature has been recorded.",
        "quote_ref": "Quotation reference: {ref}",
        "download": "Download your signed contract (PDF)",
        "download_ready": "Your signed copy is ready to download below.",
        "download_error": "Signed, but the PDF couldn't be generated here — Sydney will send you a copy.",
    },
    "es": {
        "missing_token": "A este enlace le falta el token de firma.",
        "invalid_link": "Este enlace de firma no es válido o ya ha caducado.",
        "already_signed": "Este contrato ya fue firmado por {name} el {date}.",
        "expired": "Este enlace de firma ha caducado. Por favor, solicita uno nuevo.",
        "title": "Firma tu contrato",
        "code_prompt": "Introduce el código de un solo uso que recibiste por separado (por ejemplo, por WhatsApp o correo electrónico).",
        "code_label": "Código de un solo uso",
        "continue": "Continuar",
        "code_mismatch": "Ese código no coincide. Compruébalo e inténtalo de nuevo.",
        "sign_below": "Firma aquí",
        "full_name": "Tu nombre y apellidos completos",
        "agree": "He leído y acepto los términos de este acuerdo.",
        "draw": "Dibuja tu firma:",
        "submit": "Enviar firma",
        "need_name": "Por favor, escribe tu nombre y apellidos completos.",
        "need_agree": "Por favor, confirma que aceptas los términos.",
        "need_drawing": "Por favor, dibuja tu firma.",
        "thanks": "¡Gracias! Tu firma ha quedado registrada.",
        "quote_ref": "Referencia de presupuesto: {ref}",
        "download": "Descarga tu contrato firmado (PDF)",
        "download_ready": "Tu copia firmada está lista para descargar abajo.",
        "download_error": "Firmado, pero no se pudo generar el PDF aquí — Sydney te enviará una copia.",
    },
}


def lang_of(row):
    """'es' only when the row explicitly says so, else 'en'."""
    return "es" if str(row.get("language") or "en").lower().startswith("es") else "en"


def signed_pdf_bytes(row, signature_bytes, language):
    """Render the signed contract to PDF bytes so the client can download it.
    Uses PROVIDER_NIF (required secret) + optional CONTACT_EMAIL/CONTACT_PHONE
    secrets for the footer. Returns bytes, or None if anything is missing/fails
    (the caller then tells the client Sydney will send a copy)."""
    try:
        signed_date = None
        if row.get("signed_at"):
            signed_date = datetime.fromisoformat(row["signed_at"]).date().isoformat()
        return contract_pdf.render_signed_contract_bytes(
            contract_type=row["contract_type"],
            client_name=row["client_name"],
            provider_nif=st.secrets["PROVIDER_NIF"],
            client_company=row.get("client_company"),
            client_address=row.get("client_address"),
            quotation_reference=row.get("quotation_reference"),
            event_location=row.get("event_location"),
            signed_by_name=row.get("signed_by_name"),
            signed_date=signed_date,
            signature_image_bytes=signature_bytes,
            language=language,
            contact_email=st.secrets.get("CONTACT_EMAIL"),
            contact_phone=st.secrets.get("CONTACT_PHONE"),
            provider_signature_bytes=_provider_sig_bytes(),
            provider_signature_date=datetime.now(timezone.utc).strftime("%d/%m/%Y"),
        )
    except Exception:
        return None


def _provider_sig_bytes():
    """Sydney's pre-embedded signature for the counter-signed copy, from the
    PROVIDER_SIGNATURE_B64 secret (base64). Kept out of this public repo. Returns
    bytes or None (block renders blank if the secret isn't set)."""
    try:
        import base64 as _b64
        raw = st.secrets.get("PROVIDER_SIGNATURE_B64")
        return _b64.b64decode(raw) if raw else None
    except Exception:
        return None


def _download_name(client_name):
    safe = "".join(ch if ch.isalnum() else "-" for ch in (client_name or "contrato")).strip("-")
    return f"contrato-firmado-{safe or 'contrato'}.pdf"


@st.cache_resource
def get_client():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_ANON_KEY"])


def fill_contract_markdown(contract_type, row, language, t):
    """
    Mirrors the placeholder-fill step in the main app's contract_generator.py,
    but renders to Markdown for on-screen display instead of a PDF -- the
    templates' ##/###/-/**bold** markup is already valid Markdown, so only
    the [[TABLE]] block needs converting. Signature/date placeholders always
    render blank here since the client hasn't signed yet at this point.

    Spanish reads the `_es` sibling template; if it's somehow missing the
    English base is used so the page never breaks.
    """
    template = TEMPLATES_DIR / f"{contract_type}_template.txt"
    if language == "es":
        es_template = TEMPLATES_DIR / f"{contract_type}_template_es.txt"
        if es_template.exists():
            template = es_template
    text = template.read_text(encoding="utf-8")
    text = text.replace("{{provider_nif}}", st.secrets["PROVIDER_NIF"])
    text = text.replace("{{client_name}}", row["client_name"])
    text = text.replace("{{client_company_line}}", row["client_company"] or "_")
    text = text.replace("{{client_address}}", row["client_address"] or "_")
    quote_line = t["quote_ref"].format(ref=row["quotation_reference"]) if row["quotation_reference"] else ""
    text = text.replace("{{quotation_reference_line}}", quote_line)
    if contract_type == "retreat":
        loc = row["event_location"] or "_"
        text = text.replace("{{location}}", loc)
        text = text.replace("{{ubicacion}}", loc)
    text = text.replace("{{client_signature_line}}", "_")
    text = text.replace("{{client_signature_date}}", "_")
    text = text.replace("[[SIGNATURE_IMAGE]]", "")

    lines = text.split("\n")
    out = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "[[TABLE]]":
            i += 1
            table_rows = []
            while i < len(lines) and lines[i].strip() != "[[/TABLE]]":
                table_rows.append([c.strip() for c in lines[i].split("|")])
                i += 1
            i += 1
            if table_rows:
                out.append("| " + " | ".join(table_rows[0]) + " |")
                out.append("|" + "---|" * len(table_rows[0]))
                for r in table_rows[1:]:
                    out.append("| " + " | ".join(r) + " |")
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


token = st.query_params.get("token")
sb = get_client()

# Before we have a row we don't know the language, so the very first two
# guard messages (missing/invalid token) show both languages.
if not token:
    st.error(STRINGS["en"]["missing_token"] + "  /  " + STRINGS["es"]["missing_token"])
    st.stop()

res = sb.table("pending_signatures").select("*").eq("token", token).execute()
rows = res.data or []
if not rows:
    st.error(STRINGS["en"]["invalid_link"] + "  /  " + STRINGS["es"]["invalid_link"])
    st.stop()
row = rows[0]

language = lang_of(row)
t = STRINGS[language]

if row["status"] == "signed":
    signed_date = datetime.fromisoformat(row["signed_at"]).date().isoformat()
    st.success(t["already_signed"].format(name=row["signed_by_name"], date=signed_date))
    _sig = None
    if row.get("signature_image_base64"):
        try:
            _sig = base64.b64decode(row["signature_image_base64"])
        except Exception:
            _sig = None
    _pdf = signed_pdf_bytes(row, _sig, language) if _sig else None
    if _pdf:
        st.download_button(t["download"], _pdf, file_name=_download_name(row["client_name"]),
                           mime="application/pdf")
    st.stop()

expires_at = datetime.fromisoformat(row["expires_at"])
if datetime.now(timezone.utc) > expires_at:
    st.error(t["expired"])
    st.stop()

st.title(t["title"])

if not st.session_state.get("code_verified"):
    st.write(t["code_prompt"])
    code_input = st.text_input(t["code_label"], max_chars=6)
    if st.button(t["continue"]):
        if code_input.strip() == row["one_time_code"]:
            st.session_state.code_verified = True
            st.rerun()
        else:
            st.error(t["code_mismatch"])
    st.stop()

st.markdown(fill_contract_markdown(row["contract_type"], row, language, t))

st.divider()
st.subheader(t["sign_below"])
name_input = st.text_input(t["full_name"])
agree = st.checkbox(t["agree"])
st.write(t["draw"])
canvas_result = st_canvas(
    fill_color="rgba(0,0,0,0)",
    stroke_width=3,
    stroke_color="#000000",
    background_color="#FFFFFF",
    height=150,
    width=400,
    drawing_mode="freedraw",
    key="signature_canvas",
)

if st.button(t["submit"]):
    has_drawing = canvas_result.image_data is not None and canvas_result.image_data[:, :, 3].sum() > 0
    if not name_input.strip():
        st.error(t["need_name"])
    elif not agree:
        st.error(t["need_agree"])
    elif not has_drawing:
        st.error(t["need_drawing"])
    else:
        img = Image.fromarray(canvas_result.image_data.astype("uint8"), mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        signature_bytes = buf.getvalue()
        signature_b64 = base64.b64encode(signature_bytes).decode("ascii")
        _signed_at = datetime.now(timezone.utc).isoformat()
        sb.table("pending_signatures").update({
            "status": "signed",
            "signed_by_name": name_input.strip(),
            "signature_image_base64": signature_b64,
            "signed_at": _signed_at,
        }).eq("token", token).execute()
        st.success(t["thanks"])
        st.balloons()
        # Let the client download their signed copy immediately.
        _signed_row = {**row, "signed_by_name": name_input.strip(), "signed_at": _signed_at}
        _pdf = signed_pdf_bytes(_signed_row, signature_bytes, language)
        if _pdf:
            st.write(t["download_ready"])
            st.download_button(t["download"], _pdf, file_name=_download_name(row["client_name"]),
                               mime="application/pdf")
        else:
            st.info(t["download_error"])
