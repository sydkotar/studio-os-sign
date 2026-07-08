# Studio OS -- Sign

Small, standalone client-facing e-signature page for Sydney Kotar's Studio OS.
Given a link (`?token=...`) plus a one-time code sent separately, a client
reads one contract and signs it (drawn signature + typed name). The result
is written to a shared Supabase table that the main Studio OS app (private,
runs locally, not in this repo) reads back.

This repo intentionally contains nothing but this signing page -- no client
data, no business logic, no pricing rules.

## Deploy (Streamlit Community Cloud)

1. Connect this repo at [share.streamlit.io](https://share.streamlit.io).
2. In the app's Settings -> Secrets, add:
   ```
   SUPABASE_URL = "https://oyrdxyrwdbqhmkpjjlks.supabase.co"
   SUPABASE_ANON_KEY = "<the anon/publishable key -- never the secret key>"
   PROVIDER_NIF = "<Sydney's NIF>"
   ```
   (`PROVIDER_NIF` lives here, not in the code, because this repo is public.)
3. Once deployed, copy the app's URL into `SIGNING_APP_URL` in the main
   Studio OS app's `Client Database/esign_config.py`.
