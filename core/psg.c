/*
 * psg.c — Sega PSG (SN76489/SN76496 variant used in Master System / Genesis)
 *
 * Faithful register-level model, not a frequency-domain shortcut:
 *   - 3 tone channels, each a 10-bit down-counter driving a toggle flip-flop
 *   - 1 noise channel: same counter mechanism, feeding a 16-bit LFSR that
 *     shifts only on the counter flip-flop's 0->1 transitions
 *   - White noise feedback taps bits 0 and 3 (XOR/parity), fed back into
 *     bit 15; periodic noise taps only bit 0. LFSR resets to 0x8000 on any
 *     write to the noise register. (Sega variant: 16-bit register, not the
 *     original TI 15-bit one.)
 *   - 4-bit attenuation per channel, -2dB per step, matching the widely
 *     published SN76489 volume table.
 * Reference: SMS Power! development wiki, "SN76489" page.
 */
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

typedef struct {
    uint16_t tone_reg[3];
    uint8_t  vol_reg[4];
    uint8_t  noise_reg;

    int32_t  tone_counter[3];
    uint8_t  tone_output[3];

    int32_t  noise_counter;
    uint8_t  noise_ff;
    uint16_t lfsr;

    uint8_t  latched_channel;
    uint8_t  latched_type;
} PSGHandle;

static const int16_t VOLUME_TABLE[16] = {
    32767, 26028, 20675, 16422, 13045, 10362, 8231, 6568,
    5193,  4125,  3277,  2603,  2067,  1642,  1304, 0
};

PSGHandle *psg_new(void) {
    PSGHandle *p = (PSGHandle *)calloc(1, sizeof(PSGHandle));
    if (!p) return NULL;
    p->vol_reg[0] = p->vol_reg[1] = p->vol_reg[2] = p->vol_reg[3] = 0x0F;
    p->tone_output[0] = p->tone_output[1] = p->tone_output[2] = 1;
    p->lfsr = 0x8000;
    return p;
}

void psg_free(PSGHandle *p) { free(p); }

void psg_reset(PSGHandle *p) {
    memset(p, 0, sizeof(PSGHandle));
    p->vol_reg[0] = p->vol_reg[1] = p->vol_reg[2] = p->vol_reg[3] = 0x0F;
    p->tone_output[0] = p->tone_output[1] = p->tone_output[2] = 1;
    p->lfsr = 0x8000;
}

void psg_write(PSGHandle *p, unsigned char byte) {
    if (byte & 0x80) {
        uint8_t channel = (byte >> 5) & 0x03;
        uint8_t type = (byte >> 4) & 0x01;
        uint8_t data = byte & 0x0F;
        p->latched_channel = channel;
        p->latched_type = type;
        if (type) {
            p->vol_reg[channel] = data;
        } else if (channel == 3) {
            p->noise_reg = data & 0x07;
            p->lfsr = 0x8000;
        } else {
            p->tone_reg[channel] = (p->tone_reg[channel] & 0x3F0) | data;
        }
    } else {
        uint8_t data6 = byte & 0x3F;
        if (p->latched_type) {
            p->vol_reg[p->latched_channel] = data6 & 0x0F;
        } else if (p->latched_channel == 3) {
            p->noise_reg = data6 & 0x07;
            p->lfsr = 0x8000;
        } else {
            p->tone_reg[p->latched_channel] =
                (uint16_t)((p->tone_reg[p->latched_channel] & 0x00F) | (data6 << 4));
        }
    }
}

static int parity16(uint16_t v) {
    v ^= v >> 8; v ^= v >> 4; v ^= v >> 2; v ^= v >> 1;
    return v & 1;
}

static void psg_tick(PSGHandle *p) {
    int ch;
    for (ch = 0; ch < 3; ch++) {
        if (p->tone_reg[ch] <= 1) {
            p->tone_output[ch] = 1; /* DC output, per spec (sample-playback trick) */
            continue;
        }
        /* Reload-then-decrement, NOT reload-or-decrement. Testing first and
         * decrementing only in the else branch spends one extra tick per
         * half-period, which makes every tone channel play at
         * clock/(32*(N+1)) instead of the clock/(32*N) that freq_to_tone_n
         * assumes -- about 7 cents flat, consistent across the register
         * range, so it reads as "the PSG is slightly out of tune with the
         * FM" rather than as a bug. */
        if (p->tone_counter[ch] <= 0) {
            p->tone_counter[ch] = p->tone_reg[ch];
            p->tone_output[ch] ^= 1;
        }
        p->tone_counter[ch]--;
    }

    int reset_val;
    switch (p->noise_reg & 0x03) {
        case 0: reset_val = 0x10; break;
        case 1: reset_val = 0x20; break;
        case 2: reset_val = 0x40; break;
        default: reset_val = p->tone_reg[2] > 0 ? p->tone_reg[2] : 1; break;
    }
    if (p->noise_counter <= 0) {
        p->noise_counter = reset_val;   /* same reload-then-decrement rule */
        uint8_t old_ff = p->noise_ff;
        p->noise_ff ^= 1;
        if (old_ff == 0 && p->noise_ff == 1) {
            int white = (p->noise_reg & 0x04) != 0;
            uint16_t fb = white ? (uint16_t)parity16(p->lfsr & 0x0009)
                                 : (uint16_t)(p->lfsr & 1);
            p->lfsr = (uint16_t)((p->lfsr >> 1) | (fb << 15));
        }
    }
    p->noise_counter--;
}

/* Render n_samples of native-rate (chip_clock/16) mono audio, int16. */
void psg_render(PSGHandle *p, int n_samples, short *out) {
    int i, ch;
    for (i = 0; i < n_samples; i++) {
        psg_tick(p);
        int32_t mix = 0;
        for (ch = 0; ch < 3; ch++) {
            int bit = p->tone_output[ch] ? 1 : -1;
            mix += bit * VOLUME_TABLE[p->vol_reg[ch]];
        }
        int nbit = (p->lfsr & 1) ? 1 : -1;
        mix += nbit * VOLUME_TABLE[p->vol_reg[3]];
        mix /= 4;
        if (mix > 32767) mix = 32767;
        if (mix < -32768) mix = -32768;
        out[i] = (short)mix;
    }
}
