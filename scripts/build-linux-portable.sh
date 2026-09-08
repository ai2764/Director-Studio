#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
frontend_root="$repo_root/frontend"
backend_root="$repo_root/backend"
build_root="$repo_root/build"
pyinstaller_root="$build_root/pyinstaller"
pyinstaller_dist="$build_root/pyinstaller-dist"
dist_root="$repo_root/dist"
package_name="Director-Studio-Linux-x86_64"
package_root="$dist_root/$package_name"
archive="$dist_root/$package_name.tar.gz"
checksum="$archive.sha256"
verification_port="${DS_LINUX_VERIFICATION_PORT:-18791}"

required_package_files=(
    "DirectorStudio"
    "launch.sh"
    "install-tools.sh"
    "Install-Tools.py"
    "portable-tools-requirements.txt"
    ".env"
    "README.md"
)

die() {
    printf 'Linux portable build failed: %s\n' "$*" >&2
    return 1
}

validate_linux_host() {
    local operating_system architecture
    operating_system="$(uname -s)"
    architecture="$(uname -m)"
    if [[ "$operating_system" != "Linux" ]]; then
        die "Linux is required; detected $operating_system"
    fi
    if [[ "$architecture" != "x86_64" ]]; then
        die "x86_64 is required; detected $architecture"
    fi
}

validate_generated_path() {
    local candidate="$1"
    local resolved_candidate parent

    if [[ -z "$candidate" ]]; then
        die "refusing to validate an empty generated path"
    fi
    resolved_candidate="$(realpath -m -- "$candidate")" || die "could not resolve generated path: $candidate"
    if [[ "$resolved_candidate" == "$repo_root" ]]; then
        die "Refusing to remove the checkout root: $candidate"
    fi
    parent="$(dirname -- "$resolved_candidate")"
    case "$parent" in
        "$repo_root"|"$repo_root"/*)
            ;;
        *)
            die "Refusing to remove a directory outside the repository: $candidate"
            ;;
    esac
    printf '%s\n' "$resolved_candidate"
}

remove_generated_directory() {
    local candidate="$1"
    local resolved_candidate
    resolved_candidate="$(validate_generated_path "$candidate")" || return
    if [[ "$resolved_candidate" != "$build_root" && "$resolved_candidate" != "$dist_root" ]]; then
        die "Refusing to recursively remove a non-generated directory: $candidate"
    fi
    if [[ -e "$resolved_candidate" || -L "$resolved_candidate" ]]; then
        rm -rf -- "$resolved_candidate"
    fi
}

run_checked() {
    local description="$1"
    shift
    if ! "$@"; then
        die "$description failed"
    fi
}

require_package_files() {
    local destination="$1"
    local filename
    for filename in "${required_package_files[@]}"; do
        if [[ ! -f "$destination/$filename" ]]; then
            die "package is missing required file: $filename"
        fi
    done
    for filename in launch.sh install-tools.sh; do
        if [[ ! -x "$destination/$filename" ]]; then
            die "package wrapper is not executable: $filename"
        fi
    done
}

copy_package_files() {
    local built_executable="$1"
    local destination="$2"
    mkdir -p -- "$destination"
    cp -- "$built_executable" "$destination/DirectorStudio"
    cp -- "$repo_root/launch.sh" "$destination/launch.sh"
    cp -- "$repo_root/install-tools.sh" "$destination/install-tools.sh"
    cp -- "$repo_root/Install-Tools.py" "$destination/Install-Tools.py"
    cp -- "$repo_root/portable-tools-requirements.txt" "$destination/portable-tools-requirements.txt"
    cp -- "$backend_root/.env.example" "$destination/.env"
    cp -- "$repo_root/README.md" "$destination/README.md"
    chmod 0755 "$destination/DirectorStudio" "$destination/launch.sh" "$destination/install-tools.sh"
    chmod 0644 "$destination/Install-Tools.py" "$destination/portable-tools-requirements.txt" "$destination/.env" "$destination/README.md"
    require_package_files "$destination"
}

main() {
    local built_executable file_output archive_sha256 archive_bytes

    validate_linux_host
    remove_generated_directory "$build_root"
    remove_generated_directory "$dist_root"
    mkdir -p -- "$pyinstaller_root" "$pyinstaller_dist" "$package_root"

    (
        cd -- "$frontend_root"
        run_checked "npm ci" npm ci
        run_checked "npm test" npm test -- --run
        run_checked "npm build" npm run build
    )

    (
        cd -- "$backend_root"
        run_checked "backend pytest" python -m pytest -q
    )

    run_checked "PyInstaller" python -m PyInstaller \
        --clean \
        --noconfirm \
        --workpath "$pyinstaller_root" \
        --distpath "$pyinstaller_dist" \
        "$backend_root/packaging/director-studio-legacy.spec"

    built_executable="$pyinstaller_dist/DirectorStudio"
    if [[ ! -f "$built_executable" ]]; then
        die "PyInstaller did not produce DirectorStudio"
    fi
    file_output="$(file "$built_executable")"
    printf '%s\n' "$file_output"
    if [[ "$file_output" != *"ELF 64-bit"* || "$file_output" != *"x86-64"* ]]; then
        die "PyInstaller output is not an ELF 64-bit x86-64 executable"
    fi

    copy_package_files "$built_executable" "$package_root"
    run_checked "Linux runtime verification" python "$repo_root/scripts/verify_linux_portable.py" \
        --package-root "$package_root" \
        --port "$verification_port" \
        --timeout-sec 60
    run_checked "portable archive creation" tar -C "$dist_root" -czf "$archive" "$package_name"
    run_checked "portable content verification" python "$repo_root/scripts/verify_portable_contents.py" \
        --platform linux \
        --package-root "$package_root" \
        --executable "$package_root/DirectorStudio" \
        --archive "$archive"
    (
        cd -- "$dist_root"
        sha256sum "$package_name.tar.gz" > "$package_name.tar.gz.sha256"
    )

    read -r archive_sha256 _ < "$checksum"
    archive_bytes="$(stat -c '%s' "$archive")"
    python -c 'import json, sys; print(json.dumps({"package_root": sys.argv[1], "archive": sys.argv[2], "checksum": sys.argv[3], "sha256": sys.argv[4], "bytes": int(sys.argv[5])}))' \
        "$package_root" "$archive" "$checksum" "$archive_sha256" "$archive_bytes"
}

if [[ "${DS_BUILD_TEST_MODE:-0}" == "1" ]]; then
    return 0 2>/dev/null || exit 0
fi

main "$@"
