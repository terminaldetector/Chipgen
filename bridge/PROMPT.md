# The prompt to send with the zip

Upload `chipgen-bridge.zip` to any chat model that can run Python
(ChatGPT with the code interpreter, Claude with a code sandbox, a local
model with a tool runner) and paste one of these alongside it. Nothing is
deployed, nothing is installed — the archive carries its own engine.

---

## Short version

> This zip is a Sega Genesis chiptune engine. Unzip it, run
> `python3 bridge/bootstrap.py`, read `START_HERE.md`, then compose me a
> track in its tracker notation and render it to WAV and VGM.

That is genuinely enough. `bootstrap.py` sets everything up and reports
what it found, and `START_HERE.md` is written for a model to read.

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
>    `python3 python/chipgen.py song.trk -o song.wav --vgm song.vgm`
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
>    `python3 python/chipgen.py song.trk -o song.wav --vgm song.vgm`
>    и отдай оба файла.
>
> Пиши партитуру сам, а не генерируй случайно — смысл в том, что ты
> сочиняешь напрямую в регистры чипа.

---

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
> Отрендери: `python3 python/chipgen.py song.trk -o song.wav --vgm
> song.vgm`. Команда сама печатает предупреждения, если партитура
> нарушает пункты 1–4 (`warning: ...` в выводе) — если что-то напечаталось,
> перепиши партитуру и отрендери заново, пока вывод не станет чистым.
> Пришли `song.trk`, `song.wav`, `song.vgm` и **сам вывод команды рендера
> целиком**, чтобы было видно, что предупреждений нет.

If warnings still show up in the output the model pastes back, the track
was not actually fixed — do not accept "close enough," ask for another
pass. If the output is clean, the score exercised the channel budget the
engine actually has instead of leaning on one continuous channel for
the whole runtime.

---

## What you get back

- **`song.wav`** — plays anywhere.
- **`song.vgm`** — a few kilobytes, plays in any VGM player, and **imports
  into DefleMask and Furnace**. That is the part worth caring about: what
  the model wrote opens in a real tracker and stays editable by hand.
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
