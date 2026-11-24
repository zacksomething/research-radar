"""Installation replacement must preserve existing skills on every failure."""

import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "install_skills", Path(__file__).resolve().parents[1] / "scripts" / "install_skills.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallSkillsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "checkout"
        self.target = self.base / "installed"
        self._sources(self.root)

    def _sources(self, root):
        for name in installer.NAMES:
            (root / name).mkdir(parents=True)
            (root / name / "SKILL.md").write_text("new " + name, encoding="utf-8")

    def _previous(self):
        for name in installer.NAMES:
            (self.target / name).mkdir(parents=True)
            (self.target / name / "SKILL.md").write_text("old " + name, encoding="utf-8")
            (self.target / name / "personal.txt").write_text("keep " + name, encoding="utf-8")

    def _previous_legacy(self):
        for name in installer.LEGACY_NAMES:
            (self.target / name).mkdir(parents=True)
            (self.target / name / "SKILL.md").write_text("legacy " + name, encoding="utf-8")

    def _snapshot(self, directory):
        return {str(path.relative_to(directory)): path.read_bytes()
                for path in directory.rglob("*") if path.is_file()}

    def test_fresh_install_and_explicit_replacement(self):
        installer.install(self.root, self.target)
        (self.target / "research-radar" / "old.txt").write_text("old")
        with self.assertRaises(ValueError):
            installer.install(self.root, self.target)
        self.assertTrue((self.target / "research-radar" / "old.txt").exists())
        installer.install(self.root, self.target, replace=True)
        self.assertFalse((self.target / "research-radar" / "old.txt").exists())
        self.assertEqual(self._snapshot(self.target), self._snapshot(self.root))
        self.assertFalse(list(self.target.glob(".research-radar-install-*")))

    def test_replace_retires_legacy_split_skills_and_refuses_without_replace(self):
        self._previous_legacy()
        before = self._snapshot(self.target)
        with self.assertRaises(ValueError):
            installer.install(self.root, self.target, replace=False)
        self.assertEqual(self._snapshot(self.target), before)
        installer.install(self.root, self.target, replace=True)
        self.assertFalse(any((self.target / name).exists() for name in installer.LEGACY_NAMES))
        self.assertEqual(self._snapshot(self.target), self._snapshot(self.root))

    def test_missing_source_preserves_old_and_legacy_skills(self):
        self._previous()
        self._previous_legacy()
        before = self._snapshot(self.target)
        shutil.rmtree(self.root / "research-radar")
        with self.assertRaises(ValueError):
            installer.install(self.root, self.target, replace=True)
        self.assertEqual(self._snapshot(self.target), before)

    def test_copy_failure_preserves_old_and_legacy_skills(self):
        self._previous()
        self._previous_legacy()
        before = self._snapshot(self.target)

        def fail_copy(source, destination, *args, **kwargs):
            raise OSError("simulated unreadable source")

        with mock.patch.object(installer.shutil, "copytree", side_effect=fail_copy):
            with self.assertRaises(OSError):
                installer.install(self.root, self.target, replace=True)
        self.assertEqual(self._snapshot(self.target), before)
        self.assertFalse(list(self.target.glob(".research-radar-install-*")))

    def test_commit_failure_rolls_back_old_and_legacy_skills(self):
        self._previous()
        self._previous_legacy()
        before = self._snapshot(self.target)
        original_rename = Path.rename

        def fail_incoming(path, destination):
            if path.parent.name == "incoming" and path.name == "research-radar":
                raise OSError("simulated rename failure")
            return original_rename(path, destination)

        with mock.patch.object(Path, "rename", fail_incoming):
            with self.assertRaises(OSError):
                installer.install(self.root, self.target, replace=True)
        self.assertEqual(self._snapshot(self.target), before)
        self.assertFalse(list(self.target.glob(".research-radar-install-*")))

    def test_fresh_install_commit_failure_removes_partial_installation(self):
        original_rename = Path.rename

        def fail_incoming(path, destination):
            if path.parent.name == "incoming" and path.name == "research-radar":
                raise OSError("simulated rename failure")
            return original_rename(path, destination)

        with mock.patch.object(Path, "rename", fail_incoming):
            with self.assertRaises(OSError):
                installer.install(self.root, self.target)
        self.assertFalse(any((self.target / name).exists() for name in installer.NAMES))

    def test_interrupt_after_backup_rename_restores_original_files(self):
        self._previous()
        self._previous_legacy()
        before = self._snapshot(self.target)
        original_rename = Path.rename
        interrupted = False

        def interrupt_after_success(path, destination):
            nonlocal interrupted
            result = original_rename(path, destination)
            if Path(destination).parent.name == "backups" and path.name == "research-radar" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt("after successful backup rename")
            return result

        with mock.patch.object(Path, "rename", interrupt_after_success):
            with self.assertRaises(KeyboardInterrupt):
                installer.install(self.root, self.target, replace=True)
        self.assertEqual(self._snapshot(self.target), before)
        self.assertFalse(list(self.target.glob(".research-radar-install-*")))

    def test_interrupt_after_install_rename_restores_original_files(self):
        self._previous()
        self._previous_legacy()
        before = self._snapshot(self.target)
        original_rename = Path.rename
        interrupted = False

        def interrupt_after_success(path, destination):
            nonlocal interrupted
            result = original_rename(path, destination)
            if path.parent.name == "incoming" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt("after successful install rename")
            return result

        with mock.patch.object(Path, "rename", interrupt_after_success):
            with self.assertRaises(KeyboardInterrupt):
                installer.install(self.root, self.target, replace=True)
        self.assertEqual(self._snapshot(self.target), before)
        self.assertFalse(list(self.target.glob(".research-radar-install-*")))

    def test_destination_cannot_contain_entire_checkout(self):
        nested_root = self.base / "danger" / "research-radar"
        self._sources(nested_root)
        before = self._snapshot(nested_root)
        with self.assertRaises(ValueError):
            installer.install(nested_root, nested_root.parent, replace=True)
        self.assertEqual(self._snapshot(nested_root), before)

    def test_cross_skill_and_descendant_overlaps_are_rejected(self):
        before = self._snapshot(self.root)
        for target in (self.root, self.root / "research-radar", self.root / "research-radar" / "nested"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    installer.install(self.root, target, replace=True)
        self.assertEqual(self._snapshot(self.root), before)

    def test_destination_symlink_is_not_followed_or_removed(self):
        self.target.mkdir()
        unrelated = self.base / "unrelated"
        unrelated.mkdir()
        (unrelated / "important.txt").write_text("keep")
        (self.target / "research-radar").symlink_to(unrelated, target_is_directory=True)
        with self.assertRaises(ValueError):
            installer.install(self.root, self.target, replace=True)
        self.assertEqual((unrelated / "important.txt").read_text(), "keep")
        self.assertTrue((self.target / "research-radar").is_symlink())

    def test_non_directory_legacy_destination_does_not_modify_target(self):
        self._previous()
        (self.target / "talent-scout").write_text("keep file")
        before = self._snapshot(self.target)
        with self.assertRaises(ValueError):
            installer.install(self.root, self.target, replace=True)
        self.assertEqual(self._snapshot(self.target), before)


if __name__ == "__main__":
    unittest.main()
