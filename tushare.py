#!/usr/bin/env python3
"""tushare.py — thin REST wrapper for tushare Pro (https://tushare.pro).

This is the generic escape hatch — any tushare API endpoint can be called
by name. For common OHLCV use cases, prefer the higher-level `history.sh`
wrapper which handles ticker-shape routing and date defaults.

Usage:
    tushare.py <api_name> [key=value ...] [--fields=a,b,c] [--csv]

Examples:
    # A-share daily OHLCV
    tushare.py daily ts_code=600519.SH start_date=20250101 end_date=20250501 \\
               --fields=trade_date,open,high,low,close,vol --csv

    # Index daily (e.g. CSI 300)
    tushare.py index_daily ts_code=000300.SH start_date=20250101 --csv

    # Stock basic info
    tushare.py stock_basic exchange=SSE list_status=L --fields=ts_code,name

Environment:
    TUSHARE_TOKEN — required. Register at https://tushare.pro to get one.

Output:
    Default: pretty-printed JSON of the full response body.
    With --csv: only the data rows, with a header line.

Exit codes:
    0  ok
    2  bad usage
    3  missing TUSHARE_TOKEN
    4  network / HTTP error
    5  tushare-reported error (non-zero code in response)

Dependencies: python3 stdlib only (urllib, json). No pip install needed.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.tushare.pro"
TIMEOUT = 15

# Some shells have a stale SOCKS/HTTP proxy in HTTPS_PROXY/ALL_PROXY pointing at
# a dead localhost port — this would trip urllib.urlopen before it ever reaches
# tushare. tushare's endpoint is a public Aliyun SLB and always directly
# reachable, so we install an opener that ignores env proxies. Set
# TUSHARE_USE_ENV_PROXY=1 to override (e.g. if you're actually behind a proxy
# that's the only path to the public internet).
if os.environ.get("TUSHARE_USE_ENV_PROXY") != "1":
    _no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(_no_proxy)


def usage():
    sys.stderr.write(__doc__)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        usage()
        return 2

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        sys.stderr.write("ERROR: TUSHARE_TOKEN env var not set\n")
        return 3

    api_name = argv[0]
    params = {}
    fields = ""
    out_csv = False

    for arg in argv[1:]:
        if arg == "--csv":
            out_csv = True
        elif arg.startswith("--fields="):
            fields = arg[len("--fields="):]
        elif "=" in arg:
            k, v = arg.split("=", 1)
            params[k] = v
        else:
            sys.stderr.write(f"bad arg: {arg!r} (expected key=value or --flag)\n")
            return 2

    payload = {
        "api_name": api_name,
        "token": token,
        "params": params,
        "fields": fields,
    }

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    # Tushare rate-limits per-API (e.g. sw_daily: 10/min). On 40203 we
    # transparently sleep + retry rather than bubbling the error, so batch
    # scripts don't have to manage windows. Up to 3 retries (~2 min max wait).
    # Set TUSHARE_NO_RETRY=1 to opt out.
    no_retry = os.environ.get("TUSHARE_NO_RETRY") == "1"
    max_retries = 0 if no_retry else 3

    body = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            snippet = e.read().decode("utf-8", errors="replace")[:300]
            sys.stderr.write(f"HTTP {e.code} from tushare: {snippet}\n")
            return 4
        except Exception as e:
            sys.stderr.write(f"request failed: {e}\n")
            return 4

        code = body.get("code", 0)
        if code != 40203 or attempt == max_retries:
            break

        # 40203 = frequency limit. tushare's msg usually embeds "N次/分钟".
        msg = body.get("msg") or ""
        # Default backoff: 35s (covers 1-min window with slack). If msg parses,
        # we still use the fixed backoff since tushare doesn't report window start.
        wait = 35 + attempt * 10
        sys.stderr.write(
            f"tushare rate-limited ({api_name}): {msg.strip()}  "
            f"retry {attempt + 1}/{max_retries} after {wait}s...\n"
        )
        time.sleep(wait)

    code = body.get("code", 0) if body else -1
    if code != 0:
        msg = body.get("msg") if body else "empty body"
        sys.stderr.write(f"tushare error (code={code}): {msg}\n")
        return 5

    data = body.get("data") or {}

    if out_csv:
        fields_list = data.get("fields") or []
        items = data.get("items") or []
        print(",".join(fields_list))
        for row in items:
            print(",".join(
                "" if x is None else str(x)
                for x in row
            ))
    else:
        print(json.dumps(body, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
