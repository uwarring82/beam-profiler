#!/usr/bin/env python3
"""Install the verified ARM64 macOS Aravis bottle into .vendor only.

Standard alternative: brew install aravis. This minimal route reuses existing
Homebrew GLib/libusb and skips installing the optional GTK/GStreamer viewer.
"""
import hashlib
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import urllib.request


VERSION = "0.8.36"
SHA256 = "e8d380d1205bfed309d38e061c3b16e3f37158a6b479655e7aa35554f65fb9da"
URL = "https://ghcr.io/v2/homebrew/core/aravis/blobs/sha256:" + SHA256
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / ".vendor"
LIB = DEST / "aravis" / VERSION / "lib/libaravis-0.8.0.dylib"


def main():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("This helper targets Apple Silicon macOS. Install Aravis 0.8 using your package manager.")
    if int(platform.mac_ver()[0].split('.')[0]) < 26:
        raise SystemExit("This bottle requires macOS Tahoe (26+). Use brew install aravis for your OS version.")
    required = ["glib/lib/libglib-2.0.0.dylib", "glib/lib/libgobject-2.0.0.dylib",
                "glib/lib/libgio-2.0.0.dylib", "libusb/lib/libusb-1.0.0.dylib"]
    if any(not (Path('/opt/homebrew/opt') / p).exists() for p in required):
        raise SystemExit("Install runtime dependencies first: brew install glib libusb")
    if LIB.exists():
        print(f"Already present: {LIB}")
        return
    DEST.mkdir(exist_ok=True)
    request = urllib.request.Request(URL, headers={"Authorization":"Bearer QQ=="})
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / "aravis.tar.gz"
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != SHA256:
            raise SystemExit("Package checksum mismatch; refusing to install.")
        archive.write_bytes(data)
        with tarfile.open(archive) as tar:
            tar.extractall(DEST, filter="data")
    output = subprocess.check_output(["otool", "-L", str(LIB)], text=True)
    for line in output.splitlines()[1:]:
        old = line.strip().split(" (")[0]
        if old.startswith("@@HOMEBREW_PREFIX@@"):
            new = old.replace("@@HOMEBREW_PREFIX@@", "/opt/homebrew")
            subprocess.run(["install_name_tool", "-change", old, new, str(LIB)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(LIB)], check=True)
    # Load in a subprocess so architecture/dependency errors cannot affect setup.
    subprocess.run([sys.executable, "-c", "import ctypes; ctypes.CDLL(" + repr(str(LIB)) + ")"], check=True)
    print(f"Installed Aravis {VERSION}: {LIB}")
    print("Source: https://github.com/AravisProject/aravis/tree/0.8.36 (LGPL-2.1-or-later)")


if __name__ == "__main__":
    main()
