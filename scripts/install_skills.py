#!/usr/bin/env python3
"""Copy the two host-model skills into an explicitly selected skill directory."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True, help="Host's skills directory")
    parser.add_argument("--replace", action="store_true", help="Replace only these two existing skill folders")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    target = args.target.expanduser().resolve()
    names = ("paper-sweep", "talent-scout")
    destinations = [target / name for name in names]
    for name, dest in zip(names, destinations):
        if dest.resolve() == (root / name).resolve():
            parser.error("Target must not be the source project directory")
        if dest.exists() and not args.replace:
            parser.error("Already exists: {} (use --replace to update)".format(dest))
        if dest.is_symlink():
            parser.error("Refusing to replace a symlink: {}".format(dest))
    target.mkdir(parents=True, exist_ok=True)
    for name, dest in zip(names, destinations):
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(root / name, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        print(dest)
    print("Skills copied. The host must use the Python environment where research-radar is installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
