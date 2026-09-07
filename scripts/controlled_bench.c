#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <lz4.h>
#include <numa.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include "lz4_uadk.h"
#include "uadk/wd.h"
#include "uadk/wd_comp.h"

static int mode, workers, depth = 8, window_kb = 32;
static size_t block, input_size;
static unsigned char *input;
static double seconds;
static pthread_barrier_t barrier;
static cpu_set_t allowed;
struct worker {
    int cpu;
    unsigned char *input;
    lz4_uadk_ctx sync;
    lz4_uadk_async_ctx async;
    handle_t raw_session;
    unsigned char *out, *decoded;
    size_t cap;
    uint64_t bytes, compressed, calls;
    double wall, cpu_seconds;
    lz4_uadk_stats stats;
};

static double now(clockid_t clock)
{
    struct timespec t;
    if (clock_gettime(clock, &t)) { perror("clock_gettime"); exit(2); }
    return t.tv_sec + t.tv_nsec * 1e-9;
}

static void require(int ok, const char *what)
{
    if (!ok) { fprintf(stderr, "FAIL: %s (errno=%d)\n", what, errno); exit(2); }
}

static void affinity(struct worker *w)
{
    cpu_set_t current;
    require(!sched_getaffinity(0, sizeof(current), &current), "get affinity");
    require(CPU_COUNT(&current) == 1 && CPU_ISSET(w->cpu, &current), "worker affinity changed");
}

static void all_affinities(void)
{
    DIR *d = opendir("/proc/self/task");
    require(d != NULL, "open tasks");
    struct dirent *e;
    while ((e = readdir(d))) {
        int tid = atoi(e->d_name);
        if (!tid) continue;
        cpu_set_t actual;
        require(!sched_getaffinity(tid, sizeof(actual), &actual), "task affinity");
        for (int i = 0; i < CPU_SETSIZE; i++)
            require(!CPU_ISSET(i, &actual) || CPU_ISSET(i, &allowed), "hidden thread escaped cpuset");
    }
    closedir(d);
}

/* Upstream uses a private 12-byte envelope, not an LZ4F frame. Validate
 * each payload with the independent native LZ4 decoder. */
static size_t check_hw(struct worker *w, size_t size, size_t offset, int count)
{
    size_t p = 0;
    for (int i = 0; i < count; i++) {
        uint32_t magic, header, end;
        require(p + 12 <= size, "truncated envelope");
        memcpy(&magic, w->out + p, 4);
        memcpy(&header, w->out + p + 4, 4);
        size_t n = header & 0x7fffffffU;
        require(magic == 0x184d2204 && p + n + 12 <= size, "invalid envelope");
        memcpy(&end, w->out + p + n + 8, 4);
        require(end == 0, "missing end marker");
        size_t len = input_size - offset < block ? input_size - offset : block;
        if (header & 0x80000000U) {
            require(n == len, "raw payload length");
            memcpy(w->decoded, w->out + p + 8, n);
        } else {
            int decoded = LZ4_decompress_safe((char *)w->out + p + 8,
                                             (char *)w->decoded, n, block);
            require(decoded == (int)len, "native LZ4 decoding failed");
        }
        require(!memcmp(w->decoded, w->input + offset, len), "roundtrip mismatch");
        offset = (offset + len) % input_size;
        p += n + 12;
    }
    require(p == size, "trailing output");
    return offset;
}

