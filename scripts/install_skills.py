#!/usr/bin/env python3
"""Copy the two host-model skills into an explicitly selected skill directory."""
import argparse
from pathlib import Path
import shutil
import sys
import tempfile


NAMES = ("paper-sweep", "talent-scout")


def install(root, target, replace=False):
    """Stage both skills before replacement, and restore old copies on failure."""
    root, target = Path(root).resolve(), Path(target).expanduser().resolve()
    sources = [root / name for name in NAMES]
    destinations = [target / name for name in NAMES]
    for source in sources:
        if source.is_symlink() or not source.is_dir() or not (source / "SKILL.md").is_file():
            raise ValueError("Missing or invalid source skill: " + str(source))
    for dest in destinations:
        if dest.is_symlink():
            raise ValueError("Refusing to replace a symlink: " + str(dest))
        resolved = dest.resolve()
        for source in sources:
            source = source.resolve()
            if resolved == source or source in resolved.parents or resolved in source.parents:
                raise ValueError("Source and destination directories must not overlap: {} and {}".format(source, dest))
        if dest.exists() and not dest.is_dir():
            raise ValueError("Destination is not a directory: " + str(dest))
        if dest.exists() and not replace:
            raise ValueError("Already exists: {} (use --replace to update)".format(dest))

    target.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".research-radar-install-", dir=str(target)))
    incoming, backups = staging / "incoming", staging / "backups"
    incoming.mkdir()
    backups.mkdir()
    commit_started = False
    try:
        for name, source in zip(NAMES, sources):
            shutil.copytree(source, incoming / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        commit_started = True
        for name, dest in zip(NAMES, destinations):
            if dest.exists():
                dest.rename(backups / name)
            (incoming / name).rename(dest)
    except BaseException:
        rollback_errors = []
        if commit_started:
            for name, dest in reversed(list(zip(NAMES, destinations))):
                try:
                    # Inspect completed filesystem operations, even when a signal
                    # arrived after rename succeeded but before Python resumed.
                    if not (incoming / name).exists() and dest.exists():
                        shutil.rmtree(dest)
                    if (backups / name).exists():
                        (backups / name).rename(dest)
                except BaseException as exc:
                    rollback_errors.append(type(exc).__name__ + ": " + str(exc))
        if any(backups.iterdir()):
            rollback_errors.append("Original files remain in the backup directory")
        if rollback_errors:
            raise RuntimeError("Installation and rollback failed; recover the retained backups at {}: {}".format(
                backups, "; ".join(rollback_errors)))
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        shutil.rmtree(staging)
    except OSError:
        print("Skills installed; old-copy cleanup failed. Retained files: " + str(staging), file=sys.stderr)
    return destinations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True, help="Host's skills directory")
    parser.add_argument("--replace", action="store_true", help="Replace only these two existing skill folders")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    try:
        destinations = install(root, args.target, replace=args.replace)
    except ValueError as exc:
        parser.error(str(exc))
    except (OSError, RuntimeError) as exc:
        print("Skill installation failed: " + str(exc), file=sys.stderr)
        return 1
    for dest in destinations:
        print(dest)
    print("Skills copied. The host must use the Python environment where research-radar is installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
