"""Compile the package's Swift helpers (dvdtoplex/swift/NAME.swift) with the system swiftc."""
from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
from importlib import resources


class SwiftError(RuntimeError):
    """swiftc is missing or failed."""


def build(name: str, dest: pathlib.Path) -> pathlib.Path:
    """The compiled helper in DEST, named after its source's hash so a changed source is rebuilt."""
    source = resources.files("dvdtoplex") / "swift" / f"{name}.swift"
    binary = dest / f"{name}-{hashlib.sha256(source.read_bytes()).hexdigest()[:12]}"
    if binary.is_file():
        return binary
    if shutil.which("swiftc") is None:
        raise SwiftError("swiftc is not on PATH; install the Xcode Command Line Tools")
    dest.mkdir(parents=True, exist_ok=True)
    with resources.as_file(source) as path:
        p = subprocess.run(["swiftc", "-O", str(path), "-o", str(binary)], capture_output=True,
                           text=True, timeout=600)
    if p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-8:])
        raise SwiftError(f"swiftc failed on {name}.swift ({p.returncode}):\n{tail}")
    return binary
