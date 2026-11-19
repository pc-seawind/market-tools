"""Deployment-only Gateway edit, run on the Gateway with cutover JSON on stdin."""
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile


def atomic_write(path, raw, mode):
    fd, stage = tempfile.mkstemp(dir=path.parent, prefix=".investment-stage-")
    try:
        with os.fdopen(fd, "wb") as fp:
            os.fchmod(fp.fileno(), mode)
            fp.write(raw)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(stage, path)
    finally:
        Path(stage).unlink(missing_ok=True)


def apply(root, bundle):
    changes = bundle["changes"]
    for entry in changes:
        original, revised = entry["before"], entry["after"]
        name = entry["name"]
        if name != original["name"] or original["domain"] != "investment" or "/" in name or name.startswith("."):
            raise ValueError("not a domain job: " + name)
        if {k:v for k,v in original.items() if k not in ("prompt","enabled")} != {
                k:v for k,v in revised.items() if k not in ("prompt","enabled")}:
            raise ValueError("unexpected cron field change: " + name)
    with (root / ".cron.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sources = {}
        for entry in changes:
            name = entry["name"]
            path = root / (name + ".json")
            raw = path.read_bytes()
            if json.loads(raw) != entry["before"]:
                raise ValueError(f"concurrent edit to {name}; no changes applied")
            sources[name] = (path, raw, path.stat().st_mode & 0o777)
        backup = Path(tempfile.mkdtemp(prefix="investment-", dir=root))
        for name, (_, raw, mode) in sources.items():
            atomic_write(backup / (name + ".json"), raw, mode)
        written = []
        try:
            for entry in changes:
                name = entry["name"]
                path, _, mode = sources[name]
                raw = (json.dumps(entry["after"], ensure_ascii=False, indent=2) + "\n").encode()
                atomic_write(path, raw, mode)
                written.append(name)
            for entry in changes:
                path = sources[entry["name"]][0]
                if json.loads(path.read_text()) != entry["after"]:
                    raise OSError("live readback mismatch: " + entry["name"])
        except Exception:
            # Config transaction boundary: restore only entries this transaction wrote.
            for name in reversed(written):
                atomic_write(*sources[name])
            raise
        return {"backup": str(backup), "written": written, "readback": "matched"}


if __name__ == "__main__":
    root = Path(os.environ.get("HOMESPACE_CRON_DIR", Path.home() / ".homespace/cron"))
    print(json.dumps(apply(root, json.load(sys.stdin)), ensure_ascii=False))
