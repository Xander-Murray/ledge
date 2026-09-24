#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
package_dir="${project_root}/build/lambda"
archive_path="${project_root}/build/ledge-lambda.zip"
python_bin="${LEDGE_BUILD_PYTHON:-${project_root}/venv/bin/python}"

if [[ ! -x "${python_bin}" ]]; then
    echo "Python executable not found: ${python_bin}" >&2
    exit 1
fi

# Native wheels must match the runtime selected in the Lambda console.
"${python_bin}" -c 'import platform, sys; sys.exit(0 if sys.version_info[:2] == (3, 14) and platform.system() == "Linux" and platform.machine() == "x86_64" else "Build requires Linux x86_64 and Python 3.14")'

rm -rf -- "${package_dir}"
rm -f -- "${archive_path}"
mkdir -p -- "${package_dir}"

"${python_bin}" -m pip install \
    --disable-pip-version-check \
    --no-compile \
    --target "${package_dir}" \
    "${project_root}"

(
    cd -- "${package_dir}"
    zip -q -r "${archive_path}" .
)

echo "Built ${archive_path}"
