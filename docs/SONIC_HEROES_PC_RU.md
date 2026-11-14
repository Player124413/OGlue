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
4. **Это автоматизация имеющегося конвейера, а не обещание готового играбельного порта.**
   В этом checkout есть runtime-правки OGlue для Heroes, но нет отдельного проекта Heroes
   с адресами D3D-устройства, fence-ожиданий и ручными override-функциями. Workflow не
   выдумывает эти адреса. Успешная компиляция и проверка PE не означают, что игра загрузилась,
   прошла меню или прошла проверку звука/графики. Статус честно записан в `BUILD-INFO.json`.

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
3. Проверка root `default.xbe`: retail, title ID `5345002B`, имя `Sonic Heroes`.
   Поддерживаются известные смещения game partition, включая несколько redump-layout.
   Образы Xbox 360/GOD, PC-версия, ZIP/RAR и debug XBE не подходят.
4. `xbe_parser → disasm → func_id → abi_analysis`, генерация per-title kernel ordinal remap.
5. `tools.recomp --all --split 250`: весь обнаруженный код переводится в C.
6. Проверки: нет failed-функций и unimplemented-инструкций, entry point присутствует в
   dispatch table. Unresolved call stubs и неизвестные kernel imports попадают в предупреждения.
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
| expects Sonic Heroes | другой title ID/имя, PC-версия или образ Xbox 360 |
| Translation is incomplete / no-ops | смотрите `05-recompile.log` и `translation-summary.json`; сборка намеренно остановлена |
| CMake/compiler error | смотрите `06-configure.log` и `07-build.log`; нужен MSVC x64 |
| Игра падает/висит после сборки | смотрите `heroes-last-run.log`, `xbox_kernel.log`, строки `[ICALL]`, `[STUB]`, `[UNIMPL]`, `[CRASH]`; вероятна недостающая title-specific интеграция |

Проверки самого workflow: [`.github/workflows/validate-recompilation.yml`](../.github/workflows/validate-recompilation.yml)
гоняет no-game тесты, actionlint и синтетический XBE через генерацию/MSVC/link/package.
Они проверяют механику CI без распространения игры и без заявления о прохождении Sonic Heroes.
