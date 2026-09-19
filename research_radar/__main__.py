"""Command-line entry point shared by ``python -m`` and the installed script."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from . import __version__


def _parser():
    parser = argparse.ArgumentParser(
        prog="research-radar",
        description="Discover papers, prepare evidence reviews, and render a local research radar.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("sweep", help="Fetch and normalize paper candidates")
    commands.add_parser("scout", help="Prepare or render a human-reviewed scouting report")
    doctor = commands.add_parser("doctor", help="Check local dependencies and configuration; no network or secrets")
    doctor.add_argument("--config", type=Path, help="Override the bundled paper-cluster configuration")
    doctor.add_argument("--rubric", type=Path, help="Override the bundled scouting rubric")
    doctor.add_argument("--json", action="store_true", help="Emit machine-readable diagnostics")
    demo = commands.add_parser("demo", help="Run an explicitly synthetic, entirely offline workflow")
    demo.add_argument("--data-dir", type=Path, default=Path("demo-data"), help="Local output directory (default: demo-data)")
    return parser


def doctor(args):
    """Validate installation and local YAML, without inspecting credentials."""
    checks = []

    def record(name, ok, detail):
        checks.append({"name": name, "status": "ok" if ok else "error", "detail": detail})

    record("python", sys.version_info >= (3, 9), "Python " + sys.version.split()[0] + "; requires >=3.9")
    has_yaml = importlib.util.find_spec("yaml") is not None
    record("pyyaml", has_yaml, "Installed" if has_yaml else "Install the package dependencies with pip install .")
    resource_dir = Path(__file__).resolve().parent / "resources"
    for name, override, filename, required in (
        ("clusters", args.config, "clusters.yml", ("clusters", "sources")),
        ("rubric", args.rubric, "scout_rubric.yml", ()),
    ):
        config_path = override or resource_dir / filename
        if not config_path.is_file():
            record(name, False, "Missing configuration: " + str(config_path))
            continue
        if not has_yaml:
            record(name, False, "Cannot parse configuration until PyYAML is installed")
            continue
        try:
            import yaml

            data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not data:
                raise ValueError("Expected a nonempty YAML mapping")
            for key in required:
                if not isinstance(data.get(key), dict) or not data[key]:
                    raise ValueError("Missing or empty mapping: " + key)
            if name == "clusters":
                from .collection import load_config

                load_config(config_path)
                for cluster_name, cluster in data["clusters"].items():
                    if not isinstance(cluster, dict):
                        raise ValueError("Cluster must be a mapping: " + str(cluster_name))
                    for field in ("include", "exclude"):
                        values = cluster.get(field, [])
                        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                            raise ValueError(str(cluster_name) + "." + field + " must be a list of strings")
                arxiv = data["sources"].get("arxiv")
                if not isinstance(arxiv, dict):
                    raise ValueError("sources.arxiv must be a mapping")
                categories = arxiv.get("categories")
                if not isinstance(categories, list) or not categories or not all(isinstance(item, str) for item in categories):
                    raise ValueError("sources.arxiv.categories must be a nonempty list of strings")
            else:
                from .scout import load_profile

                load_profile(config_path)
            record(name, True, "Valid local YAML: " + str(config_path))
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            record(name, False, str(exc))
    for module in ("collection", "scout", "demo"):
        available = importlib.util.find_spec("research_radar." + module) is not None
        record(module, available, "Module available" if available else "Package module is missing")
    successful = all(item["status"] == "ok" for item in checks)
    if args.json:
        print(json.dumps({"status": "ok" if successful else "error", "checks": checks}, ensure_ascii=False, indent=2))
    else:
        print("Research Radar local checks (no network access; no credentials inspected)")
        for check in checks:
            print("[{status}] {name}: {detail}".format(**check))
    return 0 if successful else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "sweep":
        from . import collection

        return collection.main(argv[1:])
    if argv and argv[0] == "scout":
        from . import scout

        return scout.main(argv[1:])
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor(args)
    if args.command == "demo":
        from .demo import run_demo

        return run_demo(args.data_dir)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
