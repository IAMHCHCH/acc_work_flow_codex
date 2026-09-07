#!/usr/bin/env bash
set -euo pipefail
source_dir=${1:?source directory}
uadk_dir=${2:?UADK build directory}
deps_dir=${3:?dependency directory}
cd "$source_dir"
make -j8 CFLAGS="-O3 -g -std=gnu99 -DLZ4_UADK_PLATFORM=\\\"Linux\\\" -I$uadk_dir/include -I$deps_dir/include -Iinclude" \
  LDFLAGS="-L$uadk_dir/.libs -L$deps_dir/lib -lwd -lwd_comp -lnuma -lpthread -lrt -ldl -llz4" \
  test_lz4_uadk bench_lz4_uadk lz4uac
if test -f controlled_bench.c; then
  gcc -O3 -g -std=gnu11 -Wall -Wextra -Iinclude -I"$deps_dir/include" \
    controlled_bench.c liblz4_uadk.a -o controlled_bench \
    -L"$uadk_dir/.libs" -L"$deps_dir/lib" -lwd -lwd_comp -lnuma -lpthread -lrt -ldl -llz4
fi
if test -f lz4_async_regression.c; then
  gcc -O2 -g -std=gnu11 -Wall -Wextra -Iinclude -I"$deps_dir/include" \
    lz4_async_regression.c liblz4_uadk.a -o lz4_async_regression \
    -L"$uadk_dir/.libs" -L"$deps_dir/lib" -lwd -lwd_comp -lnuma -lpthread -lrt -ldl -llz4
fi
if test -f lz4_numa_regression.c; then
  gcc -O2 -g -std=gnu11 -Wall -Wextra -Iinclude -I"$deps_dir/include" \
    lz4_numa_regression.c liblz4_uadk.a -o lz4_numa_regression \
    -L"$uadk_dir/.libs" -L"$deps_dir/lib" -lwd -lwd_comp -lnuma -lpthread -lrt -ldl -llz4
fi
