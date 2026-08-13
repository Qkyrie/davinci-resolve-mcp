#!/usr/bin/env bash
# Creates an isolated venv for person-matte generation (rembg + onnxruntime).
#
# Why a dedicated venv: installing rembg into a shared/system Python (e.g. an
# Anaconda base env) upgrades numpy/pillow/scipy, which can break unrelated
# packages already pinned to older versions there. This keeps the footprint
# self-contained and disposable.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet "rembg[cpu]"

echo "Ready: $VENV_DIR/bin/python $SCRIPT_DIR/generate_mattes.py --help"
