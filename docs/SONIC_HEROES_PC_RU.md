# Sonic Heroes для ПК через GitHub Actions

Workflow: [`.github/workflows/sonic-heroes-pc.yml`](../.github/workflows/sonic-heroes-pc.yml).
Он принимает **ваш образ Original Xbox Sonic Heroes**, выполняет доступный в OGlue
конвейер рекомпиляции, собирает Windows x64 `.exe` и отдаёт два отдельных артефакта:

| Артефакт | Содержимое |
|---|---|
| `SonicHeroes-PC-Windows-x64` | `SonicHeroesPC.exe`, папка `game` из вашего ISO, лаунчер, README, checksum и build metadata |
| `SonicHeroes-PC-Recompilation-Source` | сгенерированный C, host-проект, исходники OGlue/runtime, инструкции пересборки и manifest |
| `SonicHeroes-PC-Diagnostics` | логи этапов и сводки; создаётся и при ошибке. Сам ISO и папка `game` туда не попадают |

## Сначала важное

1. **Кнопка GitHub Run workflow не умеет прикреплять локальный файл.** Поэтому в форме
   есть поле `iso_url`: прямая HTTPS-ссылка, которая отдаёт сам ISO/XISO, а не HTML-страницу
   скачивания и не форму входа. Если хотите указать именно локальный файл без загрузки,
   используйте локальный запуск ниже на Windows.
2. Workflow появится в списке **Actions → Run workflow** после попадания файла в default
   branch репозитория (обычно `main`). На ветке разработки он уже есть в коде, но GitHub
   не показывает кнопку ручного запуска для workflow, которого нет в default branch.
3. Используйте только свою копию игры. Текущий репозиторий публичный: артефакты публичного
   репозитория не стоит использовать для распространения чужих игровых данных. Для реального
   образа предпочтителен приватный репозиторий/форк. Оба основных артефакта содержат
   производный код, а игровой — ещё и оригинальные assets.
4. **Исправления для Heroes в OGlue есть** — они живут в общем runtime, а не в отдельном
   патче. Но два из них включаются только тогда, когда проект игры передаёт один
   per-title адрес (см. ниже). Успешная компиляция и проверка PE не означают, что игра
   загрузилась и прошла меню. Статус честно записан в `BUILD-INFO.json`.

## Какие исправления Heroes уже есть в runtime

Это не предположение — вот конкретные места в коде этого репозитория:

| Исправление | Где |
|---|---|
| Музыка умирает через полторы минуты: DPC звука читает устаревший «voice done» | `src/kernel/kernel_hal.c` — шлюз DISPATCH, включён по умолчанию |
| Звуковой поток засыпает навсегда (потерянное пробуждение `NtResumeThread`) | `src/kernel/kernel_thread.c` |
| Заморозка в Seaside Hill: DPC обслуживается сразу за прерыванием звукового чипа | `src/apu/apu_core.c`, `src/apu/apu_mmio_hook.c` |
| Гигантские треугольники раз в секунду (склейка кольцевого пушбуфера) | `src/kernel/nv2a_pb_scan.c`, `nv2a_pb_exec.c` |
| Меню 90 FPS и игра в 1,5× быстрее нормы → темп 60 Гц | `src/kernel/nv2a_pb_exec.c` |
| 7 кадров из 100 показывали карту теней | `src/kernel/nv2a_pb_exec.c` |
| 23 FPS на выборе команды (12 скинованных персонажей) → вершины на нескольких потоках | `src/kernel/nv2a_pb_exec.c` |
| Рваные модели из-за параллельных потоков гостя | `src/kernel/kernel_thread.c` — `GENRY_GUEST_CORE` |
| Мигание карточек сохранений: «всё нарисовано» сразу после отправки | `GENRY_HONEST_FENCE`, `src/kernel/nv2a_pb_exec.c` |
| hangs на NOW LOADING с 0 FPS: ожидание busy-block D3D | `src/kernel/kernel_bridge.c` |
| Окно показывало 24 кадра из 60 | `src/video/gpu_present.c` |
| PAL-издание не стартовало (регион консоли не совпадал) | `src/kernel/kernel_xbox.c` — регион 7, NTSC-M 60 Гц |

### Что действительно требует одного числа от вас

