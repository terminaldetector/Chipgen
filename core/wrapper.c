/*
 * wrapper.c — thin render/control layer on top of Nuked-OPN2 (ym3438.c/.h)
 *
 * Exposes a small, ctypes-friendly C API:
 *   opn2_new / opn2_free / opn2_reset
 *   opn2_write(port, data)
 *   opn2_render(n_samples, out_i16_stereo)  -> fills native-rate audio
 *
 * Native output rate = chip_clock / 6 / 24 (see README for derivation).
 *
 * Two things here are less obvious than they look, and both were bugs
 * once:
 *
 * 1. THE OUTPUT PINS ARE TIME-MULTIPLEXED. mol/mor do not carry "the
 *    chip's output" — they carry one channel at a time, cycling through
 *    all six every 24 internal clocks, exactly like the real YM2612's
 *    single time-shared DAC. Reading the pin once per 24 clocks gets you
 *    one channel (and, three times out of four, not even that: the
 *    YM2612 branch only enables the pin on one sub-clock in four, and
 *    emits its DC "ladder" level the rest of the time). One output sample
 *    is the SUM of all 24 sub-clocks. That sum is what Nuked-OPN2's own
 *    OPN2_Generate() produces, and it is what this file now produces.
 *
 * 2. REGISTER WRITES TAKE TIME, AND THAT TIME IS MUSIC. A write has to be
 *    clocked into the chip before the next one can be issued, or the
 *    latch is simply overwritten and the first write is lost. So writes
 *    have to advance the chip — but if those clocks are thrown away, the
 *    audio they would have produced disappears with them, and a patch
 *    change (30 writes, ~100 samples) punches a hole in the sound. The
 *    clocks are therefore accumulated into a pending-sample FIFO that
 *    opn2_render() drains first. Nothing is discarded; a write lands at
 *    the sample position where it actually happened.
 */
#include <stdlib.h>
#include <string.h>
#include "ym3438.h"

/* Sub-clocks per output sample: 6 channels * 4 operator slots. */
#define OPN2_SLOTS_PER_SAMPLE 24

/* Clocks to settle after each byte written to the bus. The chip holds a
 * busy flag for 32 internal clocks after a data write (see write_busy_cnt
 * in ym3438.c); 40 clears that with margin and is cheap. */
#define OPN2_WRITE_SETTLE 40

/*
 * Scaling from the 24-slot accumulator to int16.
 *
 * Per sample, six sub-clocks carry a real channel value (9-bit signed,
 * multiplied by 3 in the YM2612 DAC model) and the other eighteen carry
 * the +/-3 ladder level. Worst case is therefore about
 *   6 * 3 * 256 + 18 * 3 = 4662,
 * so a gain of 7 fills the int16 range (4662 * 7 = 32634) without ever
 * clipping. Clamps below are belt-and-braces, not load-bearing.
 *
 * YM3438 mode lands on the same scale by a different route: no x3, but
 * the pin carries each channel for three sub-clocks out of four instead
 * of one, so the 24-slot sum comes out the same size. One gain works for
 * both, and switching chip type does not change how loud a track is.
 */
#define OPN2_OUTPUT_GAIN 7

/*
 * Which Genesis you are emulating.
 *
 * The discrete YM2612 (Model 1, Model 2 VA2) drives its single
 * time-shared DAC through a resistor ladder that never quite reaches
 * zero: the output pin emits a small fixed level whenever it is not
 * carrying a channel. That is the famous "ladder effect" -- it is why a
 * hard-panned channel still bleeds a quiet square into the other side,
 * and a large part of why Model 1 units sound grittier than later ones.
 *
 * The YM3438 / ASIC revisions (Model 2 VA3 and later) integrated the DAC
 * and dropped the ladder, so muting is clean.
 *
 * OPN2_SetChipType sets a FILE-STATIC in ym3438.c, not per-instance, so
 * this is a global switch by construction -- see python/opn2.py, which
 * says so out loud rather than pretending each chip object owns it.
 */
static Bit32u requested_chip_type = ym3438_mode_ym2612;

void opn2_set_chip_type(int discrete_ym2612) {
    requested_chip_type = discrete_ym2612 ? ym3438_mode_ym2612 : 0;
    OPN2_SetChipType(requested_chip_type);
}

int opn2_get_chip_type(void) {
    return (requested_chip_type & ym3438_mode_ym2612) ? 1 : 0;
}

