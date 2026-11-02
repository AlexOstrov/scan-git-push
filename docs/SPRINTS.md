# План спринтов

**Дата:** 01.11.2026  
**Основание:** `scan-git-push-TZ.md` v2, `dop_tz01.md` ред. 3.1, `analiz_optimizacii_TZ.md`

Статусы: `[x]` — выполнено, `[~]` — в работе, `[ ]` — запланировано.

---

## Sprint 0. Конфигурация и git-фундамент — `[x]`

Цель: вложенный конфиг и базовый git-слой без PyGithub.

- [x] `config.py`: `SCAN` / `ANALYSIS` / `LLM`, `extra="forbid"`.
- [x] Плоский конфиг 0.1 транслируется в новую структуру с предупреждением.
- [x] `GIT_PROJECT` вместе с секциями → `ConfigError`.
- [x] Удалённые ключи `GH_MODELS_*`, `GROQ_*` игнорируются с предупреждением.
- [x] Относительные пути разрешаются от каталога `config.json`.
- [x] `git/url.py`: `normalize_url`, `url_key`, `repo_name`, `is_local_path`.
- [x] `git/runner.py`: `subprocess` с `GIT_TERMINAL_PROMPT=0`, классификация ошибок.
- [x] `git/errors.py`, `git/models.py` (`CommitData`, `FileChange`, `filename` для совместимости).
- [x] `PyGithub` удалён из `pyproject.toml`.
- [x] `config.json` переведён на вложенную структуру.

Проверка: `test_config.py`, `TestUrlNormalization` в `test_git.py`.

---

## Sprint 1. Зеркала и чтение ревизий — `[x]`

- [x] `git/mirror.py`: `MirrorManager.ensure/clone/fetch`, путь `<repo>-<sha256(url_key)[:10]>.git`.
- [x] `MIRROR_FILTER=auto`: `--filter=blob:none` для remote, полный клон для локальных путей.
- [x] `git/rev.py`: `resolve_refs`, `list_commits`, `numstat`, `read_patches`.
- [x] Коммиты по **committer date** (`%cI`, `--since`), `--no-merges`.
- [x] Все ветки (`--branches`), дедупликация SHA силами `git log`.
- [x] Потоковое чтение патчей через `Popen`, лимит на файл, обрезка по хунку.
- [x] Бинарные файлы, `--root`, имена с пробелами/юникодом.
- [x] Сквозная проверка на временном репозитории (`check_git_layer.py`).

Проверка: `TestMirrorManager`, `TestRevisionReading`.

---

## Sprint 2. `analysis.db` версии 1 — `[x]`

- [x] Схема `schema_version`, `git_project`, `analyzed_commits`, `commit_files`.
- [x] `url_key UNIQUE`, `UNIQUE(git_project_id, sha)`, каскадные удаления.
- [x] PRAGMA: `foreign_keys`, WAL, `synchronous=NORMAL` до транзакции, `busy_timeout`.
- [x] Миграция legacy-БД (`repo_url`) с бэкапом `analysis.db.bak`.
- [x] Версия ≠ 1 → `SchemaVersionError` → код `12`.
- [x] `cleanup_old_records`, `filter_unanalyzed`, `get_analyses`.

Проверка: `test_database.py`.

---

## Sprint 3. Пайплайн анализа — `[x]`

- [x] `analysis/collector.py`: сбор коммитов проекта, применение `ANALYSIS.EXCLUDE`.
- [x] `analysis/filters.py`: glob с `**`, регистронезависимое сопоставление.
- [x] `analysis/chunker.py`: `build_chunks`, `split_hunks`, cap на файл, `part N/M`.
- [x] `analyzer.py`: промпт v1, анализ чанков, слияние саммари, `NO_PATCH_SUMMARY`.
- [x] `ANALYSIS.EXCLUDE` по умолчанию: lock-файлы, минифицированные бандлы, `dist/`, `build/`, `vendor/`, `node_modules/`, `*.map`, `.git/`.
- [x] Бинарные и patchless-файлы не отправляются в LLM.

Проверка: `test_analyzer.py`, `check_pipeline.py`.

---

## Sprint 4. Отчётность — `[x]`

- [x] `reporters/service.py`: единая точка генерации.
- [x] `reporters/summary.py`: сводный отчёт `_summary_{date}.md/.html` с фильтром по проекту.
- [x] Markdown/HTML: требования, `+/-`, бинарные файлы, чанки, URL проекта.
- [x] Jinja2 `autoescape=True`; HTML без CDN.
- [x] Консоль: таблица файлов, требования, итоговая статистика.

Проверка: `test_reporters.py`.

---

## Sprint 5. LLM-провайдеры и реестр моделей — `[x]`

- [x] Удалены `llm/github_models.py`, `llm/groq.py` и их зависимость от токенов.
- [x] `llm/registry.py`: `ModelOption`, `discover`, `parse_spec`, `resolve`, `build`.
- [x] Кэш моделей Mistral `db/llm_models.json` с TTL.
- [x] `OllamaProvider` и `OpenAICompatibleProvider`: таймаут, retry, `provider_name`.
- [x] HTTP 429 считается доступностью; retry с backoff.
- [x] `--model provider/model`, `--non-interactive`, `LLM.MODEL`.

