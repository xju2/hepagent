/* ising_pt.c -- ground-state search for Ising models on arbitrary sparse graphs.
 *
 *   E(s) = - sum_{(i,j)} J_ij s_i s_j ,  s_i = +-1,  integer J_ij (scale real couplings).
 *
 * Method: parallel tempering (replica exchange) with two replicas per temperature and
 * Houdayer isoenergetic cluster moves between them at the colder temperatures
 * (Zhu, Ochoa, Katzgraber, PRL 115, 077201 (2015)), plus a zero-temperature polish of
 * every new record. The best configuration is written atomically whenever it improves.
 *
 * Build:  gcc -O3 -march=native -o ising_pt ising_pt.c -lm
 * Usage:  ising_pt INST OUT [seconds=600] [seed=1] [nT=32] [Tlo=0.4] [Thi=3.0] [icm_frac=0.5] [target]
 *   INST: text file, first line "N M", then M lines "i j J" (0-based sites).
 *   Tlo/Thi are in units of the rms coupling. Stops at `seconds` or once E <= target.
 *   OUT: N tokens "+1"/"-1". If OUT already holds a valid configuration, the run starts
 *   from it and only ever replaces it with a lower energy. Progress lines "t=<s> round=<n> best=<E>" go to stdout.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static uint64_t rs[4];
static inline uint64_t rotl(uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }
static inline uint64_t rnd(void) { /* xoshiro256** */
    uint64_t r = rotl(rs[1] * 5, 7) * 9, t = rs[1] << 17;
    rs[2] ^= rs[0]; rs[3] ^= rs[1]; rs[1] ^= rs[2]; rs[0] ^= rs[3]; rs[2] ^= t; rs[3] = rotl(rs[3], 45);
    return r;
}
static inline double u01(void) { return (rnd() >> 11) * (1.0 / 9007199254740992.0); }
static void seed_rng(uint64_t s) {
    for (int k = 0; k < 4; k++) { /* splitmix64 */
        uint64_t z = (s += 0x9E3779B97F4A7C15ULL);
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL; z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
        rs[k] = z ^ (z >> 31);
    }
}

static int N, M, *off, *nb, *w, *ei, *ej, *eJ;

