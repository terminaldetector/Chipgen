# Обучающее ядро — core

159 треков, 24 саундтреков, 193061 нот. Genesis 85, NES 74.

Это не «лучшие» треки и не «уникальные» — отобранные по вкладу. Каждый несёт паттерны, которых ещё нет в наборе. Остальное лежит в **корпусе докачки**, ветка `claude/corpus-dokachka`.

## Как отбирали

`python/corpus_audit.py`. Три измеренных факта решили форму разбиения:

1. **Дубликатов в корпусе нет.** Проверено пятью наборами признаков — ритмические ячейки, контуры, наборы интервалов, аккордовые последовательности и их сочетания. Медианное сходство Jaccard между любыми двумя треками **0.000**, максимум по тысячам пар — **0.235**. Даже трек и его собственный ремикс не пересекаются настолько, чтобы один назвать лишним. Поэтому деление не «уникальное против повторов»: кучи повторов не существует.
2. **Вклад распределён очень неравномерно.** В корпусе 32 169 различных токенов. Половину несут первые 63 трека; последние 27.9% требуют 287. На колене кривой трек ядра приносит 135 новых токенов, трек докачки — 40.
3. **Один жадный отбор перекашивает набор.** Без балансировки Thunder Force IV, Gunstar Heroes и Alien Soldier занимали 39% ядра, Ninja Gaiden получал один трек, два саундтрека — ноль. Отбор идёт по кругу между саундтреками, забирая у каждого лучший оставшийся.

## Состав

| саундтрек | треков |
|---|---|
| alien soldier | 8 |
| batman | 7 |
| batman returns | 7 |
| blaster master | 7 |
| bucky o hare | 7 |
| contra | 7 |
| dragons fury | 7 |
| elemental master | 7 |
| final fantasy | 7 |
| gleylancer | 7 |
| gradius ii | 7 |
| granada | 7 |
| gunstar heroes | 7 |
| metal gear | 7 |
| ninja gaiden | 7 |
| red zone | 7 |
| shining force ii | 7 |
| streets of rage 2 bare knuckle ii | 7 |
| streets of rage bare knuckle | 7 |
| sub terrania | 7 |
| teenage mutant ninja turtles | 7 |
| thunder force iv lightening force | 7 |
| ghostbusters ii | 3 |
| robocop 3 | 1 |

## Измерено

```
style profile — 159 tracks

tempo      72 .. 195 BPM, median 100
mode       minor 105, major 54
voices     harmony 328, counter 161, bass 141, lead 118, pad 30, arp 28

harmony    unison 21%, 5 13%, maj 10%, min 10%, sus2 8%, maj7 7%
           91% played as chords, the rest spelled out

progressions repeated within a track and seen in several:
           i - VII - i - VII                x10
           VII - i - VII - i                x8
           iv5 - i - i - i                  x8
           IVadd9 - I - IVadd9 - I          x7
           i - i - i - iv5                  x6

what transfers between tracks (share of all pattern instances):
           rhythm alone            29.6%
           contour alone           45.6%
           the two fused           4.8%

--- bass ---
  rhythm   xxxxxxxxxxxxxxxx   x323   29 tracks   sync 0.75
  rhythm   xxxxxxxx--------   x760   25 tracks   sync 0.75
  rhythm   x-x-x-x-           x94     7 tracks   sync 0.00
  rhythm   x-x-x-x-x-x-x-x-x-x-x-x-x-x-x-x- x25     7 tracks   sync 0.75
  contour  +-+-+-+-+-+-+-+    x79    14 tracks
  contour  +-+-+-+            x58     9 tracks
  contour  +-+-               x23     8 tracks

--- lead ---
  rhythm   xxxxxxxx           x432   21 tracks   sync 0.50
  rhythm   xxxxxxxxxxxxxxxx   x75    16 tracks   sync 0.75
  rhythm   -xxxxxxx           x20     8 tracks   sync 0.57
  rhythm   xxxx------------   x10     8 tracks   sync 0.75
  contour  +-+-               x10     9 tracks
  contour  -++-               x10     9 tracks
  contour  ++-++              x11     8 tracks

--- harmony ---
  rhythm   xxxxxxxxxxxxxxxx   x228   32 tracks   sync 0.75
  rhythm   xxxxxxxx--------   x1189  30 tracks   sync 0.75
  rhythm   xxxxxxx---------   x86    12 tracks   sync 0.71
  rhythm   x-x-x-x------------------------- x112   11 tracks   sync 0.75
  contour  -++-               x20    15 tracks
  contour  --+-               x26    14 tracks
  contour  +---               x26    14 tracks

--- arp ---
  rhythm   xxxxxxxxxxxxxxxx   x163    9 tracks   sync 0.75
  contour  +-+-+-+-+-+-+-+    x87     8 tracks
  contour  +-+-+-+            x27     5 tracks
  contour  +-+-+-+-           x9      4 tracks

--- counter ---
  rhythm   xxxxxxxx--------   x691   18 tracks   sync 0.75
  rhythm   xxxxxxxxxxxxxxxx   x81    18 tracks   sync 0.75
  rhythm   xxxxxxx------------------------- x19     9 tracks   sync 0.86
  rhythm   x-x-x-x------------------------- x34     8 tracks   sync 0.75
  contour  +-+-               x36    20 tracks
  contour  -+-+               x26    15 tracks
  contour  -+++               x22    14 tracks

--- pad ---
  contour  ++-+               x9      4 tracks
  contour  -++-               x4      4 tracks
```
