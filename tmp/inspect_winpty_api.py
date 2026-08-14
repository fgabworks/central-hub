"""Inspect pywinpty spawn/setwinsize signatures and ConPTY echo bytes."""
from __future__ import annotations

import inspect
import os
import time

from winpty import PtyProcess


def main() -> None:
    print("PtyProcess.spawn", inspect.signature(PtyProcess.spawn))
    proc = PtyProcess.spawn(
        "powershell.exe -NoLogo",
        cwd=r"C:\PMNP\pmnp-live-processing",
        dimensions=(24, 80),
    )
    print("setwinsize", inspect.signature(proc.setwinsize))
    print("getwinsize", getattr(proc, "getwinsize", None), inspect.signature(proc.getwinsize) if hasattr(proc, "getwinsize") else "n/a")
    time.sleep(0.8)
    try:
        chunk = proc.read(4096)
        print("initial repr", repr(chunk[:500]))
    except Exception as exc:
        print("read err", exc)
    proc.write("echo one\r")
    time.sleep(0.6)
    try:
        chunk = proc.read(4096)
        print("after echo repr", repr(chunk[:800]))
    except Exception as exc:
        print("read2 err", exc)
    try:
        proc.setwinsize(8, 120)
        time.sleep(0.2)
        proc.write("echo wraptest\r")
        time.sleep(0.6)
        chunk = proc.read(4096)
        print("after 8-col resize repr", repr(chunk[:800]))
    except Exception as exc:
        print("resize experiment", exc)
    try:
        proc.terminate()
    except Exception:
        pass


if __name__ == "__main__":
    main()
