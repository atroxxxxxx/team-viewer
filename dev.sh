#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir"

if [ "$#" -eq 0 ]; then
    set -- --help
fi

if [ "$1" = "setup" ]; then
    exec "${SUPPORT_PYTHON:-python3}" "$project_dir/scripts/dev.py" "$@"
fi

if [ ! -x "$project_dir/.venv/bin/python" ]; then
    echo "Run: sh dev.sh setup --postgres" >&2
    exit 1
fi

exec "$project_dir/.venv/bin/python" "$project_dir/scripts/dev.py" "$@"
