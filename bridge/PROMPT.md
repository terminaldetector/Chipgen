# The prompt to send with the zip

Upload `chipgen-bridge.zip` to any chat model that can run Python
(ChatGPT with the code interpreter, Claude with a code sandbox, a local
model with a tool runner) and paste one of these alongside it. Nothing is
deployed, nothing is installed — the archive carries its own engine.

**Installing chipgen as a plugin instead of a zip?** The prompt to give
the model is `bridge/CORE.md`, not this file. This file is the *task*;
`CORE.md` is what the model needs to know about the hardware before any
task makes sense. A session that has read only the notation writes scores
that render successfully and sound wrong.

---

## Short version — works for every model family

> This zip is chipgen, a chiptune engine. Unzip it, run
> `python3 bridge/bootstrap.py`, then
> `python3 python/chipgen.py --brief --chip-target YM2612 --family <you>`
> and follow that briefing. Compose me a track, write it to `work/`,
> check it with `--check` until it says PLAYABLE, and render it with
> `-o work/song.mp3` — hand me the MP3, not a WAV. Do not edit any file
> outside `work/`.

`--chip-target` is YM2612 (Genesis), RP2A03 (NES) or YM3812 (OPL2);
`--family` is your model family (gpt, claude, grok, gemini, llama,
qwen...) and picks how the briefing is pitched. The briefing is a few
hundred tokens and complete; `AGENTS.md`, which agent tools read on
their own, says the same thing.

**Why not just "read START_HERE.md".** That was the short version until
models that skim started following it: START_HERE.md and CORE.md are
about 12,000 tokens, and a model that keeps only the gist writes
`C-5:15` on a PSG channel (silence: it is an attenuator) or an FM column
with no instrument (silence, again) — and, handed an archive full of
Python, some start improving the engine instead of writing the score.

**No archive, just a chat?** `python3 python/chipgen.py --brief
--chip-target RP2A03 --family gpt --describe "castle theme" --bars 8`
prints a briefing to paste — notation, lists and rules included — and
`--check` reads the reply you get back.

---

## Longer version, when you want to steer the music

> Attached is chipgen, a chiptune engine that drives real Sega Genesis
> sound chip emulation (YM2612 FM + SN76489 PSG).
>
> 1. Unzip it and run `python3 bridge/bootstrap.py`. It builds the chip
>    cores and self-tests; no network or pip needed.
> 2. Read `START_HERE.md` for the tracker notation.
> 3. Compose an original 8-bar piece: **<key, tempo, mood, reference
>    tracks>**. Use the FM channels for bass, lead and pads, the PSG for
>    arpeggios and hats, and the DAC for drums.
> 4. Render it with
>    `python3 python/chipgen.py song.trk -o song.mp3 --vgm song.vgz`
>    and give me both files.
>
> Write the score yourself rather than generating it randomly — the point
> is that you are composing directly into the chip's registers.

---

## Русская версия

> Во вложении chipgen — движок чиптюна поверх настоящей эмуляции звуковых
> чипов Sega Genesis (YM2612 FM + SN76489 PSG).
>
> 1. Распакуй архив и выполни `python3 bridge/bootstrap.py`. Он соберёт
>    ядра чипов и проверит себя; сеть и pip не нужны.
> 2. Прочитай `START_HERE.md` — там нотация трекера целиком.
> 3. Сочини оригинальный отрывок на 8 тактов: **<тональность, темп,
>    настроение, ориентиры>**. FM-каналы — бас, лид и пэды; PSG —
>    арпеджио и хэты; DAC — барабаны.
> 4. Отрендери:
>    `python3 python/chipgen.py song.trk -o song.mp3 --vgm song.vgz`
>    и отдай оба файла.
>
> Пиши партитуру сам, а не генерируй случайно — смысл в том, что ты
> сочиняешь напрямую в регистры чипа.

---

---

## The demo request

This is the one to use when you want to *show someone what chipgen is*.
It is not the shortest prompt — it is the one whose output proves the
model understood the machine rather than pattern-matched "chiptune".

Two rules make it a demo rather than a generation: it asks for structure
the engine can measure, and it asks the model to hand back the
measurements. A track with real dynamic structure is the one thing you
cannot fake past `--profile`.