`xbox_Nv2aMirrorFence(device_ptr_va, put_off, get_ptr_off)` **не вызывается нигде в `src/`** —
это API для проекта игры. Пока зеркало fence не зарегистрировано:

- `xbox_Nv2aFenceWordVa()` возвращает 0 → `GENRY_HONEST_FENCE` не знает, какое слово писать
  (мигание карточек сохранений, рваные модели);
- `xbox_Nv2aBusyEventVa()` возвращает 0 → общий обработчик ожидания в `kernel_bridge.c`
  не срабатывает (тот самый NOW LOADING с 0 FPS).

Сама логика уже написана и является общей; не хватает **адреса указателя D3D-устройства
в вашем XBE**. Проект автора игры с этим адресом не опубликован (в upstream
`GenryTheFox0/OGlue` тоже нет каталога с проектом Heroes), поэтому workflow не может
взять его откуда-то — и не угадывает: неверный адрес означал бы работу с чужим словом памяти.

Раньше это требовало правки C-кода. Теперь это поле формы:

| Поле workflow / флаг CLI | Значение |
|---|---|
| `d3d_device_ptr` / `--d3d-device-ptr` | адрес **ячейки**, в которой лежит указатель на устройство; пусто = зеркало не регистрируется |
| `d3d_put_off` / `--d3d-put-off` | смещение счётчика отправок; пусто = `0x2C` (XDK 5849) |
| `d3d_get_ptr_off` / `--d3d-get-ptr-off` | смещение указателя «прочитано GPU»; пусто = `0x30` (XDK 5849) |

Смещения по умолчанию взяты из `docs/technical/d3d8ltcg-device-context.md`
(`+0x2C` — write sequence, `+0x30` — указатель на позицию чтения GPU).

### какое именно число и как его найти

Нужен **адрес ячейки**, а не адрес самого устройства. Runtime делает так:

```c
dev = *(uint32_t *)device_ptr_va;   /* device_ptr_va — то, что вы вводите */
put = dev + 0x2C;                   /* счётчик отправок */
get = *(uint32_t *)(dev + 0x30);    /* слово «прочитано GPU» */
```

Пример из `docs/technical/d3d8ltcg-device-context.md` (это **Burnout 3**, не Heroes):
устройство лежит по `0x0035D6A0`, а ячейка указателя — `0x35FB48`. Вводить нужно
`0x0035FB48`. Для вашего дампа Heroes адрес будет другим.

Поиск автоматизирован — отдельный инструмент читает ваш `default.xbe` и ранжирует
кандидатов по характерной форме стокового ожидания D3D:

```bat
python -m tools.ci.find_d3d_device game\default.xbe --json candidates.json
```

Он ищет регистр, загруженный из глобальной ячейки, у которого затем читаются оба
поля `+0x2C` и `+0x30`; дополнительное `+0x1DCC` (KEVENT ожидания) повышает оценку.
Вывод:

```
Candidates for --d3d-device-ptr (heuristic, verify by running):
  0x003xxxxx  score 3  cell in .data, from D3D:0x003xxxxx +busy KEVENT

Start with:  --d3d-device-ptr 0x003xxxxx
```

Это эвристика: инструмент только печатает кандидатов и ничего не подставляет в
сборку. Проверка — запуск: в логе должно появиться `NV2A fence mirror: device at ...`,
а игра должна пройти NOW LOADING.

Тот же поиск выполняется и в workflow (шаг «Find D3D device pointer candidates»),
его результат попадает в `SonicHeroes-PC-Diagnostics/d3d-device-candidates.json`
и в сводку запуска. Поэтому можно сначала запустить без адреса, взять кандидата
из сводки и перезапустить с ним.

В логе запуска видно, что получилось:

```
[BOOT] fence mirror registered: device ptr 0x0035FB48
  NV2A fence mirror: device at 0x0035FB48, PUT +0x2C -> *(GET +0x30)
```

или, если адрес не задан:

```
[BOOT] no D3D device pointer supplied: honest fence and the D3D busy-block wait stay dormant
```

Полезно добавить `GENRY_FENCE_TRACE=1` — раз в 2 секунды печатает, что отправлено и что
освобождено. Статус регистрации пишется в `BUILD-INFO.json` (`d3d_fence.registered`).

## Какой образ подходит: USA, Europe или Japan