typedef struct {
    ym3438_t chip;

    /* accumulator for the sample currently being assembled */
    long acc_l, acc_r;
    int acc_slots;

    /* FIFO of samples produced while servicing register writes */
    short *pending;
    int pending_cap;      /* capacity in FRAMES */
    int pending_head;     /* next frame to hand out */
    int pending_count;    /* frames available */
} OPN2Handle;

static int opn2_reserve(OPN2Handle *h, int frames) {
    int needed, cap;
    if (h->pending_head > 0) {
        /* compact first — the common case never has to grow at all */
        memmove(h->pending, h->pending + h->pending_head * 2,
                (size_t)h->pending_count * 2 * sizeof(short));
        h->pending_head = 0;
    }
    needed = h->pending_count + frames;
    if (needed <= h->pending_cap) return 1;
    cap = h->pending_cap ? h->pending_cap : 256;
    while (cap < needed) cap *= 2;
    {
        short *grown = (short *)realloc(h->pending, (size_t)cap * 2 * sizeof(short));
        if (!grown) return 0;
        h->pending = grown;
        h->pending_cap = cap;
    }
    return 1;
}

static void opn2_emit(OPN2Handle *h, short l, short r) {
    if (!opn2_reserve(h, 1)) return;   /* out of memory: drop rather than crash */
    h->pending[(h->pending_head + h->pending_count) * 2]     = l;
    h->pending[(h->pending_head + h->pending_count) * 2 + 1] = r;
    h->pending_count++;
}

static void opn2_scale(long acc_l, long acc_r, short *out_l, short *out_r) {
    long l = acc_l * OPN2_OUTPUT_GAIN;
    long r = acc_r * OPN2_OUTPUT_GAIN;
    if (l > 32767) l = 32767;
    if (l < -32768) l = -32768;
    if (r > 32767) r = 32767;
    if (r < -32768) r = -32768;
    *out_l = (short)l;
    *out_r = (short)r;
}

/* Advance one internal clock, folding its output into the current sample.
 * Returns 1 when that completed a sample, writing it to out (if non-NULL)
 * or to the pending FIFO. */
static int opn2_step(OPN2Handle *h, short *out) {
    Bit16s buffer[2];
    OPN2_Clock(&h->chip, buffer);
    h->acc_l += buffer[0];
    h->acc_r += buffer[1];
    if (++h->acc_slots < OPN2_SLOTS_PER_SAMPLE) return 0;

    {
        short l, r;
        opn2_scale(h->acc_l, h->acc_r, &l, &r);
        h->acc_l = h->acc_r = 0;
        h->acc_slots = 0;
        if (out) { out[0] = l; out[1] = r; }
        else     { opn2_emit(h, l, r); }
    }
    return 1;
}

OPN2Handle *opn2_new(void) {
    OPN2Handle *h = (OPN2Handle *)malloc(sizeof(OPN2Handle));
    if (!h) return NULL;
    memset(h, 0, sizeof(OPN2Handle));
    OPN2_SetChipType(requested_chip_type);
    OPN2_Reset(&h->chip);
    return h;
}

void opn2_free(OPN2Handle *h) {
    if (!h) return;
    free(h->pending);
    free(h);
}

void opn2_reset(OPN2Handle *h) {
    OPN2_Reset(&h->chip);
    h->acc_l = h->acc_r = 0;
    h->acc_slots = 0;
    h->pending_head = h->pending_count = 0;
}

/* port: 0 = address bank 0, 1 = data bank 0, 2 = address bank 1, 3 = data bank 1 */
void opn2_write(OPN2Handle *h, unsigned int port, unsigned char data) {
    int i;
    OPN2_Write(&h->chip, port, data);
    for (i = 0; i < OPN2_WRITE_SETTLE; i++) {
        opn2_step(h, NULL);
    }
}

/* Number of frames buffered by register writes and not yet handed back. */
int opn2_pending(OPN2Handle *h) {
    return h->pending_count;
}

/*
 * Render n_samples of native-rate interleaved 16-bit stereo audio into out.
 * out must be pre-allocated with space for n_samples * 2 shorts.
 */
void opn2_render(OPN2Handle *h, int n_samples, short *out) {
    int i = 0;

    /* hand back anything the write path already produced */
    while (i < n_samples && h->pending_count > 0) {
        out[i * 2]     = h->pending[h->pending_head * 2];
        out[i * 2 + 1] = h->pending[h->pending_head * 2 + 1];
        h->pending_head++;
        h->pending_count--;
        i++;
    }
    if (h->pending_count == 0) h->pending_head = 0;

    for (; i < n_samples; i++) {
        while (!opn2_step(h, &out[i * 2])) {
            /* keep clocking until this sample's 24 slots are complete */
        }
    }
}
