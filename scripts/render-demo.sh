#!/usr/bin/env bash
# Renders docs/demo.gif from docs/demo.tape in the official vhs image. The image has
# python3 but no pip, so the wheel and its dependencies are installed from the host into
# .demo/ (same OS/arch and Python minor as the image) and exposed through a tiny wrapper.
set -euo pipefail
cd "$(dirname "$0")/.."
py=$(docker run --rm --entrypoint python3 ghcr.io/charmbracelet/vhs -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
rm -rf dist .demo && uv build -q
uv pip install -q --python "$py" --target .demo dist/*.whl
mkdir -p .demo/bin
printf '#!/bin/sh\nPYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/vhs/.demo exec python3 -m memory_boost.cli "$@"\n' > .demo/bin/memory-boost
chmod +x .demo/bin/memory-boost
docker run --rm -v "$PWD:/vhs" ghcr.io/charmbracelet/vhs docs/demo.tape   # needs root: headless Chromium
docker run --rm -v "$PWD:/vhs" --entrypoint chown ghcr.io/charmbracelet/vhs "$(id -u):$(id -g)" /vhs/docs/demo.gif
rm -rf .demo
ls -la docs/demo.gif
