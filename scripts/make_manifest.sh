#!/usr/bin/env bash
# make_manifest.sh — build a tamper-evident fingerprint of a release.
#
# Records a SHA-256 for every VERSIONED file (git ls-files) plus the HEAD
# commit hash and a UTC timestamp, then a single SHA-256 "root hash" over that
# whole list. The root hash is the thing you seal externally (eIDAS timestamp,
# registry, OpenTimestamps) — it proves "exactly this code existed on this date"
# without any file leaving the machine.
#
# By construction it only ever hashes files git already tracks, so clients.db,
# uploads, real invoices/proposals/contracts, secrets, backups and exports —
# all git-ignored — can never enter the manifest. The trade secret stays home;
# only the 64-char root hash travels.
#
# Usage:  scripts/make_manifest.sh <tag>
# Writes: manifests/MANIFEST-<tag>.txt   (the full list + root hash)
#         appends the root hash to manifests/HASHES.log
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TAG="${1:-untagged}"
OUT_DIR="$ROOT/manifests"
mkdir -p "$OUT_DIR"
MANIFEST="$OUT_DIR/MANIFEST-${TAG}.txt"
HASHLOG="$OUT_DIR/HASHES.log"

HEAD_COMMIT="$(git rev-parse HEAD)"
UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# Body: one "sha256  path" line per tracked file, sorted for a stable order.
BODY="$(git ls-files -z | sort -z | xargs -0 shasum -a 256)"

{
  echo "# Studio OS release manifest"
  echo "# tag:         ${TAG}"
  echo "# head_commit: ${HEAD_COMMIT}"
  echo "# built_utc:   ${UTC}"
  echo "# files:       $(printf '%s\n' "$BODY" | grep -c . || true)"
  echo "#"
  echo "# Each line below is: <sha256>  <versioned path>"
  echo "# The root hash at the bottom is what gets externally sealed."
  echo "#"
  printf '%s\n' "$BODY"
} > "$MANIFEST"

# Root hash = SHA-256 of everything above (list + header), so any change to any
# tracked file, or to the commit/date, changes this one number.
ROOT_HASH="$(shasum -a 256 "$MANIFEST" | awk '{print $1}')"
echo "#" >> "$MANIFEST"
echo "# root_sha256: ${ROOT_HASH}" >> "$MANIFEST"

printf '%s  %s  head=%s  %s\n' "$UTC" "$TAG" "$HEAD_COMMIT" "$ROOT_HASH" >> "$HASHLOG"

echo "Manifest written: manifests/MANIFEST-${TAG}.txt"
echo "Root hash (seal this): ${ROOT_HASH}"
