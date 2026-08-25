#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  source <(sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' .env)
  set +a
fi

export PYTHONPATH="$PWD"
python -m uvicorn app.standard_main:app --host 0.0.0.0 --port 8091
