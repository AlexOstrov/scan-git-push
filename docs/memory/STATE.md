# Текущее состояние проекта

## Текущая задача
Доработка 2026-11-06 по статистике `SCAN.INCLUDE` завершена: добавлена таблица `include_stat` (схема `scan.db` v4), синхронизация шаблонов при `scan` для всех проектов, пересчёт файлов/маркеров после reconciliation и сводная таблица в конце `report-from-db --req` (+ `include_stats` в JSON); коммит `34ce176`. Предыдущая доработка 2026-11-06 — коммиты `ef53b19` и `995a04f`. Не закрыт только пункт производительности на репозитории > 10 000 коммитов.

## Реализованы
- Вложенная конфигурация `SCAN` / `ANALYSIS` / `LLM` (`config.py`), `extra="forbid"`, deprecated-апгрейд плоского конфига.
- Git-слой без PyGithub: `git/url.py`, `git/runner.py`, `git/mirror.py`, `git/rev.py`, `git/models.py`, `git/errors.py`.
- Bare-зеркала `<repo>-<sha256(url_key)[:10]>.git`; `MIRROR_FILTER=auto` → `--filter=blob:none` для remote.
- Коммиты по committer date (`%cI`), все ветки (`--branches`), дедупликация SHA, `--no-merges`.
- `analysis.db` версии 1: `schema_version`, `git_project`, `analyzed_commits`, `commit_files`; каскады, 4 PRAGMA, миграция legacy с `.bak`, код `12`.
- `analysis/collector.py`, `filters.py`, `chunker.py`, `linker.py`, `results.py`.
- Chunking по файлам и хункам, `MAX_PATCH_CHARS_PER_FILE`, `NO_PATCH_SUMMARY`.
- `ANALYSIS.EXCLUDE` по умолчанию: lock-файлы, `*.min.js/css`, `*.map`, `dist/`, `build/`, `vendor/`, `node_modules/`, `.git/`.
- LLM: только `ollama` и `mistral`; `llm/registry.py` (discover, cache, resolve/build), `--model`, `--non-interactive`, `--reanalyze`.
- CLI: `main`/`analyze`/`report-from-db`/`scan`, `--project`, `--days`, `--json`, коды `0/1/2/3/4/5/8/9/10/11/12`.
- Мультипроектные отчёты: на проект + `_summary_{date}` (MD/HTML), автономный HTML, autoescape. Имя проектного отчёта — `{repo}-{sha256(url_key)[:6]}_{date}`, поэтому одноимённые проекты не перезаписывают файлы.
- `report-from-db --req`: автономный HTML «Отчет по требованиям и разделам» из `scan.db` (требование → раздел → вхождения), `--project` и `--json`; в конце — сводная статистика по проектам и шаблонам `SCAN.INCLUDE`; `analysis.db` и LLM не нужны.
- `scan.db` версии 4: `git_project`, `scan_run`, `file_hash`, `file_snapshot`, `req` (глобальный `UNIQUE(code)`, `is_export`), `point` (`is_export`), `point_content` (номерные поля), `include_stat`; 4 PRAGMA, создание с нуля, версия ≠ 4 → `12`.
- `include_stat`: одна строка на `(git_project_id, pattern)`, `position`, `files_count`, `markers_count`, `status`; синхронизация с `SCAN.INCLUDE` при `scan` для всех проектов, пересчёт после reconciliation по успешно просканированным снимкам всех веток.
- `is_export` (`0` — экспорт не выполнялся, `1` — выполнен) на `req` и `point`; триггеры сбрасывают флаг при изменении `req.code/name/status`, `point.point/status`, а также вставке/изменении/удалении `point_content`. Обновление только `is_export` и повторный скан без изменений флаг сохраняют.
- `replace_occurrences` синхронизирует вхождения diff-ом (insert/update/delete), поэтому неизменённые маркеры не теряют номерные поля и `is_export`.
- `point_content.numbeartefact`, `numbercase`, `numberscenario`, `numberstep` — `TEXT DEFAULT NULL`; алгоритм заполнения и экспорт — будущая доработка.
- `scan/markers.py`: только канонический `<!-- REQ-META ... -->`, legacy-предупреждения, валидация id/point; завершающая точка в `point` отбрасывается (`5.5.1.` → `5.5.1`, `point="."` → ошибка), `SCAN_PARSER_VERSION = 2`.
- `scan/files.py`: `ls-tree`, include/exclude/secrets, размер, бинарность, LFS, кодировки, `blob_hash`, последний коммит файла.
- `scan/scanner.py`: ветки и `BRANCH_INCLUDE`/`BRANCH_EXCLUDE`, транзакция на проект, идемпотентность, reconciliation, GC хэшей, `--dry-run`/`--force`/`--strict`, Rich progress bar (интерактивно), сбой истории коммитов → `partial`/`11`.
- `scan`: метаданные последнего коммита для неизменённых файлов берутся из предыдущего снимка; `git log` вызывается только для изменённых/новых путей, а при повторном запуске без изменений история не читается.
- `scan`: обратный слэш перед неизвестным символом сохраняется (`C:\users`), `\"` и `\\` работают как escape-последовательности.
- `scan` не вызывает LLM, не требует ключей и не создаёт/не меняет `analysis.db`; `LINK_REQUIREMENTS` и `ATTACH` работают по общему `url_key`.
- Тесты: `244` проходят на временных git-репозиториях (включая инвалидацию `is_export`, номерные поля, UTC-нормализацию дат, уникальность имён отчётов, раздельные таймауты провайдеров, ограничение истории изменёнными путями, сохранение обратного слэша, статистику `include_stat`).
- `scan-git-push-TZ.md` v2, `dop_tz01.md` ред. 3.3, `docs/SPRINTS.md`, README, `user-guide.html`.

