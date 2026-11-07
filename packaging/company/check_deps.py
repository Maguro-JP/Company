"""同梱した site-packages の依存の抜けを、Windows の環境マーカーで見る。

pip は --platform を渡してもマーカーをこの機械（Linux）で評価する。だから Linux で組んだ中身は、
Windows で初めて import が落ちることがある。配る前に、各 wheel の METADATA の Requires-Dist を
Windows（win32、cp312）として評価し、無い物を並べて止める。

    python3 packaging/company/check_deps.py <site-packages>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    from packaging.markers import Marker
    from packaging.requirements import Requirement
except ImportError:  # pragma: no cover
    sys.exit("packaging が要る: pip install packaging")

WIN = {
    "sys_platform": "win32", "os_name": "nt", "platform_system": "Windows", "platform_machine": "AMD64",
    "python_version": "3.12", "python_full_version": "3.12.10", "implementation_name": "cpython",
    "platform_python_implementation": "CPython", "platform_release": "10", "platform_version": "10.0",
}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main(sp: Path) -> int:
    have: dict[str, Path] = {}
    for d in sp.glob("*.dist-info"):
        have[norm(d.name.split("-")[0])] = d
    missing: list[str] = []
    for name, d in sorted(have.items()):
        meta = (d / "METADATA").read_text(encoding="utf-8", errors="replace")
        for line in meta.splitlines():
            if not line.startswith("Requires-Dist:"):
                continue
            req = Requirement(line.split(":", 1)[1].strip())
            if req.marker is not None:
                env = dict(WIN, extra="")
                if not req.marker.evaluate(env):
                    continue
            if norm(req.name) not in have:
                missing.append(f"{name} -> {req.name}")
    if missing:
        print("依存が抜けている:\n  " + "\n  ".join(missing))
        return 1
    print(f"依存はそろっている（{len(have)} 個）")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
