"""Add the locked certifi roots that Debian's ca-certificates bundle lacks.

Debian stable's bundle lags Mozilla's root program, so code that relies on
OpenSSL defaults rejects certificates chaining to newer public roots. Roots
are only added, never removed, so chains to roots Debian still ships keep
working.
"""

import base64
import hashlib
import io
import re
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from pathlib import Path

SYSTEM_BUNDLE = Path("/etc/ssl/certs/ca-certificates.crt")
TARGET_DIR = Path("/usr/local/share/ca-certificates/certifi")
PEM_BLOCK = re.compile(
    r"-----BEGIN CERTIFICATE-----\s+(.+?)\s+-----END CERTIFICATE-----", re.DOTALL
)


def pem_blocks(text: str) -> list[tuple[bytes, str]]:
    return [
        (base64.b64decode("".join(m.group(1).split())), m.group(0))
        for m in PEM_BLOCK.finditer(text)
    ]


def locked_certifi_bundle(lock_path: Path) -> str:
    lock = tomllib.loads(lock_path.read_text())
    package = next(p for p in lock["package"] if p["name"] == "certifi")
    wheel = next(w for w in package["wheels"] if w["url"].endswith("-none-any.whl"))
    algorithm, expected = wheel["hash"].split(":", 1)
    with urllib.request.urlopen(wheel["url"], timeout=60) as response:
        data = response.read()
    if hashlib.new(algorithm, data).hexdigest() != expected:
        raise SystemExit(f"certifi wheel hash mismatch: {wheel['url']}")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read("certifi/cacert.pem").decode()


def main(lock_path: Path) -> None:
    system = {der for der, _ in pem_blocks(SYSTEM_BUNDLE.read_text())}
    missing = [
        pem
        for der, pem in pem_blocks(locked_certifi_bundle(lock_path))
        if der not in system
    ]
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    for pem in missing:
        digest = hashlib.sha256(pem.encode()).hexdigest()[:16]
        (TARGET_DIR / f"certifi-{digest}.crt").write_text(pem + "\n")
    subprocess.run(["update-ca-certificates"], check=True)
    print(f"Added {len(missing)} certifi roots to {SYSTEM_BUNDLE}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