static long energy(const signed char *s) {
    long e = 0;
    for (int m = 0; m < M; m++) e -= (long)eJ[m] * s[ei[m]] * s[ej[m]];
    return e;
}
static inline int field(const signed char *s, int i) {
    int h = 0;
    for (int k = off[i]; k < off[i + 1]; k++) h += w[k] * s[nb[k]];
    return h;
}
/* one Metropolis sweep; ptab[d] = exp(-2 d / T) for d = s_i h_i > 0; returns the energy change */
static long sweep(signed char *s, const double *ptab) {
    long de = 0;
    for (int i = 0; i < N; i++) {
        int d = s[i] * field(s, i);
        if (d <= 0 || u01() < ptab[d]) { s[i] = -s[i]; de += 2 * d; }
    }
    return de;
}
static void quench(signed char *s) {
    for (int moved = 1; moved;) {
        moved = 0;
        for (int i = 0; i < N; i++)
            if (s[i] * field(s, i) < 0) { s[i] = -s[i]; moved = 1; }
    }
}
static void save(const char *out, const signed char *s) {
    char tmp[4096];
    snprintf(tmp, sizeof tmp, "%s.tmp", out);
    FILE *f = fopen(tmp, "w");
    if (!f) { perror(tmp); exit(1); }
    for (int i = 0; i < N; i++) fputs(s[i] > 0 ? "+1\n" : "-1\n", f);
    fclose(f);
    rename(tmp, out);
}
static double now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + 1e-9 * t.tv_nsec;
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s INST OUT [seconds seed nT Tlo Thi icm_frac target]\n", argv[0]); return 2; }
    double seconds = argc > 3 ? atof(argv[3]) : 600;
    seed_rng(argc > 4 ? strtoull(argv[4], 0, 10) : 1);
    int nT = argc > 5 ? atoi(argv[5]) : 32;
    double Tlo = argc > 6 ? atof(argv[6]) : 0.4, Thi = argc > 7 ? atof(argv[7]) : 3.0;
    double icm_frac = argc > 8 ? atof(argv[8]) : 0.5;
    long target = argc > 9 ? atol(argv[9]) : -(1L << 60);

    FILE *f = fopen(argv[1], "r");
    if (!f || fscanf(f, "%d %d", &N, &M) != 2) { fprintf(stderr, "cannot read %s\n", argv[1]); return 2; }
    ei = malloc(M * sizeof *ei); ej = malloc(M * sizeof *ej); eJ = malloc(M * sizeof *eJ);
    off = calloc(N + 2, sizeof *off);
    double j2 = 0; int maxh = 0;
    for (int m = 0; m < M; m++) {
        if (fscanf(f, "%d %d %d", &ei[m], &ej[m], &eJ[m]) != 3 || ei[m] < 0 || ej[m] < 0 || ei[m] >= N || ej[m] >= N) {
            fprintf(stderr, "bad edge on line %d\n", m + 2); return 2;
        }
        off[ei[m] + 2]++; off[ej[m] + 2]++; j2 += (double)eJ[m] * eJ[m];
    }
    fclose(f);
    for (int i = 0; i < N; i++) off[i + 2] += off[i + 1];
    nb = malloc(2 * M * sizeof *nb); w = malloc(2 * M * sizeof *w);
    for (int m = 0; m < M; m++) { /* off[i+1] is the fill cursor of site i */
        nb[off[ei[m] + 1]] = ej[m]; w[off[ei[m] + 1]++] = eJ[m];
        nb[off[ej[m] + 1]] = ei[m]; w[off[ej[m] + 1]++] = eJ[m];
    }
    for (int i = 0; i < N; i++) {
        int h = 0;
        for (int k = off[i]; k < off[i + 1]; k++) h += abs(w[k]);
        if (h > maxh) maxh = h;
    }
    double jrms = sqrt(j2 / M);

    int icmK = (int)(icm_frac * nT);
    double *beta = malloc(nT * sizeof *beta), *ptab = malloc((size_t)nT * (maxh + 1) * sizeof *ptab);
    for (int t = 0; t < nT; t++) {
        double T = jrms * Tlo * pow(Thi / Tlo, nT > 1 ? (double)t / (nT - 1) : 0);
        beta[t] = 1 / T;
        for (int d = 0; d <= maxh; d++) ptab[(size_t)t * (maxh + 1) + d] = exp(-2.0 * d / T);
    }
    /* rep[c][t]: replica of copy c currently at temperature t */
    signed char **rep[2], *best = malloc(N), *trial = malloc(N);
    long *E[2], bestE = 1L << 60;
    int *stack = malloc(N * sizeof *stack);
    for (int c = 0; c < 2; c++) {
        rep[c] = malloc(nT * sizeof *rep[c]); E[c] = malloc(nT * sizeof *E[c]);
        for (int t = 0; t < nT; t++) {
            rep[c][t] = malloc(N);
            for (int i = 0; i < N; i++) rep[c][t][i] = rnd() & 1 ? 1 : -1;
            E[c][t] = energy(rep[c][t]);
        }
    }
    /* An existing valid OUT is the record to beat (never overwrite it with worse) and
       seeds the coldest replica, so runs can be resumed or chained. */
    if ((f = fopen(argv[2], "r"))) {
        int ok = 1, v;
        for (int i = 0; i < N && ok; i++) { ok = fscanf(f, "%d", &v) == 1 && (v == 1 || v == -1); trial[i] = v; }
        fclose(f);
        if (ok) {
            memcpy(best, trial, N); bestE = energy(best);
            memcpy(rep[0][0], best, N); E[0][0] = bestE;
            printf("resuming from %s: E=%ld\n", argv[2], bestE);
        }
    }
    double t0 = now(), last_save = 0, last_log = 0;
    int dirty = 0;
    for (long round = 0;; round++) {
        for (int c = 0; c < 2; c++)
            for (int t = 0; t < nT; t++) E[c][t] += sweep(rep[c][t], ptab + (size_t)t * (maxh + 1));
        /* Houdayer move: flip one connected cluster of disagreeing sites in both copies */
        for (int t = 0; t < icmK; t++) {
            signed char *a = rep[0][t], *b = rep[1][t];
            int neg = 0;
            for (int i = 0; i < N; i++) neg += a[i] != b[i];
            if (neg == 0 || neg == N) continue;
            if (2 * neg > N) { for (int i = 0; i < N; i++) b[i] = -b[i]; } /* Z2: keep clusters small */
            int i0;
            do i0 = rnd() % N; while (a[i0] == b[i0]);
            int top = 0;
            stack[top++] = i0; a[i0] = -a[i0]; b[i0] = -b[i0];
            /* visited sites are flipped in both copies, so they still disagree: mark them
               in copy a as +-3 until the cluster is complete */
            a[i0] *= 3;
            while (top) {
                int i = stack[--top];
                for (int k = off[i]; k < off[i + 1]; k++) {
                    int j = nb[k];
                    if (a[j] != b[j] && abs(a[j]) == 1) {
                        a[j] = -3 * a[j]; b[j] = -b[j]; stack[top++] = j;
                    }
                }
            }
            for (int i = 0; i < N; i++) if (abs(a[i]) == 3) a[i] /= 3;
            E[0][t] = energy(a); E[1][t] = energy(b);
        }
        for (int c = 0; c < 2; c++)
            for (int t = (round & 1); t + 1 < nT; t += 2) {
                double x = (beta[t] - beta[t + 1]) * (double)(E[c][t] - E[c][t + 1]);
                if (x >= 0 || u01() < exp(x)) {
                    signed char *s = rep[c][t]; rep[c][t] = rep[c][t + 1]; rep[c][t + 1] = s;
                    long e = E[c][t]; E[c][t] = E[c][t + 1]; E[c][t + 1] = e;
                }
            }
        for (int c = 0; c < 2; c++)
            for (int t = 0; t < 4 && t < nT; t++)
                if (E[c][t] < bestE + 2 * maxh) { /* near the record: polish a copy */
                    memcpy(trial, rep[c][t], N); quench(trial);
                    long e = energy(trial);
                    if (e < bestE) { bestE = e; memcpy(best, trial, N); dirty = 1; }
                }
        if ((round & 15) == 0 || bestE <= target) {
            double t = now() - t0;
            if (dirty && (t - last_save > 1 || bestE <= target || t >= seconds)) { save(argv[2], best); dirty = 0; last_save = t; }
            if (t - last_log > 30 || bestE <= target || t >= seconds) {
                printf("t=%.0f round=%ld best=%ld\n", t, round, bestE); fflush(stdout); last_log = t;
            }
            if (bestE <= target || t >= seconds) break;
        }
    }
    if (dirty) save(argv[2], best);
    return 0;
}