## Архитектурные решения
- Python 3.11+, Typer + Rich + Pydantic Settings, git CLI + sqlite3.
- Отказ от GitHub API и PyGithub: источник данных — локальное зеркало.
- Единый `url_key` в `scan.db` и `analysis.db` для `ATTACH` и связи требований.
- Отчёты всегда строятся из БД за период, а не из результатов текущего запуска.
- `scan` и `analyze` раздельны; команда `run` не реализуется.
- GitHub Models и Groq удалены полностью.
- LLM-выбор ленивый: пробы только при необходимости, кэш моделей Mistral.
- `scan.db` не хранит содержимое файлов: только `blob_hash` и размер; reconciliation вместо `TRUNCATE`.
- Инвалидация `is_export` — на уровне схемы (триггеры), а не в коде сканера: правило работает для любых будущих писателей БД.
- `commit_date` и `author_date` хранятся в UTC (`+00:00`), чтобы лексикографическое сравнение и `ORDER BY` были корректны; существующие строки с офсетом чинятся при `initialize()`.
- Статистика `include_stat` считается по `file_snapshot`, а не по всем совпадениям `SCAN.INCLUDE`: бинарные, большие, LFS, symlink/submodule и исключённые файлы не учитываются. Файл, подходящий под несколько шаблонов, учитывается в каждом; агрегация по всем веткам.
- Синхронизация `include_stat` выполняется только командой `scan`; `report-from-db --req` остаётся read-only.

## Следующие шаги
1. Производительность на репозитории > 10 000 коммитов (репозитория под рукой нет).
2. Релиз `0.2.0` опубликован: коммит `d8a49ad`, тег `v0.2.0` на `origin`; доработка 2026-11-03 — коммит `62602ce`, доработка 2026-11-04 (экспорт, номерные поля, UTC) — коммит `4e98a32`, доработка 2026-11-05 (имена отчётов, таймауты, точность ТЗ) — коммит `b44983b`, доработка 2026-11-06 (история, escape, `include_stat`) — коммиты `ef53b19`, `995a04f`, `34ce176`, все на `origin/main` (версия не поднималась).
3. При необходимости — поднять версию до `0.2.1` и опубликовать тег.

## Реальный прогон (2026-11-05, схема v3 / is_export / UTC)
- `db/scan.db` v2 сохранена как `db/scan.db.v2.bak`; файл был открыт в DBeaver, поэтому схема пересоздана в том же файле (drop + `SCAN_SCHEMA_SQL`) как v3.
- `scan`: 4 проекта (`example`, `KK`, `Test1`, `testqa`) — все `success`, 11 файлов, 9 маркеров, ошибок нет; в БД 6 требований, 7 пунктов, 9 вхождений, `schema_version = 3`.
- Номерные поля `point_content` у всех 9 вхождений `NULL`; `is_export = 0` у всех требований и пунктов.
- Имитация экспорта (`is_export = 1`) + повторный `scan` без изменений: флаги сохранены (`6/7`), `req_created = 0`, `point_created = 0` во всех проектах. После проверки флаги возвращены в `0`.
- `report-from-db --req --json`: код `0`, 6 требований, 7 разделов, HTML `reports/requirements_20261105.html`.
- `db/analysis.db`: 7 строк с `+03:00` нормализованы при `initialize()` в `+00:00` (лог `Normalized 7 commit date(s) to UTC`).

## Реальный прогон (2026-11-03, глобальные требования)
- Старая `db/scan.db` v1 сохранена как `db/scan.db.v1.bak` (gitignored) и удалена; схема пересоздана как v2.
- `scan`: 4 проекта (`example`, `KK`, `Test1`, `testqa`) — все `success`, 11 файлов, 8 маркеров, ошибок нет.
- `TR_3` с `point="5.5.1."` в `KK/check-list.html:1446` теперь валиден и сохранён как `5.5.1` (ранее — `invalid: point`).
- `FT_1` встречается в `Test1` и `testqa`, `REQ_5` — в `example` и `KK`: в БД по одному требованию, для второго проекта `req_created=0`.
- Повторный `scan` идемпотентен: `req_created=0`, `point_created=0` во всех проектах.
- `report-from-db --req --json`: `6` требований, `6` разделов, `8` вхождений, HTML `reports/requirements_20261103.html`; `--project …/Test1` даёт `1/1`.

## Реальный прогон (2026-11-02)
- Конфиг `config.json`: `SCAN.GIT_PROJECTS` = `example` + `KK` (GitHub), `SCAN.INCLUDE` включает `**/*.html`.
- `scan`: оба проекта `success`, 9 файлов, 5 маркеров, HTML-снимок `check-list.html`; в `KK` одна ошибка маркера `check-list.html:1446 invalid: point`, при `STRICT=false` не влияет на код. Повтор идемпотентен (`req_created=0`, `point_created=0`).
- Оба зеркала частичные: `remote.origin.promisor=true`, `partialclonefilter=blob:none`.
- `analyze`: Mistral вернул `429` (`x-ratelimit-limit-req-minute: 0`, квота ключа), поэтому прогон выполнен через локальный Ollama `qwen2.5-coder:7b` — 7 коммитов, отчёты по `example`/`KK` и сводный, ошибок нет.
- `report-from-db --days 4 --json`: код `0`, оба проекта и связи требований на месте, LLM не вызывался.

## Последнее обновление
2026-11-04
