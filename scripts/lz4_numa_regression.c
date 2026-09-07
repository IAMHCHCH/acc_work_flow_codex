#define _GNU_SOURCE
#include <sched.h>
#include <lz4.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "lz4_uadk.h"

static void check(int ok, const char *why)
{
    if (!ok) { fprintf(stderr, "FAIL: %s\n", why); exit(2); }
}

int main(int argc, char **argv)
{
    check(argc == 3, "usage: lz4_numa_regression creator_cpu session_node");
    int cpu = atoi(argv[1]), node = atoi(argv[2]);
    cpu_set_t mask; CPU_ZERO(&mask); CPU_SET(cpu, &mask);
    check(!sched_setaffinity(0, sizeof(mask), &mask), "pin creator");
    check(!lz4_uadk_init(2), "init");
    check(lz4_uadk_create_ctx_on_node(NULL, -2) == NULL, "invalid negative node");
    check(lz4_uadk_async_create_on_node(2, 2, 4096) == NULL, "invalid absent node");
    unsigned char src[65536], out[131072], decoded[65536];
    for (size_t i = 0; i < sizeof(src); i++) src[i] = (i % 4093) % 251;
    lz4_uadk_params bad = {.use_hw=1, .window_size=7};
    check(lz4_uadk_create_ctx_on_node(&bad, node) == NULL, "invalid sync window");
    check(lz4_uadk_async_create_config(2, 2, node, 7) == NULL, "invalid async window");
    int windows[] = {0, 4, 8, 16, 24, 32};
    for (int wi = 0; wi < 6; wi++) {
    lz4_uadk_params params = {.use_hw=1, .window_size=windows[wi]};
    lz4_uadk_ctx sync = lz4_uadk_create_ctx_on_node(&params, node);
    lz4_uadk_async_ctx async = lz4_uadk_async_create_config(2, 2, node, windows[wi]);
    check(sync && async, "explicit node and window sessions");
    for (int mode = 0; mode < 2; mode++) {
        size_t size = 0;
        if (!mode) {
            check(!lz4_uadk_compress(sync, src, sizeof(src), out, sizeof(out), &size), "sync request");
        } else {
            check(!lz4_uadk_async_submit(async, src, sizeof(src), 1), "async submit");
            time_t end = time(NULL) + 5;
            int ret;
            do {
                ret = lz4_uadk_async_poll(async, out, sizeof(out), &size);
                check(ret >= 0 && time(NULL) <= end, "async poll");
            } while (!ret);
            check(ret == 1, "async count");
        }
        uint32_t magic, header, tail;
        check(size >= 12, "frame minimum");
        memcpy(&magic, out, 4); memcpy(&header, out+4, 4);
        size_t n = header & 0x7fffffffU;
        check(magic == 0x184d2204 && size == n + 12, "frame header");
        memcpy(&tail, out+8+n, 4); check(!tail, "frame tail");
        if (header & 0x80000000U) {
            check(n == sizeof(src), "raw size"); memcpy(decoded, out+8, n);
        } else {
            check(LZ4_decompress_safe((char *)out+8, (char *)decoded, n, sizeof(decoded)) == sizeof(src), "native decode");
        }
        check(!memcmp(src, decoded, sizeof(src)), "round trip");
    }
    lz4_uadk_async_destroy(async); lz4_uadk_destroy_ctx(sync);
    }
    lz4_uadk_fini();
    printf("PASS: creator CPU %d targets NUMA %d; all five windows and default pass native decode; invalid nodes/windows rejected\n", cpu, node);
    return 0;
}
