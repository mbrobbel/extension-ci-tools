"""Regression tests for extensions embedded in a larger repository."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from ci_phase import PhaseRunner


class ExtensionDirectoryTest(unittest.TestCase):
    def environment(self, workspace, **values):
        return {
            **os.environ,
            "GITHUB_ENV": "",
            "GITHUB_OUTPUT": "",
            "GITHUB_PATH": "",
            "CI_PLATFORM": "linux",
            "DUCKDB_PLATFORM": "linux_amd64",
            "GITHUB_WORKSPACE": str(workspace),
            "CI_EXTENSION_DIRECTORY": "extensions/quack",
            "CI_EXTENSION_NAME": "quack",
            "CI_BUILD_TYPE": "release",
            "CI_LINUX_NATIVE_CONTAINER": "true",
            "CI_TEST_CONFIG": "{}",
            "DUCKDB_VERSION": "v2.0.0-test",
            **values,
        }

    def test_directory_defaults_and_rejects_paths_outside_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            for value in ("", "."):
                runner = PhaseRunner(
                    self.environment(workspace, CI_EXTENSION_DIRECTORY=value)
                )
                self.assertEqual(runner.extension_root, workspace)
            for value in ("../outside", str(workspace)):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    PhaseRunner(
                        self.environment(workspace, CI_EXTENSION_DIRECTORY=value)
                    )

    def test_subprocesses_use_extension_directory_and_can_read_parent_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            extension = workspace / "extensions/quack"
            extension.mkdir(parents=True)
            (workspace / "shared.txt").write_text("shared engine")
            runner = PhaseRunner(self.environment(workspace))
            script = (
                "from pathlib import Path; "
                "assert Path('../../shared.txt').read_text() == 'shared engine'; "
                "Path('output.txt').write_text(str(Path.cwd()))"
            )
            runner.run([sys.executable, "-c", script])
            self.assertEqual((extension / "output.txt").read_text(), str(extension))
            self.assertEqual(
                runner.capture(
                    [
                        sys.executable,
                        "-c",
                        "from pathlib import Path; print(Path.cwd())",
                    ]
                ),
                str(extension),
            )
            self.assertFalse((workspace / "output.txt").exists())

    def test_checkout_resolves_nested_duckdb_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            duckdb = workspace / "extensions/quack/duckdb"
            duckdb.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(duckdb)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(duckdb),
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            pin = subprocess.check_output(
                ["git", "-C", str(duckdb), "rev-parse", "HEAD"], text=True
            ).strip()
            # A second DuckDB at the repository root must not be selected.
            (workspace / "duckdb").mkdir()
            runner = PhaseRunner(
                self.environment(workspace, DUCKDB_VERSION="", DUCKDB_COMMIT="")
            )
            runner.checkout()
            self.assertEqual(runner.value("DUCKDB_VERSION"), pin)

    def test_native_build_and_test_run_under_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = PhaseRunner(self.environment(directory))
            with mock.patch("ci_phase.subprocess.run") as run:
                runner.build()
                runner.test()
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [["make", "release"], ["make", "test_release"]],
            )
            for call in run.call_args_list:
                self.assertEqual(
                    call.kwargs["cwd"], Path(directory).resolve() / "extensions/quack"
                )

    def test_docker_preserves_workspace_mount_and_prebuilt_library_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            runner = PhaseRunner(
                self.environment(
                    workspace,
                    CI_LINUX_NATIVE_CONTAINER="false",
                    CI_VCPKG_OVERLAY_PORTS="extension-ci-tools/vcpkg_ports",
                    CI_VCPKG_OVERLAY_TRIPLETS="extension-ci-tools/toolchains",
                    DUCKDB_PREBUILT_LIBRARY=str(
                        workspace / ".ci/prebuilt/libduckdb_static.a"
                    ),
                )
            )
            args = runner.docker_arguments()
            self.assertIn(f"{workspace}:/duckdb_build_dir", args)
            self.assertEqual(
                args[args.index("--workdir") + 1], "/duckdb_build_dir/extensions/quack"
            )
            self.assertIn(f"--env-file={workspace / 'docker_env.txt'}", args)
            runner.create_docker_environment()
            env = dict(
                line.split("=", 1)
                for line in (workspace / "docker_env.txt").read_text().splitlines()
            )
            self.assertEqual(
                env["VCPKG_OVERLAY_PORTS"],
                "/duckdb_build_dir/extensions/quack/extension-ci-tools/vcpkg_ports",
            )
            self.assertEqual(
                env["DUCKDB_PREBUILT_LIBRARY"],
                "/duckdb_build_dir/.ci/prebuilt/libduckdb_static.a",
            )
            self.assertTrue(
                env["OPENSSL_DIR"].startswith(
                    "/duckdb_build_dir/extensions/quack/build/"
                )
            )

    def test_group_configs_are_relative_to_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            extension = workspace / "extensions/quack"
            extension.mkdir(parents=True)
            (workspace / "extension_config.cmake").write_text("root config")
            (extension / "group.cmake").write_text("set(GROUP 1)\n")
            runner = PhaseRunner(
                self.environment(
                    workspace, CI_EXTENSION_CONFIG_PATHS=json.dumps(["group.cmake"])
                )
            )
            runner.inject_extension_config()
            self.assertIn(
                "set(GROUP 1)", (extension / "extension_config.cmake").read_text()
            )
            self.assertEqual(
                (workspace / "extension_config.cmake").read_text(), "root config"
            )

    def test_v2_artifact_handoff_restores_nested_build(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            extension = workspace / "extensions/quack"
            build = extension / "build/release"
            artifact = (
                build / "repository/v2.0.0-test/linux_amd64/quack.duckdb_extension"
            )
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"extension")
            binary = build / "test/unittest"
            binary.parent.mkdir()
            binary.write_bytes(b"test runner")
            output = workspace / "output"
            env = self.environment(
                workspace, CI_UPLOAD_ALL_EXTENSIONS="true", GITHUB_OUTPUT=str(output)
            )
            runner = PhaseRunner(env)
            runner.upload()
            staged = Path(
                dict(line.split("=", 1) for line in output.read_text().splitlines())[
                    "artifact_path"
                ]
            )
            self.assertEqual(staged.parent, workspace / "ci-artifacts")
            self.assertEqual(
                (staged / artifact.relative_to(build / "repository")).read_bytes(),
                b"extension",
            )
            shutil.rmtree(build)
            (workspace / "build").mkdir()
            (workspace / "build/keep.txt").write_text("parent build")
            runner.env["CI_EXTENSION_ARTIFACT_DIR"] = str(staged)
            with mock.patch.object(runner, "_run_tests") as run_tests:
                runner.test()
            run_tests.assert_called_once()
            self.assertEqual(artifact.read_bytes(), b"extension")
            self.assertEqual(binary.read_bytes(), b"test runner")
            self.assertEqual((workspace / "build/keep.txt").read_text(), "parent build")


if __name__ == "__main__":
    unittest.main()