Все три региональных издания Sonic Heroes для Original Xbox несут **один и тот же
title ID `5345002B`**, поэтому проверка workflow принимает любое из них. Регион
записывается в `BUILD-INFO.json` (`region`, `region_flags`).

| Издание | Бит региона в сертификате | Комментарий |
|---|---|---|
| USA (En,Ja,Fr,De,Es,It) | `0x1` — North America | NTSC/60 Гц; наиболее прямой вариант для этого runtime |
| Europe (En,Ja,Fr,De,Es,It) | `0x4` — Rest of World | PAL-издание; runtime всё равно отвечает NTSC-M 60 Гц |
| Japan | `0x2` — Japan | тот же title ID |

**USA-версию использовать можно.** Более того, runtime и так представляет себя
консолью NTSC-M 60 Гц со всеми разрешёнными регионами
(`src/kernel/kernel_xbox.c`: `XC_FACTORY_GAME_REGION = 7`, `XC_FACTORY_AV_REGION = NTSC_M | 60Hz`),
а темп кадров рассчитан на 60 Гц — так что NTSC-образ не требует никаких
дополнительных настроек.

Адреса функций и entry point берутся из **вашего** `default.xbe`, а не из
зашитого списка, поэтому смена региона не ломает генерацию кода. Побочный эффект:
EXE из одного дампа нельзя запускать с `game` из другого — host сверяет SHA-256.

Не подходят: PC-версия игры, издание для Xbox 360 (GOD/обёртка), ZIP/RAR вместо
образа, debug/devkit XBE. Redump-образ с video partition впереди поддерживается —
смещение game partition находится probing'ом.

## Запуск на GitHub

1. Откройте **Actions → Sonic Heroes — PC (ISO → EXE + source)**.
2. Нажмите **Run workflow**, выберите ветку с workflow.
3. Заполните форму:

| Поле | Значение |
|---|---|
| `iso_url` | обязательная прямая HTTPS-ссылка на ваш `.iso`/`.xiso` Original Xbox Sonic Heroes |
| `iso_sha256` | необязательный SHA-256 всего файла образа, 64 hex-символа; рекомендуется |
| `d3d_device_ptr` | необязательный адрес указателя D3D-устройства; включает фиксы fence (см. выше) |
| `d3d_put_off`, `d3d_get_ptr_off` | необязательные смещения; по умолчанию `0x2C` и `0x30` |
| `strict_translation` | выключено; включите, чтобы любая непереведённая функция останавливала сборку |
| `build_jobs` | `2` по умолчанию; `1` для меньшего расхода памяти, `4` для более быстрой сборки |
| `retention_days` | `7` по умолчанию; также `14` или `30`, с учётом лимита репозитория |

4. Дождитесь зелёного запуска. Внизу страницы run, в разделе **Artifacts**, скачайте
   **отдельно** игровой архив и архив исходников. GitHub упаковывает каждый артефакт в ZIP.
5. Распакуйте игровой архив целиком, например в `C:\Games\SonicHeroes`, и запустите
   `Launch-SonicHeroes.cmd`. Не запускайте EXE из ZIP и не отделяйте его от папки `game`.

Ссылка должна оставаться доступной, пока runner скачивает файл. Предпочтительна
кратковременная подписанная ссылка без логина. URL маскируется в логах, но значения
`workflow_dispatch` сохраняются GitHub в метаданных запуска: не используйте долгоживущие
токены в query-строке и отзывайте временную ссылку после скачивания. URL с логином/паролем
запрещён. Редиректы проверяются заново; приватные/loopback-адреса не принимаются.

## Что выполняется автоматически

1. Потоковое скачивание с лимитом размера/времени и проверкой SHA-256, если он задан.
2. Чтение XDVDFS встроенным `tools.xiso`, проверка путей и извлечение всего дерева игры.
3. Проверка root `default.xbe`: retail и title ID `5345002B`. Имя в сертификате
   проверяется мягко: на розничном диске оно записано как `SONICHEROES` (без пробела),
   в других изданиях может отличаться — при совпавшем title ID это лишь предупреждение.
   Поддерживаются известные смещения game partition, включая несколько redump-layout.
   Образы Xbox 360/GOD, PC-версия, ZIP/RAR и debug XBE не подходят.
