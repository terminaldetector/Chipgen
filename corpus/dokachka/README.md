# Корпус докачки

516 треков, 36 саундтреков, 262665 нот. Genesis 432, NES 84.

Вторая половина обучающего корпуса. Живёт на отдельной ветке, чтобы не лежать весом в основном репозитории — докачивается по необходимости.

## Что это и чем отличается от ядра

Не «худшие» треки и не брак. Разбиение сделано по **предельному вкладу**: сколько трек приносит паттернов, которых ещё нет в наборе. Трек ядра приносит в среднем 135 новых токенов, трек докачки — 34.

Важное, что показал аудит и что стоит знать, прежде чем что-то отсюда выбрасывать: **дубликатов в корпусе нет вообще**. Проверено пятью наборами признаков; медианное сходство Jaccard между любыми двумя из 773 треков 0.000, максимум по тысячам пар 0.235. Здесь нет ни одного трека, который просто повторяет то, что уже есть в ядре. Разница только в плотности нового материала на трек.

Ядра хватает, чтобы модель усвоила идиому — 66.5% всего словаря паттернов. Докачка нужна за хвостом: редкие ходы, нетипичные прогрессии, то, что встречается в корпусе один раз.

## Как подключить

```bash
git fetch origin claude/corpus-dokachka
git checkout origin/claude/corpus-dokachka -- corpus/dokachka
```

После этого `score_model.corpus_paths()` подхватит их вместе с ядром — обход идёт по всему `corpus/` рекурсивно, отдельной настройки не нужно.

## Состав

| саундтрек | треков |
|---|---|
| phantasy star iv | 41 |
| thunder force iv lightening force | 36 |
| sonic the hedgehog 3 | 29 |
| battle mania daiginjou | 27 |
| shining force ii | 27 |
| sonic the hedgehog 2 | 24 |
| castlevania bloodlines castlevania the new generation vampire killer | 19 |
| crusader of centy soleil shin souseiki ragnacenty | 19 |
| gunstar heroes | 18 |
| ninja gaiden | 18 |
| comix zone | 17 |
| sparkster | 17 |
| streets of rage 2 bare knuckle ii | 17 |
| elemental master | 16 |
| alien soldier | 15 |
| gleylancer | 15 |
| dragons fury | 14 |
| dune the battle for arrakis | 14 |
| mega turrican | 14 |
| batman returns | 13 |
| battletoads | 11 |
| bucky o hare | 11 |
| final fantasy | 11 |
| granada | 9 |
| streets of rage bare knuckle | 9 |
| vectorman | 8 |
| warsong langrisser | 8 |
| teenage mutant ninja turtles | 7 |
| batman | 6 |
| blaster master | 6 |
| contra | 6 |
| sub terrania | 6 |
| gradius ii | 3 |
| metal gear | 3 |
| red zone | 1 |
| road rash ii | 1 |

Вклад на трек: медиана 24, минимум 0, максимум 242 новых токенов.
