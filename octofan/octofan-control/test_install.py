"""Exercise the real installer only inside disposable destination trees."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = Path(__file__).resolve().parent
REAL_INSTALL = shutil.which("install") or ""


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="octofan-installer-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        shutil.copytree(HERE, self.source,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.dest = self.root / "image"
        self.dest.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "install-log.jsonl"
        self.systemctl_log = self.root / "systemctl-called"
        self.executable("id", "#!/bin/sh\nprintf '1000\\n'\n")
        self.executable("systemctl", "#!/bin/sh\nprintf called > \"$SYSTEMCTL_LOG\"\nexit 97\n")
        wrapper = "#!" + sys.executable + "\n" + textwrap.dedent('''\
            import json, os, pathlib, subprocess, sys
            allowed = pathlib.Path(os.environ['SAFE_IMAGE_ROOT']).resolve()
            with open(os.environ['INSTALL_LOG'], 'a') as f:
                f.write(json.dumps(sys.argv[1:]) + '\\n')
            for arg in sys.argv[1:]:
                if pathlib.Path(arg).is_absolute():
                    path = pathlib.Path(arg).resolve()
                    if not path.is_relative_to(allowed):
                        raise SystemExit('installer attempted a host write: ' + arg)
            raise SystemExit(subprocess.call([os.environ['REAL_INSTALL'], *sys.argv[1:]]))
            ''')
        self.executable("install", wrapper)
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        SAFE_IMAGE_ROOT=str(self.dest), INSTALL_LOG=str(self.log),
                        SYSTEMCTL_LOG=str(self.systemctl_log), REAL_INSTALL=REAL_INSTALL,
                        DESTDIR=str(self.dest), PYTHONDONTWRITEBYTECODE="1")

    def executable(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_install(self, dest=None):
        env = dict(self.env)
        if dest is not None:
            env["DESTDIR"] = dest
        return subprocess.run(["bash", str(self.source / "install.sh")],
                              cwd=self.source, env=env, text=True,
                              capture_output=True, timeout=30)

    def assert_no_external_effects(self):
        self.assertFalse(self.systemctl_log.exists(), "staging called systemctl")

    def test_unprivileged_staging_copies_payload_without_live_dependencies(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        expected = {
            "usr/local/sbin/octofan-control": 0o755,
            "usr/local/sbin/octofan_topology.py": 0o644,
            "usr/local/share/octofan-control/profiles/minerdude-12xtreme.json": 0o644,
            "etc/octofan-control.conf": 0o644,
            "etc/systemd/system/octofan-control.service": 0o644,
        }
        for rel, mode in expected.items():
            with self.subTest(path=rel):
                path = self.dest / rel
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_mode & 0o777, mode)
        self.assertEqual((self.dest / "usr/local/sbin/octofan-control").read_bytes(),
                         (self.source / "octofan-control").read_bytes())
        self.assert_no_external_effects()
        self.assertFalse((self.source / "__pycache__").exists())

    def test_existing_host_configuration_is_preserved_byte_exactly(self):
        config = self.dest / "etc/octofan-control.conf"
        config.parent.mkdir()
        before = b"# owned host configuration\nmode = unchanged\n"
        config.write_bytes(before)
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(config.read_bytes(), before)
        self.assert_no_external_effects()

    def test_invalid_profile_refuses_before_any_install_command(self):
        (self.source / "profiles/minerdude-12xtreme.json").write_text("not JSON")
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assert_no_external_effects()

    def test_invalid_helper_refuses_before_any_install_command(self):
        (self.source / "octofan_topology.py").write_text("def incomplete(\n")
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assert_no_external_effects()

    def test_invalid_shipped_config_refuses_before_any_install_command(self):
        (self.source / "octofan-control.conf").write_text("not a configuration\n")
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())
        self.assert_no_external_effects()

    def test_symlink_escape_is_refused_before_install_commands(self):
        outside = self.root / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_bytes(b"preserve")
        (self.dest / "usr").symlink_to(outside, target_is_directory=True)
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists(), "installer attempted redirected writes")
        self.assertEqual(sentinel.read_bytes(), b"preserve")
        self.assertEqual(list(outside.iterdir()), [sentinel])
        self.assert_no_external_effects()

    def test_dangling_existing_config_symlink_is_not_overwritten(self):
        config = self.dest / "etc/octofan-control.conf"
        config.parent.mkdir()
        missing = config.parent / "operator-managed.conf"
        config.symlink_to(missing)
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(config.is_symlink())
        self.assertFalse(missing.exists())
        self.assert_no_external_effects()

    def test_root_or_relative_staging_destination_is_refused(self):
        root_via_parents = os.path.join(str(self.dest), *([".."] * len(self.dest.parts)))
        self.assertEqual(Path(root_via_parents).resolve(), Path("/"))
        for bad in ["/", "//", root_via_parents, "relative/image"]:
            with self.subTest(destination=bad):
                result = self.run_install(bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())
                self.assert_no_external_effects()


if __name__ == "__main__":
    unittest.main()
