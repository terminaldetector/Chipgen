"""
sequencer.py — turns a List[Event] into audio and/or a .vgm file by
driving the YM2612 and SN76489 emulators in parallel, each at its own
native sample rate, then resampling both to a common target rate and
summing.

This module is the "player" half of the DefleMask-for-neural-networks
idea: instruments.py + events.py define what a model can say, this file
is what makes it audible.

Two things it does that are worth knowing about:

  * DAC streaming. A DACSample event cannot be applied "instantly" the way
    a note-on can — PCM is a stream of bytes at its own rate, so the
    renderer breaks the FM output into chunks and drops one DAC byte
    between them. That is exactly what a Genesis sound driver does on a
    timer interrupt.

  * VGM recording. Pass vgm_path= and the same performance also lands as
    a register log playable in any VGM player. It is not a re-render: the
    writer is the chips' logger, so the file records the bytes the
    emulator actually received.
"""


import audio as _audio
import events as events_mod
import opn2
import mixer
import samples as samples_mod
import sn76489
import instruments as instruments_mod
from mixer import DEFAULT_OPL_GAIN, DEFAULT_PSG_GAIN

DEFAULT_TICKS_PER_SECOND = 192.0

#: Which events mean "this score plays the OPL2".
_OPL_EVENTS = ("OPLNoteOn", "OPLInstrumentSelect", "OPLVolume", "OPLDepth")


def _uses_opl(event_list) -> bool:
    return any(type(e).__name__ in _OPL_EVENTS for e in event_list)



