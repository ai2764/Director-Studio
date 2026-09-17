"""Build on a native Mac; use temporary staging so existing releases/data survive."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

from verify_linux_portable import verify_runtime
from verify_portable_contents import (
    FLAVORS,
    main as verify_contents,
    required_package_files,
    verify_archive,
)


def host_architecture() -> str:
    if platform.system() != "Darwin":
        raise RuntimeError("macOS is required: PyInstaller cannot cross-compile from Windows or Linux")
    architecture = platform.machine()
    if architecture not in ("arm64", "x86_64"):
        raise RuntimeError(f"Unsupported macOS architecture: {architecture}")
    return architecture


def stage_package(repo: Path, executable: Path, package: Path) -> None:
    package.mkdir(parents=True, exist_ok=False)
    # Both Mac architectures have the same files. Never copy the live .env/data.
    flavor = FLAVORS["macos-arm64"]
    for name in required_package_files(flavor):
        source = executable if name == "DirectorStudio" else repo / name
        if name == ".env":
            source = repo / "backend" / ".env.example"
        destination = package / name
        shutil.copyfile(source, destination)
        if name == ".env":
            portable_env = destination.read_bytes()
            runtime_line = b"DS_DIRECTOR_AGENT_RUNTIME=harness"
            if portable_env.count(runtime_line) != 1:
                raise RuntimeError("Portable .env is missing the Harness runtime setting")
            destination.write_bytes(
                portable_env.replace(runtime_line, b"DS_DIRECTOR_AGENT_RUNTIME=legacy")
            )
        executable_file = name == "DirectorStudio" or name in flavor.wrappers
        if name in flavor.wrappers:
            destination.write_bytes(destination.read_bytes().replace(b"\r\n", b"\n"))
        destination.chmod(0o755 if executable_file else 0o644)


def create_archive(package: Path, archive_path: Path) -> None:
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in [package, *sorted(package.rglob("*"))]:
            info = archive.gettarinfo(str(path), arcname=f"{package.name}/{path.relative_to(package).as_posix()}".rstrip("/."))
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            # Preserve POSIX launch permissions even in packaging tests on Windows.
            info.mode = 0o755 if path.is_dir() or path.name == "DirectorStudio" or path.suffix in (".sh", ".command") else 0o644
            if path.is_file():
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)


def main() -> int:
    architecture = host_architecture()
    repo = Path(__file__).resolve().parents[1]
    flavor = FLAVORS[f"macos-{architecture}"]
    build = repo / "build"
    dist = repo / "dist"
    build.mkdir(exist_ok=True)
    dist.mkdir(exist_ok=True)
    for tool in ("npm", "ffmpeg", "ffprobe", "lipo", "codesign"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"Build prerequisite not found: {tool}")

    def run(*command: str, cwd: Path = repo) -> None:
        print("+ " + " ".join(command), flush=True)
        subprocess.run(command, cwd=cwd, check=True)

    run("npm", "ci", cwd=repo / "frontend")
    # Intel hosted Macs are slower under parallel jsdom workers. Keep all
    # assertions while bounding contention and allowing cold DOM initialization.
    run("npm", "test", "--", "--run", "--maxWorkers=2", "--testTimeout=15000", cwd=repo / "frontend")
    run("npm", "run", "build", cwd=repo / "frontend")
    run(sys.executable, "-m", "pytest", "-q", cwd=repo / "backend")

    with tempfile.TemporaryDirectory(prefix=f"macos-{architecture}-", dir=build) as temporary:
        work = Path(temporary)
        run(sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
            "--workpath", str(work / "pyinstaller"), "--distpath", str(work / "bin"),
            str(repo / "backend" / "packaging" / "director-studio-legacy.spec"))
        executable = work / "bin" / "DirectorStudio"
        run("lipo", str(executable), "-verify_arch", architecture)
        run("codesign", "--verify", "--strict", str(executable))
        package = work / flavor.name
        stage_package(repo, executable, package)
        archive = work / f"{flavor.name}.tar.gz"
        create_archive(package, archive)
        verify_archive(archive, package, flavor)
        if verify_contents(["--platform", f"macos-{architecture}", "--package-root", str(package),
                            "--executable", str(package / "DirectorStudio"), "--archive", str(archive)]):
            raise RuntimeError("macOS portable content verification failed")
        # Test the extracted download, including its executable permission bits.
        extracted = work / "extracted"
        with tarfile.open(archive) as contents:
            contents.extractall(extracted, filter="data")
        runtime = verify_runtime(extracted / flavor.name,
                                 int(os.environ.get("DS_MACOS_VERIFICATION_PORT", "18792")), 90)
        output = dist / archive.name
        shutil.copyfile(archive, output)
        with output.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
        print(json.dumps({"archive": str(output), "sha256": digest, "bytes": output.stat().st_size,
                          "architecture": architecture, "runtime": runtime}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"macOS portable build failed: {error}", file=sys.stderr)
        raise SystemExit(1)