static size_t batch(struct worker *w, size_t offset, int verify)
{
    size_t bytes = 0, out_size = 0;
    int count = mode == 2 ? depth : 1;
    size_t start = offset;
    size_t check_offset = start;
    int collected = 0;
    double deadline = now(CLOCK_MONOTONIC) + 10;
    for (int i = 0; i < count; i++) {
        size_t len = input_size - offset < block ? input_size - offset : block;
        int ret;
        if (mode == 0) {
            ret = LZ4_compress_default((char *)w->input + offset, (char *)w->out, len, w->cap);
            require(ret > 0, "software compress");
            out_size = ret;
            if (verify) {
                require(LZ4_decompress_safe((char *)w->out, (char *)w->decoded, ret, block) == (int)len,
                        "software decompress");
                require(!memcmp(w->input + offset, w->decoded, len), "software roundtrip");
            }
        } else if (mode == 1) {
            ret = lz4_uadk_compress(w->sync, w->input + offset, len, w->out, w->cap, &out_size);
            require(ret == 0 && out_size > 0, "hardware compress");
            if (verify) check_hw(w, out_size, offset, 1);
        } else if (mode == 3) {
            struct wd_lz77_zstd_data triples = {0};
            struct wd_comp_req req = {0};
            req.src = w->input + offset; req.src_len = len;
            req.dst = w->out; req.dst_len = w->cap;
            req.op_type = WD_DIR_COMPRESS; req.data_fmt = WD_FLAT_BUF;
            req.last = 1; req.priv = &triples;
            ret = wd_do_comp_sync(w->raw_session, &req);
            require(!ret && !req.status, "raw LZ77 request");
            out_size = triples.lit_num + (size_t)triples.seq_num * 8;
        } else {
            do {
                ret = lz4_uadk_async_submit(w->async, w->input + offset, len, i == count - 1);
                if (ret == -EBUSY || ret == -EAGAIN) {
                    size_t written = 0;
                    int polled = lz4_uadk_async_poll(w->async, w->out, w->cap, &written);
                    require(polled >= 0, "poll under backpressure");
                    if (verify && polled) check_offset = check_hw(w, written, check_offset, polled);
                    collected += polled; out_size += written;
                }
                require(now(CLOCK_MONOTONIC) < deadline, "submission deadline");
            } while (ret == -EBUSY || ret == -EAGAIN);
            if (ret) fprintf(stderr, "async submit ret=%d\n", ret);
            require(ret == 0, "async submit");
        }
        offset = (offset + len) % input_size;
        bytes += len;
    }
    if (mode == 2) {
        while (collected < count) {
            size_t written = 0;
            int ret = lz4_uadk_async_poll(w->async, w->out, w->cap, &written);
            require(ret >= 0, "async poll");
            if (verify && ret) check_offset = check_hw(w, written, check_offset, ret);
            out_size += written;
            collected += ret;
            require(now(CLOCK_MONOTONIC) < deadline, "async deadline");
        }
        require(!lz4_uadk_async_pending(w->async), "async requests remain");
    }
    w->bytes += bytes;
    w->compressed += out_size;
    w->calls += count;
    return offset;
}

static void *run(void *arg)
{
    struct worker *w = arg;
    cpu_set_t set;
    CPU_ZERO(&set); CPU_SET(w->cpu, &set);
    require(!pthread_setaffinity_np(pthread_self(), sizeof(set), &set), "pin worker");
    affinity(w);
    w->input = input;
    if (getenv("LZ4_BENCH_LOCAL")) {
        w->input = malloc(input_size);
        require(w->input != NULL, "local input allocation");
        memcpy(w->input, input, input_size);
    }
    w->cap = (size_t)LZ4_compressBound(block) * (mode == 2 ? depth : 1) + 1024;
    if (mode == 3) w->cap = block * 4 + 4200;
    w->out = malloc(w->cap); w->decoded = malloc(block);
    require(w->out && w->decoded, "worker buffers");
    memset(w->out, 0, w->cap);
    size_t offset = 0;
    size_t chunks = (input_size + block - 1) / block;
    for (size_t i = 0; i < chunks; i += mode == 2 ? depth : 1)
        offset = batch(w, offset, 1);
    affinity(w);
    w->bytes = w->compressed = w->calls = 0;
    offset = 0;
    pthread_barrier_wait(&barrier);
    double start = now(CLOCK_MONOTONIC), cpu_start = now(CLOCK_THREAD_CPUTIME_ID);
    do { offset = batch(w, offset, 0); } while (now(CLOCK_MONOTONIC) - start < seconds);
    w->cpu_seconds = now(CLOCK_THREAD_CPUTIME_ID) - cpu_start;
    w->wall = now(CLOCK_MONOTONIC) - start;
    affinity(w);
    pthread_barrier_wait(&barrier);
    if (mode == 1) lz4_uadk_get_stats(w->sync, &w->stats);
    pthread_barrier_wait(&barrier);
    return NULL;
}