class Sequencer:
    def __init__(self, ticks_per_second: float = DEFAULT_TICKS_PER_SECOND,
                 target_rate: int = 44100, fm_gain: float = 1.0,
                 psg_gain: float = DEFAULT_PSG_GAIN,
                 opl_gain: float = DEFAULT_OPL_GAIN, pal: bool = False,
                 dc_block: bool = True,
                 chip_type: str = opn2.DEFAULT_CHIP_TYPE):
        self.ticks_per_second = ticks_per_second
        self.target_rate = target_rate
        self.fm_gain = fm_gain
        self.psg_gain = psg_gain
        self.opl_gain = opl_gain
        self.pal = pal
        #: "ym2612" (discrete, Model 1, has the DAC ladder) or "ym3438"
        #: (later ASIC, clean). See opn2.CHIP_TYPES.
        self.chip_type = chip_type
        #: The YM2612's DAC ladder pushes the mix off centre — measured at
        #: -0.027, or 4.3% of peak, on the demo — which is headroom spent
        #: on nothing audible and a click at the start and end of a render.
        #: On by default: it removes only the 0 Hz term, so the ladder's
        #: actual grit (a signal-dependent square, not a constant) survives
        #: untouched. Turn it off to compare against an unfiltered capture.
        self.dc_block = dc_block

    @property
    def fm_clock(self) -> float:
        return opn2.PAL_CHIP_CLOCK if self.pal else opn2.NTSC_CHIP_CLOCK

    @property
    def psg_clock(self) -> float:
        return sn76489.PAL_PSG_CLOCK if self.pal else sn76489.NTSC_PSG_CLOCK

    # ----------------------------------------------------------------- API
    def render(self, event_list, vgm_path: str = None, gd3=None):
        """Render to audio. Optionally record the same take as a .vgm."""
        writer = self._make_writer(gd3) if vgm_path else None
        result = self._run(event_list, writer=writer, want_audio=True)
        if writer is not None:
            writer.save(vgm_path)
        return result

    def export_vgm(self, event_list, path: str, gd3=None):
        """Write only the .vgm — no audio rendered, so this is fast and
        works even where the chips can only be poked, not listened to."""
        writer = self._make_writer(gd3)
        self._run(event_list, writer=writer, want_audio=False)
        writer.save(path)
        return writer

    def render_to_file(self, event_list, wav_path: str, vgm_path: str = None,
                       gd3=None):
        import wavio
        buf = self.render(event_list, vgm_path=vgm_path, gd3=gd3)
        wavio.write(wav_path, buf, self.target_rate)
        return buf

    def _make_writer(self, gd3):
        import vgm
        return vgm.VGMWriter(ym_clock=self.fm_clock, psg_clock=self.psg_clock,
                             gd3=gd3)

    # ------------------------------------------------------------- internals
    def _run(self, event_list, writer=None, want_audio=True):
        ym = opn2.YM2612(clock=self.fm_clock, chip_type=self.chip_type,
                         logger=writer.ym_logger if writer else None)
        psg = sn76489.SN76489(clock=self.psg_clock,
                              logger=writer.psg_logger if writer else None)
        fm_rate = ym.native_rate
        psg_rate = psg.native_rate

        # The OPL2 is built only if the score plays it. It is a pure-Python
        # core, so an unused chip that still renders silence would be the
        # most expensive thing in the run.
        opl = None
        if _uses_opl(event_list):
            import opl2 as opl2_mod
            opl = opl2_mod.YM3812(
                logger=writer.opl_logger if writer else None)
            if writer is not None:
                writer.opl_clock = int(round(opl.clock))

        state = _RenderState(ym, psg, fm_rate, writer, want_audio, opl)
        rate = float(self.ticks_per_second)
        fm_pending = psg_pending = opl_pending = 0.0
        fm_chunks, psg_chunks, opl_chunks = [], [], []
        opl_rate = opl.native_rate if opl is not None else 0.0

        for ev in event_list:
            if isinstance(ev, events_mod.Wait):
                dt = ev.ticks / rate
                fm_pending += dt * fm_rate
                psg_pending += dt * psg_rate
                opl_pending += dt * opl_rate
                continue
            if isinstance(ev, events_mod.Tempo):
                rate = max(1.0, float(ev.ticks_per_second))
                continue
            if isinstance(ev, events_mod.End):
                break

            fm_pending, psg_pending, opl_pending = self._catch_up(
                state, fm_chunks, psg_chunks, opl_chunks,
                fm_pending, psg_pending, opl_pending)

            if isinstance(ev, events_mod.LoopPoint):
                if writer is not None:
                    writer.set_loop_point()
                continue
            if isinstance(ev, events_mod.Marker):
                continue
            self._apply(ev, state)

        # Any DAC sample still playing has to finish, or the tail is cut off
        # mid-drum. Extend the trailing silence to cover it.
        tail = state.dac_remaining_seconds()
        if tail > 0:
            fm_pending += tail * fm_rate
            psg_pending += tail * psg_rate
            opl_pending += tail * opl_rate

        self._catch_up(state, fm_chunks, psg_chunks, opl_chunks,
                       fm_pending, psg_pending, opl_pending, final=True)

        ym.close()
        psg.close()
        if opl is not None:
            opl.close()

        if not want_audio:
            return None
        return self._mix(_audio.concat(fm_chunks, 2), _audio.concat(psg_chunks, 1),
                         fm_rate, psg_rate,
                         _audio.concat(opl_chunks, 1) if opl_chunks else None,
                         opl_rate)

    def _catch_up(self, state, fm_chunks, psg_chunks, opl_chunks,
                  fm_pending, psg_pending, opl_pending, final: bool = False):
        """Render the time owed since the last event, then return the
        sub-sample remainders so timing never drifts."""
        n_fm = int(round(fm_pending)) if final else int(fm_pending)
        if n_fm > 0:
            chunk = state.render_fm(n_fm)
            if chunk is not None:
                fm_chunks.append(chunk)
            fm_pending -= n_fm
        n_psg = int(round(psg_pending)) if final else int(psg_pending)
        if n_psg > 0:
            chunk = state.render_psg(n_psg)
            if chunk is not None:
                psg_chunks.append(chunk)
            psg_pending -= n_psg
        n_opl = int(round(opl_pending)) if final else int(opl_pending)
        if n_opl > 0:
            chunk = state.render_opl(n_opl)
            if chunk is not None:
                opl_chunks.append(chunk)
            opl_pending -= n_opl
        return fm_pending, psg_pending, opl_pending

    @staticmethod
    def _apply(ev, state):
        ym, psg, opl = state.ym, state.psg, state.opl
        E = events_mod
        if isinstance(ev, E.FMInstrumentSelect):
            # instruments.get() rather than BANK[...]: a typo'd or
            # not-yet-loaded patch name should say which names exist,
            # not surface as a bare KeyError from inside the renderer.
            ym.set_instrument(ev.channel,
                              instruments_mod.get(ev.instrument))
        elif isinstance(ev, E.FMNoteOn):
            ym.note_on(ev.channel, ev.note, ev.octave, velocity=ev.velocity)
        elif isinstance(ev, E.FMNoteOff):
            ym.note_off(ev.channel)
        elif isinstance(ev, E.FMPan):
            ym.set_pan(ev.channel, ev.left, ev.right, ev.ams, ev.pms)
        elif isinstance(ev, E.FMLFO):
            ym.set_lfo(ev.enable, ev.freq)
        elif isinstance(ev, E.FMVolume):
            ym.set_volume(ev.channel, ev.volume)
        elif isinstance(ev, E.FMPitch):
            ym.set_pitch_offset(ev.channel, ev.cents)
        elif isinstance(ev, E.OPLInstrumentSelect):
            if opl is not None:
                import opl_instruments
                opl.set_instrument(ev.channel, opl_instruments.get(ev.instrument))
        elif isinstance(ev, E.OPLNoteOn):
            if opl is not None:
                opl.note_on(ev.channel, ev.note, ev.octave, velocity=ev.velocity)
        elif isinstance(ev, E.OPLNoteOff):
            if opl is not None:
                opl.note_off(ev.channel)
        elif isinstance(ev, E.OPLVolume):
            if opl is not None:
                opl.set_volume(ev.channel, ev.volume)
        elif isinstance(ev, E.OPLDepth):
            if opl is not None:
                opl.tremolo_depth = ev.tremolo
                opl.vibrato_depth = ev.vibrato
        elif isinstance(ev, E.DACEnable):
            ym.set_dac_enable(ev.enable)
        elif isinstance(ev, E.DACSample):
            state.start_dac(ev)
        elif isinstance(ev, E.PSGToneOn):
            psg.tone_on(ev.channel, ev.note, ev.octave, ev.volume)
        elif isinstance(ev, E.PSGToneOff):
            psg.tone_off(ev.channel)
        elif isinstance(ev, E.PSGVolume):
            psg.set_volume(ev.channel, ev.volume)
        elif isinstance(ev, E.PSGNoiseOn):
            psg.noise_on(ev.white, ev.rate, ev.volume, restart=ev.restart)
        elif isinstance(ev, E.PSGNoiseOff):
            psg.noise_off()
        else:
            raise ValueError(f"unknown event: {ev!r}")

    # ------------------------------------------------------------------ mix
    def _mix(self, fm_audio, psg_audio, fm_rate: float, psg_rate: float,
             opl_audio=None, opl_rate: float = 0.0):
        return mixer.mix(fm_audio, psg_audio, fm_rate, psg_rate,
                         self.target_rate, fm_gain=self.fm_gain,
                         psg_gain=self.psg_gain, dc_block=self.dc_block,
                         opl_audio=opl_audio, opl_rate=opl_rate,
                         opl_gain=self.opl_gain)