4. `xbe_parser → disasm → func_id → abi_analysis`, генерация per-title kernel ordinal remap.
5. `tools.recomp --all --split 250`: весь обнаруженный код переводится в C.
6. Проверки: entry point присутствует в dispatch table и переведена хотя бы одна функция —
   это жёсткие условия. Непереведённые функции, no-op инструкции, unresolved-заглушки и
   неизвестные kernel imports по умолчанию попадают в **предупреждения** (видны в сводке
   запуска и в `BUILD-INFO.json`), потому что у крупной игры они почти всегда есть.
   Флаг `strict_translation` возвращает строгое поведение: любая такая проблема
   останавливает сборку.
7. CMake + Visual Studio 2022, Release x64, статический CRT. Проверка PE32+/x64.
8. Раздельная упаковка игры и исходников. Исходный артефакт не содержит `.xbe`, ISO,
   текстуры, аудио или видео; игровой артефакт содержит данные, необходимые runtime.

ISO не публикуется как артефакт и не коммитится. Логи лежат только в приватной рабочей
папке runner и в diagnostics-артефакте; они могут содержать фрагменты производного кода,
поэтому их тоже не следует публиковать без права на распространение.

## Локальный запуск с локальным ISO

На Windows x64 с Visual Studio 2022 (C++ workload), CMake 3.20+ и Python 3.10+:

```bat
python -m pip install -r tools/ci/requirements.txt
python -m tools.ci.heroes_build all --iso C:\Images\SonicHeroes.iso --work-dir .work\heroes-local --jobs 2
```

Результат:

- `.work\heroes-local\artifacts\game` — готовая структура игрового артефакта;
- `.work\heroes-local\project` — исходники рекомпиляции;
- `.work\heroes-local\build\Release\SonicHeroesPC.exe` — собранный EXE;
- `.work\heroes-local\diagnostics` — логи.

Каждый запуск использует новую рабочую папку. Команда не перезаписывает предыдущий проект.
Можно запускать этапы отдельно: `prepare --iso ... --work-dir ...`, затем
`build --work-dir ... --jobs 2`.

## Пересборка из артефакта исходников

Распакуйте `SonicHeroes-PC-Recompilation-Source`. Для повторной компиляции уже
сгенерированного C Python не нужен:

```bat
cmake -S . -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release --target SonicHeroesPC --parallel 2
```

Для новой генерации из того же ISO используйте команды из `README.md` внутри артефакта.
`BUILD-INFO.json` хранит SHA-256 XBE, entry point, XDK builds, статистику трансляции,
commit OGlue и признаки проверки. `SOURCE-MANIFEST.json` хранит хеши исходных файлов.
Host сверяет SHA-256 `game\default.xbe`, чтобы не запустить код с данными другого дампа.

## Диагностика

| Симптом | Что проверить |
|---|---|
| HTTP 403/404 | ссылка истекла, требует вход или отдаёт страницу вместо файла |
| SHA-256 mismatch | образ отличается от ожидаемого; проверьте дамп и хеш всего ISO |
| not an XDVDFS image | это не поддерживаемый Xbox ISO/XISO либо неизвестный layout |
| expects Sonic Heroes | другой title ID, PC-версия или образ Xbox 360. Само имя в сертификате роли не играет |
| Strict translation gate | включён `strict_translation`; смотрите `translation-summary.json`, чтобы решить, критичны ли потери |
| CMake/compiler error | смотрите `06-configure.log` и `07-build.log`; нужен MSVC x64 |
| Игра падает/висит после сборки | смотрите `heroes-last-run.log`, `xbox_kernel.log`, строки `[ICALL]`, `[STUB]`, `[UNIMPL]`, `[CRASH]` |
| Висит на NOW LOADING, 0 FPS | в логе нет `fence mirror registered` — задайте `d3d_device_ptr` и пересоберите |
| Мигают карточки сохранений, рвутся модели | то же: без зарегистрированного зеркала `GENRY_HONEST_FENCE` не работает |

Проверки самого workflow: [`.github/workflows/validate-recompilation.yml`](../.github/workflows/validate-recompilation.yml)
гоняет no-game тесты, actionlint и синтетический XBE через генерацию/MSVC/link/package.
Они проверяют механику CI без распространения игры и без заявления о прохождении Sonic Heroes.
