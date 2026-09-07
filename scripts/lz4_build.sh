#!/bin/bash
set -euo pipefail
base=$1
cd "$base"
mkdir -p uadk runtime/lib/uadk deps/include deps/lib
tar -xf uadk-source.tar -C uadk
cp deps/lz4*.h deps/include/
cp "$HOME/.numa-root/include/"*.h deps/include/
ln -sfn "$base/uadk/include" deps/include/uadk
ln -sfn /usr/lib64/libnuma.so.1 deps/lib/libnuma.so
ln -sfn /usr/lib64/liblz4.so.1 deps/lib/liblz4.so
cd uadk
./autogen.sh
CPPFLAGS="-I$base/deps/include" LDFLAGS="-L$base/deps/lib" ./configure --disable-static --enable-shared --prefix="$base/runtime"
make -j8 libwd.la libwd_comp.la libhisi_zip.la
for lib in libwd libwd_comp libhisi_zip; do
    cp -a .libs/$lib.so* "$base/runtime/lib/"
done
cp -a .libs/libhisi_zip.so* "$base/runtime/lib/uadk/"
cp uadk.cnf "$base/runtime/lib/uadk/"
cd "$base/source"
export LIBRARY_PATH="$base/deps/lib${LIBRARY_PATH:+:$LIBRARY_PATH}"
make -j8 UADK_ROOT="$base/uadk" CFLAGS="-O3 -g -std=gnu99 -DLZ4_UADK_PLATFORM=\"Linux\" -I$base/deps/include -I$base/uadk/include -Iinclude" LDFLAGS="-L$base/runtime/lib -L$base/deps/lib -lwd -lwd_comp -lnuma -lpthread -lrt -ldl -llz4"
cp bench_lz4_uadk test_lz4_uadk lz4uac "$base/runtime/"
cd "$base"
tar -czf runtime.tgz runtime
sha256sum runtime/lib/*.so.* runtime/lib/uadk/*.so.* runtime/bench_lz4_uadk
