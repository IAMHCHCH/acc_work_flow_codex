#include <errno.h>
#include <lz4.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "lz4_uadk.h"

static void check(int ok, const char *message)
{
    if (!ok) { fprintf(stderr, "FAIL: %s\n", message); exit(2); }
}
static double now(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec * 1e-9;
}
int main(void)
{
    const size_t n = 256 * 1024, cap = 4 * (n + 64);
    unsigned char *src = malloc(n), *expected = malloc(4*n);
    unsigned char *out = malloc(cap), *decoded = malloc(n);
    check(src && expected && out && decoded, "allocation");
    for (int order = 0; order < 2; order++) {
        if (!order) check(lz4_uadk_init(2) == 0, "sync first init");
        lz4_uadk_async_ctx a = lz4_uadk_async_create(4, 2);
        check(a != NULL, "async create");
        lz4_uadk_ctx s = lz4_uadk_create_ctx(NULL);
        check(s != NULL, "sync context after async");
        for (int batch = 0; batch < 8; batch++) {
            for (int i = 0; i < 4; i++) {
                for (size_t j = 0; j < n; j++)
                    src[j] = (j % 4093 + i * 7 + batch * 13) % 251;
                memcpy(expected + i*n, src, n);
                check(!lz4_uadk_async_submit(a, src, n, i == 3), "submit");
                memset(src, 0xa5, n);
            }
            check(lz4_uadk_async_submit(a, src, n, 1) == -EAGAIN, "full queue");
            size_t written; int ret; double deadline = now() + 5;
            do {
                ret = lz4_uadk_async_poll(a, out, 1, &written);
                check(now() < deadline, "completion timeout");
            } while (ret == 0);
            check(ret == -ENOSPC && written == 0 && lz4_uadk_async_pending(a) == 4,
                  "ENOSPC must preserve pending results");
            int received = 0;
            while (received < 4) {
                ret = lz4_uadk_async_poll(a, out, cap, &written);
                check(ret >= 0 && now() < deadline, "poll retry");
                size_t p = 0;
                for (int k = 0; k < ret; k++) {
                    uint32_t magic, header, end;
                    check(p + 12 <= written, "frame bounds");
                    memcpy(&magic, out+p, 4); memcpy(&header, out+p+4, 4);
                    size_t size = header & 0x7fffffffU;
                    check(magic == 0x184d2204 && p + size + 12 <= written, "frame header");
                    memcpy(&end, out+p+size+8, 4); check(end == 0, "frame end");
                    if (header & 0x80000000U) {
                        check(size == n, "raw length"); memcpy(decoded, out+p+8, n);
                    } else {
                        check(LZ4_decompress_safe((char *)out+p+8, (char *)decoded, size, n) == (int)n,
                              "native decoder");
                    }
                    check(!memcmp(decoded, expected + received*n, n), "input ownership or ordering");
                    received++; p += size + 12;
                }
                check(p == written, "output accounting");
            }
            check(lz4_uadk_async_pending(a) == 0, "drained queue");
            size_t compressed = 0;
            check(!lz4_uadk_compress(s, expected, n, out, cap, &compressed), "sync after async");
        }
        lz4_uadk_async_destroy(a); lz4_uadk_destroy_ctx(s); lz4_uadk_fini();
    }
    free(src); free(expected); free(out); free(decoded);
    puts("PASS: 64 requests; ownership, ordering, queue wrap, ENOSPC retry, native decoding, mixed initialization");
    return 0;
}