> Во вложении chipgen — движок чиптюна поверх настоящей эмуляции YM2612 +
> SN76489 (Sega Mega Drive). Не «звук под чиптюн», а те же регистры и тот
> же протокол записи; `.vgm` на выходе открывается в DefleMask и Furnace.
>
> 1. Распакуй, выполни `python3 bridge/bootstrap.py`, дождись `READY`.
> 2. Прочитай `bridge/CORE.md` — это про сам чип, и там есть вещи,
>    которые противоречат общим знаниям об FM-синтезе. Потом
>    `START_HERE.md` — это нотация.
> 3. Прочитай `corpus/STUDY.md`. Это 79 реальных треков с Mega Drive,
>    измеренных. Пиши в этой идиоме, а не в «чиптюн вообще».
>
> Сочини **60–90 секунд** оригинальной музыки: **<стиль, тональность,
> темп, ориентиры>**. Требования:
>
> - Настоящая структура, минимум четыре секции, размеченные `mark`
>   (например intro / main / breakdown / outro). Брейкдаун должен
>   реально проваливаться по плотности, а не просто менять ноты.
> - Задействуй чип целиком: несколько FM-каналов, больше одного
>   PSG-тона, шум и DAC. Помни, что DAC забирает шестой FM-канал —
>   планируй аранжировку с этим, а не вопреки.
> - Динамика: не все ноты на 127. В корпусе медианная velocity — 21 из
>   127. Вибрато порядка 30–40 центов, а не полтона.
> - Стерео: разведи FM-каналы, не держи всё по центру.
>
> Отрендери и измерь:
>
> ```
> python3 python/chipgen.py demo.trk -o demo.mp3 --vgm demo.vgz --profile
> ```
>
> Пришли: `demo.trk`, `demo.mp3`, `demo.vgz`, **полный вывод команды** —
> и предупреждения, и таблицу `--profile` по секциям.
>
> Приёмка, проверь сам до того, как отдашь:
> - в выводе нет ни одной строки `warning:`;
> - в таблице `--profile` брейкдаун заметно тише main-секции. Если
>   разница в пределах пары процентов — это не брейкдаун, перепиши
>   аранжировку и отрендери заново;
> - `demo.vgz` существует и весит килобайты, а не байты.
>
> Если что-то не сошлось — скажи, что именно, и покажи вывод. Не отдавай
> «примерно нормально».

### English

> Attached is chipgen, a chiptune engine driving real YM2612 + SN76489
> emulation (Sega Mega Drive). Not chiptune-flavoured synthesis: the same
> registers, the same write protocol; the `.vgm` it emits opens in
> DefleMask and Furnace.
>
> 1. Unzip, run `python3 bridge/bootstrap.py`, wait for `READY`.
> 2. Read `bridge/CORE.md` — it is about the chip itself, and several
>    items in it contradict general knowledge about FM synthesis. Then
>    `START_HERE.md` for the notation.
> 3. Read `corpus/STUDY.md`: 79 real Mega Drive tracks, measured. Write
>    in that idiom rather than in "chiptune" generally.
>
> Compose **60–90 seconds** of original music: **<style, key, tempo,
> references>**. Requirements:
>
> - Real structure, at least four sections delimited with `mark`
>   (intro / main / breakdown / outro). The breakdown must actually drop
>   in density, not just change notes.
> - Use the whole chip: several FM channels, more than one PSG tone,
>   noise and DAC. Remember the DAC takes FM channel 6 — arrange around
>   that rather than against it.
> - Dynamics: not every note at 127. Median velocity in the corpus is 21
>   of 127. Vibrato around 30–40 cents, not a semitone.
> - Stereo: spread the FM channels, do not leave everything centred.
>
> Render and measure:
>
> ```
> python3 python/chipgen.py demo.trk -o demo.mp3 --vgm demo.vgz --profile
> ```
>
> Send back `demo.trk`, `demo.mp3`, `demo.vgz` and **the complete command
> output** — both the warnings and the per-section `--profile` table.
>
> Acceptance, check it yourself before handing it over:
> - no `warning:` line in the output;
> - in the `--profile` table the breakdown is clearly quieter than the
>   main section. A couple of percent apart is not a breakdown — rewrite
>   the arrangement and render again;
> - `demo.vgz` exists and is kilobytes, not bytes.
>
> If something does not line up, say what and show the output. Do not
> hand over "close enough".


