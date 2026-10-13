"""Verify a built wheel without relying on repo cwd, editable paths, or inference."""

import argparse
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    expected = {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((root / "backend" / "migrations").glob("*.sql"))
    }
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        package = directory / "installed"
        with zipfile.ZipFile(args.wheel.resolve()) as wheel:
            wheel.extractall(package)
        (directory / "expected.json").write_text(json.dumps(expected), encoding="utf-8")
        code = """
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import legal_rag
assert Path(legal_rag.__file__).is_relative_to(Path(sys.argv[1]))
from legal_rag.infrastructure.migrations import migration_files
assert {p.name: p.read_text(encoding='utf-8') for p in migration_files()} == json.loads(Path('expected.json').read_text(encoding='utf-8'))
from legal_rag.cli import main
sys.argv = ['legal-rag', '--help']
main()
"""
        subprocess.run(
            [sys.executable, "-I", "-c", code, str(package)],
            cwd=directory,
            check=True,
            timeout=20,
        )
    print(f"Wheel verified outside repository: {len(expected)} unchanged SQL migrations; CLI importable.")


if __name__ == "__main__":
    main()