Проверка: `test_llm_providers.py`.

---

## Sprint 6. CLI, мультипроектность и связь с требованиями — `[x]`

- [x] `main` = `analyze`; `--project`, `--days`, `--model`, `--non-interactive`, `--json`, `--reanalyze`.
- [x] `report-from-db` с `--project` и `--json`.
- [x] Коды выхода `0/1/2/3/4/5/8/9/11/12`.
- [x] Отчёты всегда из БД за период; повторный запуск без коммитов даёт непустой отчёт.
- [x] `analysis/linker.py`: `LINK_REQUIREMENTS` через `url_key` и read-only `scan.db`.
- [x] Ошибка одного проекта не прерывает остальные (`11`).
- [x] `--json` в stdout, человекочитаемый вывод в stderr.

Проверка: `test_cli.py`, `check_pipeline.py`.

---

## Sprint 7. Тесты и документация — `[x]`

- [x] `tests/conftest.py`: временный git-репозиторий, конфиг, `FakeLLM`.
- [x] Переписаны `test_config`, `test_git`, `test_database`, `test_analyzer`, `test_llm_providers`, `test_cli`, `test_reporters`.
- [x] Удалены `github_client.py` и `test_github_client.py`.
- [x] `114` тестов проходят (`pytest -q`).
- [x] `scan-git-push-TZ.md` v2.
- [x] `dop_tz01.md` ред. 3.1: без fallback на `GIT_PROJECT`, `MIRROR_FILTER`, общий `url_key`.
- [x] `README.md`, `docs/memory/*`.

---

## Sprint 8. Режим `scan` — `[x]`

Цель: реализовать `dop_tz01.md` ред. 3.1 без изменения `analysis.db`.

### Шаг 1. Схема `scan.db`

- [x] `scan/database.py`: DDL из `dop_tz01.md` (раздел 7) и `schema_version = 1`.
- [x] Четыре PRAGMA; создание с нуля; версия ≠ 1 → код `12`.
- [x] UPSERT-хелперы: `git_project`, `file_hash`, `file_snapshot`, `req`, `point`, `point_content`, `scan_run`.
- [x] `GC_HASHES` после успешной обработки всех проектов.

### Шаг 2. Маркеры и файлы

- [x] `scan/markers.py`: парсинг только `<!-- REQ-META ... -->`; `[WARN] legacy marker format`.
- [x] `scan/files.py`: `ls-tree`, include/exclude/secrets, размер, бинарность, LFS, кодировки, `blob_hash`.
- [x] `scan/scanner.py`: обход веток, `cat-file --batch`, последний коммит файла, reconciliation.
- [x] Транзакция на проект; ошибка одного проекта → `11`.

### Шаг 3. CLI `scan`

- [x] `scan` + устаревший алиас `--scan`.
- [x] `--config`, `--project`, `--branch`, `--all-branches`, `--include`, `--exclude`, `--max-file-size`, `--dry-run`, `--force`, `--strict`, `--json`, `--no-color`, `--verbose`.
- [x] Коды выхода по приоритету `12 → 9 → 5 → 11 → 10 → 0`.
- [x] Консольная сводка по проектам.
- [x] Rich progress bar в интерактивном режиме; при `--json` и без TTY отключён.
- [x] Сбой чтения истории коммитов → статус `partial` и код `11`.

### Шаг 4. Интеграция с `analyze`

- [x] Общий `SCAN.MIRROR_DIR` и `url_key`; `LINK_REQUIREMENTS` читает `scan.db`.
- [x] Проверка `ATTACH`-запроса между `scan.db` и `analysis.db`.
- [x] `scan` не требует LLM-токенов и не меняет `analysis.db`.

### Шаг 5. Тесты и приёмка

- [x] Временные репозитории с маркерами, ветками, кодировками, LFS-указателями.
- [x] Идемпотентность, reconciliation, несколько проектов, коды выхода.
- [x] `--dry-run` не создаёт БД.
- [x] Обновить README и `docs/memory`.

Оценка: 2–3 дня. Риск: парсинг `cat-file --batch`, кодировки, reconciliation.

---

## Sprint 9. Релизная полировка — `[~]`

- [x] Прогон на реальных проектах (remote + local, partial clone): `scan` на `example` и `KK`, повтор идемпотентен; `analyze` через Ollama `qwen2.5-coder:7b` — 7 коммитов, отчёты MD/HTML; `report-from-db` без LLM.
- [ ] Проверка производительности на репозитории > 10 000 коммитов.
- [x] `python -m scan_git_push --help` и README-примеры.
- [x] Обновить `user-guide.html` под новую структуру конфига.
- [x] Тег версии `0.2.0` (`pyproject.toml`, `__init__.py`, CLI берёт `__version__`).