class _RenderState:
    """Everything the render loop mutates: the chips plus the DAC stream."""

    def __init__(self, ym, psg, fm_rate: float, writer, want_audio: bool,
                 opl=None):
        self.ym = ym
        self.psg = psg
        self.opl = opl
        self.fm_rate = fm_rate
        self.writer = writer
        self.want_audio = want_audio
        self._dac = None

    # -- DAC ---------------------------------------------------------------
    def start_dac(self, ev):
        sample = samples_mod.KIT[ev.name]
        rate = ev.rate or sample.rate
        auto_enabled = not self.ym.dac_enabled
        if auto_enabled:
            # Convenience, deliberately: requiring DACEnable before every
            # drum would be one more thing for a model to forget, and there
            # is no case where DACSample means anything else.
            self.ym.set_dac_enable(True)
        self._dac = {
            "data": sample.data,
            "pos": 0,
            "rate": float(rate),
            "volume": max(0.0, min(1.0, ev.volume)),
            "countdown": 0.0,          # FM samples until the next byte
            "auto_enabled": auto_enabled,
        }

    def dac_remaining_seconds(self) -> float:
        if not self._dac:
            return 0.0
        left = len(self._dac["data"]) - self._dac["pos"]
        return max(0.0, left / self._dac["rate"])

    def _emit_dac_byte(self) -> bool:
        """Push one PCM byte. Returns False when the sample is finished."""
        d = self._dac
        raw = d["data"][d["pos"]]
        d["pos"] += 1
        if d["volume"] < 1.0:
            raw = int(round(128 + (raw - 128) * d["volume"]))
        self.ym.write_dac(raw)
        if d["pos"] >= len(d["data"]):
            self.ym.write_dac(128)     # park at centre so it does not click
            if d["auto_enabled"]:
                self.ym.set_dac_enable(False)
            self._dac = None
            return False
        d["countdown"] += self.fm_rate / d["rate"]
        return True

    # -- rendering ---------------------------------------------------------
    def render_fm(self, n_samples: int):
        """Render n FM samples, interleaving DAC writes at the sample's rate.

        Also the clock the VGM writer runs on: whatever time is rendered
        here is the time that passes in the register log, which keeps DAC
        bytes at the right offsets instead of all bunched at one instant.
        """
        if self._dac is None:
            self._advance_writer(n_samples)
            return self.ym.render(n_samples) if self.want_audio else None

        chunks = []
        remaining = n_samples
        while remaining > 0 and self._dac is not None:
            take = int(self._dac["countdown"])
            if take >= remaining:
                self._dac["countdown"] -= remaining
                self._advance_writer(remaining)
                if self.want_audio:
                    chunks.append(self.ym.render(remaining))
                remaining = 0
                break
            if take > 0:
                self._dac["countdown"] -= take
                self._advance_writer(take)
                if self.want_audio:
                    chunks.append(self.ym.render(take))
                remaining -= take
            self._emit_dac_byte()
        if remaining > 0:
            self._advance_writer(remaining)
            if self.want_audio:
                chunks.append(self.ym.render(remaining))
        if not self.want_audio:
            return None
        return _audio.concat(chunks, 2)

    def render_opl(self, n_samples: int):
        if self.opl is None or not self.want_audio:
            return None
        return self.opl.render(n_samples)

    def render_psg(self, n_samples: int):
        # The PSG has no equivalent of the DAC stream and the VGM clock is
        # already being advanced by the FM path, so this stays simple.
        return self.psg.render(n_samples) if self.want_audio else None

    def _advance_writer(self, fm_samples: int):
        if self.writer is not None and fm_samples > 0:
            self.writer.advance(fm_samples / self.fm_rate)
