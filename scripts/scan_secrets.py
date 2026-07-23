#!/usr/bin/env python3
"""Self-contained secret scanner (no third-party dependency, by design — we do
not add a package right after an OSS-license audit).

Two detection layers:
  1. High-confidence patterns for the exact credential shapes this project can
     leak: Anthropic keys, JWTs (Supabase anon/service keys), GitHub tokens,
     AWS keys, Slack tokens, PEM private keys, and `secret=...`-style literals.
  2. Shannon-entropy on quoted string literals, to catch unknown-format
     secrets a pattern would miss (skips pure-hex, which is usually a hash).

Usage:
  scan_secrets.py <file> [<file> ...]   # scan given files
  scan_secrets.py --staged              # scan git-staged blob content (hook)

Exit 0 = clean; 1 = something looks like a secret (printed, value redacted).
Findings print file:line:kind with the match masked, never the raw secret.
"""
import math
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = {
    "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    "openai_key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "aws_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "slack_token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    # Google Calendar secret iCal address — the token is hex, which the
    # entropy layer deliberately skips, so it needs its own pattern (this
    # project's calendar widget uses exactly this URL type).
    "gcal_private_ical": re.compile(
        r"calendar\.google\.com/calendar/ical/[^\s\"']+/private-[0-9a-f]{8,}"),
    "generic_secret": re.compile(
        r"""(?ix)(password|passwd|secret|token|api[_-]?key|access[_-]?key|
            auth[_-]?token|client[_-]?secret)
            \s*[:=]\s*["']([^"'\s]{8,})["']"""),
}

# substrings that mark a match as a documented placeholder / format mention,
# not a real secret (keeps the checker from crying wolf on our own docs).
ALLOW = re.compile(
    r"(?i)your[-_ ]|placeholder|example|dummy|<[a-z_]+>|xxx|\.\.\.|"
    r"here|_env\b|getenv|environ|st\.secrets|redacted|format|prefix|"
    r"changeme|fake|sample|test[-_]?key|not[-_]?a[-_]?real")

_QUOTED = re.compile(r"""["']([A-Za-z0-9+/=_-]{25,})["']""")
_HEXONLY = re.compile(r"^[0-9a-fA-F]+$")


def entropy(s):
    if not s:
        return 0.0
    freq = {c: s.count(c) for c in set(s)}
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def scan_text(text, label):
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        for kind, rx in PATTERNS.items():
            for m in rx.finditer(line):
                hit = m.group(0)
                if ALLOW.search(line):
                    continue
                findings.append((label, i, kind, mask(hit)))
        for m in _QUOTED.finditer(line):
            val = m.group(1)
            if _HEXONLY.match(val) or ALLOW.search(line):
                continue
            if entropy(val) >= 4.5 and len(val) >= 25:
                findings.append((label, i, "high_entropy_string", mask(val)))
    return findings


def mask(s):
    if len(s) <= 8:
        return s[0] + "***"
    return f"{s[:4]}…{s[-2:]} (len {len(s)})"


def staged_files():
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True).stdout.split()
    for f in out:
        blob = subprocess.run(["git", "show", f":{f}"],
                              capture_output=True, text=True)
        if blob.returncode == 0:
            yield f, blob.stdout


# --selftest cases: (should_be_caught, text). Assembled from fragments so this
# file never contains anything credential-shaped itself. Keeps the acceptance
# test permanent instead of a one-off at install time.
def _selftest_cases():
    # every fixture line below carries a 'fake' marker so scanning THIS file
    # stays clean; the assembled runtime strings don't include the comments,
    # so the cases still exercise real detection.
    k = "sk-ant-" + "api03-" + "abc123def456ghi789jkl012mno345pqr"  # fake
    jwt = "eyJ" + "hbGciOiJIUzI1NiJ9" + ".eyJ" + "yb2xlIjoiYW5vbiJ9" + "." + "sig0123456789ab"  # fake
    ical = ("https://calendar.google.com/calendar/ical/"
            "someone%40gmail.com/private-" + "0123456789abcdef" + "/basic.ics")  # fake
    pw = "hunter2" + "hunter2"  # fake
    return [
        (True, f'API_KEY = "{k}"'),
        (True, f'supabase = "{jwt}"'),
        (True, f'CAL_URL = "{ical}"'),
        (True, f'password = "{pw}"'),
        (False, 'API_KEY = "your-api-key-here"  # example placeholder'),
        (False, 'digest = "d41d8cd98f00b204e9800998ecf8427e"'),  # pure hex hash
        (False, 'label = "Awaiting payment"'),
    ]


def selftest():
    failures = []
    for should_catch, text in _selftest_cases():
        caught = bool(scan_text(text, "case"))
        if caught != should_catch:
            failures.append(f"  {'MISSED' if should_catch else 'FALSE POSITIVE'}: {text[:60]}")
    if failures:
        print("SELFTEST FAILED:")
        print("\n".join(failures))
        return 1
    print(f"scan_secrets selftest: OK ({len(_selftest_cases())} cases)")
    return 0


def main(argv):
    findings = []
    if argv == ["--selftest"]:
        return selftest()
    if argv == ["--staged"]:
        for label, text in staged_files():
            findings += scan_text(text, label)
    elif argv:
        for a in argv:
            p = Path(a)
            if p.is_file():
                findings += scan_text(p.read_text(errors="ignore"), a)
    else:
        print(__doc__)
        return 2
    if findings:
        print("SECRET SCAN: possible credential(s) found — commit blocked:")
        for label, line, kind, masked in findings:
            print(f"  {label}:{line}: {kind}: {masked}")
        print("If this is a false positive, add a clear placeholder marker "
              "(e.g. 'example', 'your-…') or move the value out of the repo.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