---

## Acceptance-test prompt — use this to check a bridge session behaves

The three prompts above ask for a track. This one asks for a track AND
checks it against the exact mistakes a real session made once: gating the
PSG noise channel on and never releasing it for 60 seconds straight,
enabling the DAC once and streaming samples back to back with no gap for
57 of those seconds, and never touching FMPan so the whole mix collapsed
to mono. None of that clipped, none of it desynced — it only showed up as
elevated spectral flatness, and it took replaying the accompanying VGM
through this engine's own emulator to confirm the render was honest and
the problem was in the score, not the chip. `sanity.py` now runs this
check automatically on every `compose()` call, so a session that reads
its own output has everything it needs to catch this itself before
handing a file back.

Use this prompt to test a fresh session (a new chipgen version, a
different model, a different sandbox) or whenever you want a track that
actually exercises the engine instead of leaning on one channel:

> Во вложении chipgen — движок чиптюна на эмуляции YM2612+SN76489.
> Распакуй, `python3 bridge/bootstrap.py`, прочитай `START_HERE.md`.
>
> Сочини трек 30–60 секунд, **<стиль/тональность/темп>**. Требования,
> которые движок проверяет сам и напишет предупреждение, если что-то
> нарушено:
>
> 1. Канал шума (`noise` / `PSGNoiseOn`) гейтится короткими всплесками,
>    как настоящие хэты — не держи его включённым больше нескольких
>    тактов подряд.
> 2. Между сэмплами DAC (`kick`, `snare`, `hat`...) должны быть реальные
>    паузы — не гони их вплотную одно за другим весь трек, дай каждому
>    доиграть.
> 3. Хотя бы часть FM-каналов разведи по стерео (`pan fmN L`/`R`), не
>    держи всё по центру.
> 4. Используй больше одного FM-канала и больше одного PSG-тон-канала —
>    не оставляй половину чипа простаивать.
>
> Отрендери: `python3 python/chipgen.py song.trk -o song.mp3 --vgm
> song.vgz`. Команда сама печатает предупреждения, если партитура
> нарушает пункты 1–4 (`warning: ...` в выводе) — если что-то напечаталось,
> перепиши партитуру и отрендери заново, пока вывод не станет чистым.
> Пришли `song.trk`, `song.mp3`, `song.vgz` и **сам вывод команды рендера
> целиком**, чтобы было видно, что предупреждений нет.

If warnings still show up in the output the model pastes back, the track
was not actually fixed — do not accept "close enough," ask for another
pass. If the output is clean, the score exercised the channel budget the
engine actually has instead of leaning on one continuous channel for
the whole runtime.

---

## What you get back

- **`song.mp3`** — plays anywhere, about a tenth of the size of a WAV
  (a WAV is ten megabytes a minute; handing one back from a sandbox is
  often the most expensive thing in the exchange). `-o song.wav` still
  works when the full-resolution file is wanted.
- **`song.vgz`** — the register log, gzipped. VGM players read .vgz,
  and unpacked (`gzip -dc song.vgz > song.vgm`) it **imports into
  DefleMask and Furnace**. That is the part worth caring about: what the
  model wrote opens in a real tracker and stays editable by hand. Gzipped
  because sample playback is a register write per sample: for 28 seconds
  the plain VGM is 5 KB on OPL2 but 786 KB on Genesis with DAC drums and
  1.5 MB on NES with DMC drums — 1, 13 and 134 KB as .vgz.
- **`song.trk`** — the score itself, readable and diffable.

## Why this works without deploying anything

The archive is self-contained. The chip cores are C source that
`bootstrap.py` compiles on the spot (sandboxes with a code interpreter
have a compiler); if there is no compiler it uses the pure-Python cores in
`python/fallback/`. numpy and scipy are used when installed and are not
required. Nothing reaches the network at any point.

So the plugin path and the zip path are the same engine reached two ways:
register chipgen as a generator backend in a long-running system, or throw
the zip at a chat model for one track. Same events, same emulation, same
output.
