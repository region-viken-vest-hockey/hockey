#!/usr/bin/env sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
SOURCE="${1:-$ROOT/docs/kampveileder-for-3-mot-3-spill-revidert-august-25.pdf}"
OUTPUT="${2:-$ROOT/docs/kampveileder-for-3-mot-3-spill-revidert-august-25.md}"

if [ ! -f "$SOURCE" ]; then
  echo "ERROR: Kampveileder PDF not found: $SOURCE" >&2
  exit 2
fi

if ! command -v markitdown >/dev/null 2>&1; then
  echo "ERROR: markitdown is required. Install with: python3 -m pip install 'markitdown[pdf]==0.1.7'" >&2
  exit 2
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT HUP INT TERM

markitdown "$SOURCE" -o "$TMP"

SOURCE_SHA256="$(python3 - "$SOURCE" <<'PY'
import hashlib
import sys
from pathlib import Path

path = Path(sys.argv[1])
print(hashlib.sha256(path.read_bytes()).hexdigest())
PY
)"
MARKITDOWN_VERSION="$(python3 - <<'PY'
from importlib.metadata import version
print(version('markitdown'))
PY
)"

mkdir -p "$(dirname "$OUTPUT")"
{
  cat <<EOF
<!--
GENERATED FILE — DO NOT EDIT BY HAND.
Source: docs/kampveileder-for-3-mot-3-spill-revidert-august-25.pdf
Source SHA-256: $SOURCE_SHA256
Converter: Microsoft MarkItDown $MARKITDOWN_VERSION
Regenerate with: sh scripts/convert-kampveileder.sh
-->

EOF
  cat "$TMP"
} > "$OUTPUT"

echo "Generated ${OUTPUT#$ROOT/} from ${SOURCE#$ROOT/} with MarkItDown $MARKITDOWN_VERSION"
