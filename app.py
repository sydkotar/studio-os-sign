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

st.set_page_config(page_title="Sign your contract", page_icon="✍️")

TEMPLATES_DIR = Path(__file__).parent / "contract_templates"


@st.cache_resource
def get_client():
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_ANON_KEY"])


def fill_contract_markdown(contract_type, row):
    """
    Mirrors the placeholder-fill step in the main app's contract_generator.py,
    but renders to Markdown for on-screen display instead of a PDF -- the
    templates' ##/###/-/**bold** markup is already valid Markdown, so only
    the [[TABLE]] block needs converting. Signature/date placeholders always
    render blank here since the client hasn't signed yet at this point.
    """
    text = (TEMPLATES_DIR / f"{contract_type}_template.txt").read_text(encoding="utf-8")
    text = text.replace("{{provider_nif}}", st.secrets["PROVIDER_NIF"])
    text = text.replace("{{client_name}}", row["client_name"])
    text = text.replace("{{client_company_line}}", row["client_company"] or "_")
    text = text.replace("{{client_address}}", row["client_address"] or "_")
    quote_line = f"Quotation reference: {row['quotation_reference']}" if row["quotation_reference"] else ""
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
if not token:
    st.error("This link is missing a signing token.")
    st.stop()

sb = get_client()
res = sb.table("pending_signatures").select("*").eq("token", token).execute()
rows = res.data or []
if not rows:
    st.error("This signing link isn't valid, or has already expired.")
    st.stop()
row = rows[0]

if row["status"] == "signed":
    signed_date = datetime.fromisoformat(row["signed_at"]).date().isoformat()
    st.success(f"This contract was already signed by {row['signed_by_name']} on {signed_date}.")
    st.stop()

expires_at = datetime.fromisoformat(row["expires_at"])
if datetime.now(timezone.utc) > expires_at:
    st.error("This signing link has expired. Please ask for a new one.")
    st.stop()

st.title("Sign your contract")

if not st.session_state.get("code_verified"):
    st.write("Enter the one-time code you were sent separately (e.g. by WhatsApp or email).")
    code_input = st.text_input("One-time code", max_chars=6)
    if st.button("Continue"):
        if code_input.strip() == row["one_time_code"]:
            st.session_state.code_verified = True
            st.rerun()
        else:
            st.error("That code doesn't match. Double check and try again.")
    st.stop()

st.markdown(fill_contract_markdown(row["contract_type"], row))

st.divider()
st.subheader("Sign below")
name_input = st.text_input("Your full legal name")
agree = st.checkbox("I have read and agree to the terms of this agreement.")
st.write("Draw your signature:")
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

if st.button("Submit signature"):
    has_drawing = canvas_result.image_data is not None and canvas_result.image_data[:, :, 3].sum() > 0
    if not name_input.strip():
        st.error("Please type your full legal name.")
    elif not agree:
        st.error("Please confirm you agree to the terms.")
    elif not has_drawing:
        st.error("Please draw your signature.")
    else:
        img = Image.fromarray(canvas_result.image_data.astype("uint8"), mode="RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        signature_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        sb.table("pending_signatures").update({
            "status": "signed",
            "signed_by_name": name_input.strip(),
            "signature_image_base64": signature_b64,
            "signed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("token", token).execute()
        st.success("Thank you! Your signature has been recorded.")
        st.balloons()
