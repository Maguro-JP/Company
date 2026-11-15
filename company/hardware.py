"""機械の力（メモリ、GPU、CPU）を見て、どの模型が入るかを決める。

大きい模型が動かない理由の大半は「メモリに入らない」か「GPU に載せられない」で、そのどちらかを画面で言わないと
ユーザーには分からない。Strata のように、先に機械を見て、入る物だけを勧め、入らない物にはその理由を書く。
外の部品は使わない（Windows は GlobalMemoryStatusEx、Linux は /proc/meminfo、GPU は nvidia-smi があれば読む）。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from typing import Any

GB = 1024 ** 3


def memory() -> dict[str, int]:
    """物理メモリの合計と空き（バイト）。取れなければ 0。"""
    if sys.platform == "win32":
        class _Stat(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        st = _Stat()
        st.dwLength = ctypes.sizeof(_Stat)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return {"total": int(st.ullTotalPhys), "free": int(st.ullAvailPhys)}
        except Exception:  # noqa: BLE001
            pass
        return {"total": 0, "free": 0}
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                k, _, v = line.partition(":")
                info[k.strip()] = int(v.split()[0]) * 1024
        return {"total": info.get("MemTotal", 0), "free": info.get("MemAvailable", info.get("MemFree", 0))}
    except (OSError, ValueError):
        return {"total": 0, "free": 0}


def gpus() -> list[dict[str, Any]]:
    """NVIDIA の GPU（名前、VRAM の合計と空き）。nvidia-smi が無ければ空。"""
    exe = shutil.which("nvidia-smi") or (r"C:\Windows\System32\nvidia-smi.exe" if sys.platform == "win32" else "")
    if not exe or not os.path.exists(exe):
        return []
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3 and parts[1].isdigit():
            found.append({"name": parts[0], "vram_total": int(parts[1]) * 1024 * 1024, "vram_free": int(parts[2]) * 1024 * 1024})
    return found


def engine_backend() -> str:
    """同梱の llama.cpp が GPU を使えるか（cuda | cpu | none）。"""
    try:
        import llama_cpp  # noqa: PLC0415
    except ImportError:
        return "none"
    try:
        from llama_cpp import llama_cpp as lib  # noqa: PLC0415
        fn = getattr(lib, "llama_supports_gpu_offload", None)
        if fn and fn():
            return "cuda"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def need_bytes(model_bytes: int, n_ctx: int = 8192) -> int:
    """模型を動かすのに要るメモリの目安。重み + 文脈の KV（4096 で約 0.5GB）+ 余裕 1GB。"""
    return int(model_bytes * 1.1) + int(n_ctx / 4096 * 0.5 * GB) + GB


def status(n_ctx: int = 8192) -> dict[str, Any]:
    mem = memory()
    g = gpus()
    backend = engine_backend()
    return {
        "memory": mem, "gpus": g, "backend": backend,
        "cpu_count": os.cpu_count() or 0,
        "summary": summarize(mem, g, backend),
    }


def summarize(mem: dict[str, int], g: list[dict[str, Any]], backend: str) -> str:
    parts = []
    if mem.get("total"):
        parts.append(f"メモリ {mem['total'] / GB:.0f} GB（空き {mem['free'] / GB:.1f} GB）")
    if g:
        parts.append("、".join(f"GPU {x['name']} {x['vram_total'] / GB:.0f} GB" for x in g))
    else:
        parts.append("GPU なし（または NVIDIA 以外）")
    parts.append({"cuda": "内蔵エンジンは GPU 版", "cpu": "内蔵エンジンは CPU 版", "none": "内蔵エンジン無し"}[backend])
    return "。".join(parts)


def fit(model_bytes: int, n_ctx: int = 8192, mem: dict[str, int] | None = None, g: list[dict[str, Any]] | None = None,
        backend: str | None = None) -> dict[str, Any]:
    """この模型が今の機械に入るか。入らないときは理由と代わりの手を書く。"""
    mem = memory() if mem is None else mem
    g = gpus() if g is None else g
    backend = engine_backend() if backend is None else backend
    need = need_bytes(model_bytes, n_ctx)
    out: dict[str, Any] = {"need": need, "fits": True, "where": "cpu", "note": ""}
    vram = max((x["vram_total"] for x in g), default=0)
    if backend == "cuda" and vram and need <= vram:
        out["where"] = "gpu"
        out["note"] = f"GPU に全部載ります（要 {need / GB:.1f} GB）"
        return out
    if not mem.get("total"):
        out["note"] = f"要 {need / GB:.1f} GB（メモリの量が取れないので入るかは未確認）"
        return out
    if need <= mem["total"]:
        if need > mem["free"]:
            out["note"] = f"要 {need / GB:.1f} GB。今の空き {mem['free'] / GB:.1f} GB では足りません。他のアプリを閉じるか、小さい模型を選んでください"
            out["fits"] = False
        else:
            out["note"] = (f"CPU で動きます（要 {need / GB:.1f} GB）。GPU 版の内蔵エンジンなら速くなります" if g and backend != "cuda"
                           else f"CPU で動きます（要 {need / GB:.1f} GB）")
        return out
    out["fits"] = False
    out["note"] = (f"要 {need / GB:.1f} GB で、この機械のメモリ {mem['total'] / GB:.0f} GB に入りません。"
                   "小さい模型を選ぶか、Strata や Ollama のように GPU と CPU に分けて動かすエンジンを手元で起動して、設定で選んでください")
    return out
