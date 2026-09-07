#!/usr/bin/env bash
set -euo pipefail
runtime=${1:?runtime directory containing controlled_bench and .libs}
corpus=${2:?Silesia corpus directory}
output=${3:?output directory}
exec python3 "$(dirname "$0")/run_lz4_matrix.py" \
    --runtime "$runtime" --corpus "$corpus" --output "$output" \
    --phase scaling --seconds "${SECONDS_PER_POINT:-2}" --repeats "${REPEATS:-3}"
