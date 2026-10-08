#!/usr/bin/env bash
# Rebuilds lecture_cli/gui/static from frontend/. The build is committed so installs need no Node.
set -euo pipefail
cd "$(dirname "$0")/.."
root="$PWD"
cd frontend
npm ci
npm run check
npm test
npm run build
python3 "$root/scripts/frontend_hash.py" > "$root/lecture_cli/gui/static/build-hash.txt"
echo "已构建 lecture_cli/gui/static；请把它与前端源码一起提交。"
