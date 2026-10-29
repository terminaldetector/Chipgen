# Обучающее ядро — core

257 треков, 38 саундтреков, 316159 нот. Genesis 183, NES 74.

Это не «лучшие» треки и не «уникальные» — отобранные по вкладу. Каждый несёт паттерны, которых ещё нет в наборе. Остальное — в **корпусе докачки**, ветка `claude/corpus-dokachka`.

## Как отбирали

`python/corpus_audit.py`. Три измеренных факта решили форму разбиения:

1. **Дубликатов в корпусе нет.** Проверено пятью наборами признаков — ритмические ячейки, контуры, наборы интервалов, аккордовые последовательности и их сочетания. Медианное сходство Jaccard между любыми двумя треками **0.000**, максимум по тысячам пар — **0.235**. Даже трек и его собственный ремикс не пересекаются настолько, чтобы один назвать лишним. Поэтому деление не «уникальное против повторов»: кучи повторов не существует.
2. **Вклад распределён очень неравномерно.** В корпусе 52 314 различных токенов на 773 трека. Трек ядра приносит в среднем 135 новых токенов, трек докачки — 34.
3. **Один жадный отбор перекашивает набор.** Без балансировки самые длинные и плотные саундтреки съедают ядро, а другие получают ноль треков. Отбор идёт по кругу между саундтреками, забирая у каждого лучший оставшийся: 36 саундтреков из 38 представлены семью треками, два коротких — всем, что у них есть.

## Состав

| саундтрек | треков |
|---|---|
| alien soldier | 8 |
| batman | 7 |
| batman returns | 7 |
| battle mania daiginjou | 7 |
| battletoads | 7 |
| blaster master | 7 |
| bucky o hare | 7 |
| castlevania bloodlines castlevania the new generation vampire killer | 7 |
| comix zone | 7 |
| contra | 7 |
| crusader of centy soleil shin souseiki ragnacenty | 7 |
| dragons fury | 7 |
| dune the battle for arrakis | 7 |
| elemental master | 7 |
| final fantasy | 7 |
| gleylancer | 7 |
| gradius ii | 7 |
| granada | 7 |
| gunstar heroes | 7 |
| mega turrican | 7 |
| metal gear | 7 |
| ninja gaiden | 7 |
| phantasy star iv | 7 |
| red zone | 7 |
| road rash ii | 7 |
| shining force ii | 7 |
| sonic the hedgehog 2 | 7 |
| sonic the hedgehog 3 | 7 |
| sparkster | 7 |
| streets of rage 2 bare knuckle ii | 7 |
| streets of rage bare knuckle | 7 |
| sub terrania | 7 |
| teenage mutant ninja turtles | 7 |
| thunder force iv lightening force | 7 |
| vectorman | 7 |
| warsong langrisser | 7 |
| ghostbusters ii | 3 |
| robocop 3 | 1 |

## Измерено

```
style profile — 257 tracks

tempo      70 .. 200 BPM, median 112
mode       minor 162, major 95
voices     harmony 573, counter 272, bass 258, lead 192, pad 66, arp 41

harmony    unison 20%, 5 13%, maj 11%, min 11%, sus2 8%, maj7 7%
           91% played as chords, the rest spelled out

progressions repeated within a track and seen in several:
           i - VII - i - VII                x10
           i - i - VIIsus2 - VIIsus2        x10
           VII - i - VII - i                x8
           iv5 - i - i - i                  x8
           iv5 - iv5 - i - i                x7

what transfers between tracks (share of all pattern instances):
           rhythm alone            32.5%
           contour alone           50.1%
           the two fused           3.5%

--- bass ---
  rhythm   xxxxxxxx           x1450  50 tracks   sync 0.50
  rhythm   xxxxxxxxxxxxxxxx   x564   42 tracks   sync 0.75
  rhythm   xx-xxxxx--------   x37    19 tracks   sync 0.71
  rhythm   xxxx-xxx           x58    15 tracks   sync 0.57
  contour  +-+-+-+-+-+-+-+    x102   15 tracks
  contour  +-+-+-+            x99    14 tracks
  contour  +-+-               x26    12 tracks

--- lead ---
  rhythm   xxxxxxxx           x454   27 tracks   sync 0.50
  rhythm   xxxxxxxxxxxxxxxx   x126   25 tracks   sync 0.75
  rhythm   -xxxxxxx           x18     9 tracks   sync 0.57
  rhythm   xxxx----           x10     9 tracks   sync 0.50
  contour  +-+-               x21    13 tracks
  contour  -++-               x13    12 tracks
  contour  +-+-+-+            x46    11 tracks

--- harmony ---
  rhythm   xxxxxxxx--------   x1670  58 tracks   sync 0.75
  rhythm   xxxxxxxxxxxxxxxx   x503   50 tracks   sync 0.75
  rhythm   -xxxxxxx--------   x58    26 tracks   sync 0.86
  rhythm   xxxxxxx-           x106   23 tracks   sync 0.43
  contour  +-+-               x67    32 tracks
  contour  --+-               x53    32 tracks
  contour  ++--               x63    31 tracks

--- arp ---
  rhythm   xxxxxxxxxxxxxxxx   x202   10 tracks   sync 0.75
  contour  +-+-+-+-+-+-+-+    x124    9 tracks
  contour  +-+-+-+            x28     6 tracks
  contour  +-+-+-+-           x14     6 tracks

--- counter ---
  rhythm   xxxxxxxx           x819   37 tracks   sync 0.50
  rhythm   xxxxxxxxxxxxxxxx   x182   27 tracks   sync 0.75
  rhythm   xxxxxxx-           x51    21 tracks   sync 0.43
  rhythm   -xxxxxxx           x32    14 tracks   sync 0.57
  contour  +-+-               x54    30 tracks
  contour  -+-+               x33    22 tracks
  contour  --+-               x31    21 tracks

--- pad ---
  contour  -++-               x18     7 tracks
  contour  ++-+               x32     6 tracks
  contour  +-+-               x15     5 tracks
```