int main(int argc, char **argv)
{
    require(argc == 7, "usage: controlled_bench sw|sync|async|raw workers block_bytes input seconds depth");
    mode = !strcmp(argv[1], "sw") ? 0 : !strcmp(argv[1], "sync") ? 1 : !strcmp(argv[1], "async") ? 2 : 3;
    require(mode != 3 || !strcmp(argv[1], "raw"), "unknown mode");
    workers = atoi(argv[2]); block = strtoul(argv[3], NULL, 10);
    seconds = atof(argv[5]); depth = atoi(argv[6]);
    if (getenv("LZ4_BENCH_WINDOW_KB")) window_kb = atoi(getenv("LZ4_BENCH_WINDOW_KB"));
    require(window_kb == 4 || window_kb == 8 || window_kb == 16 || window_kb == 24 || window_kb == 32,
            "invalid match window");
    require(workers > 0 && workers <= 64 && block > 0 && block <= HW_MAX_INPUT_SIZE &&
            seconds > 0 && depth > 0 && depth <= 32, "invalid arguments");
    require(!sched_getaffinity(0, sizeof(allowed), &allowed), "initial affinity");
    require(CPU_COUNT(&allowed) == workers, "cpuset size differs from workers");
    FILE *f = fopen(argv[4], "rb"); require(f != NULL, "input open");
    require(!fseek(f, 0, SEEK_END), "input seek"); long file_size = ftell(f);
    require(file_size > 0, "empty input"); input_size = file_size;
    rewind(f); input = malloc(input_size); require(input != NULL, "input allocation");
    require(fread(input, 1, input_size, f) == input_size, "input read"); fclose(f);
    if (mode) require(lz4_uadk_init(workers) == 0, "UADK init");
    all_affinities();
    struct worker w[64] = {0}; pthread_t threads[64];
    int next_cpu = 0;
    for (int i = 0; i < workers; i++) {
        while (!CPU_ISSET(next_cpu, &allowed)) next_cpu++;
        w[i].cpu = next_cpu++;
        /* UADK's default session scheduler captures the creating NUMA node.
         * Serialize creation on the owning CPU before starting workers. */
        if (getenv("LZ4_BENCH_LOCAL")) {
            cpu_set_t creator;
            CPU_ZERO(&creator); CPU_SET(w[i].cpu, &creator);
            require(!sched_setaffinity(0, sizeof(creator), &creator), "pin session creator");
        }
        if (mode == 1) {
            lz4_uadk_params p = {.level=8, .window_size=window_kb, .block_size=block/1024, .use_hw=1};
            w[i].sync = getenv("LZ4_BENCH_LOCAL") ?
                lz4_uadk_create_ctx_on_node(&p, numa_node_of_cpu(w[i].cpu)) : lz4_uadk_create_ctx(&p);
            require(w[i].sync != NULL, "sync context");
        } else if (mode == 2) {
            w[i].async = lz4_uadk_async_create_config(depth, workers,
                getenv("LZ4_BENCH_LOCAL") ? numa_node_of_cpu(w[i].cpu) : -1, window_kb);
            require(w[i].async != NULL, "async context");
        } else if (mode == 3) {
            struct wd_comp_sess_setup setup = {0};
            setup.alg_type = WD_LZ77_ONLY; setup.op_type = WD_DIR_COMPRESS;
            setup.comp_lv = WD_COMP_L8;
            setup.win_sz = window_kb == 4 ? WD_COMP_WS_4K : window_kb == 8 ? WD_COMP_WS_8K :
                window_kb == 16 ? WD_COMP_WS_16K : window_kb == 24 ? WD_COMP_WS_24K : WD_COMP_WS_32K;
            w[i].raw_session = wd_comp_alloc_sess(&setup);
            require(w[i].raw_session != 0, "raw session");
        }
    }
    require(!sched_setaffinity(0, sizeof(allowed), &allowed), "restore creator affinity");
    require(!pthread_barrier_init(&barrier, NULL, workers + 1), "barrier init");
    for (int i = 0; i < workers; i++) require(!pthread_create(&threads[i], NULL, run, &w[i]), "thread create");
    pthread_barrier_wait(&barrier);
    double cpu_start = now(CLOCK_PROCESS_CPUTIME_ID);
    all_affinities();
    pthread_barrier_wait(&barrier);
    double process_cpu = now(CLOCK_PROCESS_CPUTIME_ID) - cpu_start;
    all_affinities();
    pthread_barrier_wait(&barrier);
    uint64_t bytes = 0, compressed = 0, calls = 0, fallback = 0;
    double wall = 0, cpu_seconds = 0;
    for (int i = 0; i < workers; i++) {
        pthread_join(threads[i], NULL);
        bytes += w[i].bytes; compressed += w[i].compressed; calls += w[i].calls;
        fallback += w[i].stats.num_sw_fallback;
        if (mode == 1) require(w[i].stats.num_hw_used >= w[i].calls, "missing hardware completions");
        if (w[i].wall > wall) wall = w[i].wall;
        cpu_seconds += w[i].cpu_seconds;
        if (w[i].sync) lz4_uadk_destroy_ctx(w[i].sync);
        if (w[i].async) lz4_uadk_async_destroy(w[i].async);
        if (w[i].raw_session) wd_comp_free_sess(w[i].raw_session);
        free(w[i].out); free(w[i].decoded);
        if (w[i].input != input) free(w[i].input);
    }
    require(fallback == 0, "hardware silently fell back to software");
    printf("%s,%d,%zu,%s,%.6f,%llu,%llu,%llu,%.3f,%.6f,%.6f,%.6f,%.6f,%d,%s,%llu\n",
           argv[1], workers, block, argv[4], wall, (unsigned long long)bytes,
           (unsigned long long)compressed, (unsigned long long)calls, bytes / wall / 1e6,
           cpu_seconds, process_cpu, process_cpu / wall, (double)compressed / bytes, depth,
           mode == 3 ? "RAW_NOT_LZ4" : "PASS",
           (unsigned long long)fallback);
    if (mode) lz4_uadk_fini();
    free(input); pthread_barrier_destroy(&barrier);
    return 0;
}
