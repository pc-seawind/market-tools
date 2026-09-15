#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Ubuntu dist-packages supplies requests/PyYAML; pytest comes from the existing
# homespace venv. No installation or mutation of either environment.
PYTHON="${TA10_TEST_PYTHON:-/home/emox/work/homespace/.venv/bin/python}"
USER_SITE="$(/usr/bin/python3 -m site --user-site)"
export PYTHONPATH="$USER_SITE:/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"
"$PYTHON" -c 'import sys,pytest,requests,yaml,bs4; print("python="+sys.executable); print("pytest="+pytest.__version__+" requests="+requests.__version__+" yaml="+yaml.__version__+" bs4="+bs4.__version__)'
if [ "$#" -eq 0 ]; then set -- tests/test_ta_research.py tests/test_ta_r2.py tests/test_ta_r3.py tests/test_mt13_action_loop.py; fi
exec "$PYTHON" -m pytest "$@"
