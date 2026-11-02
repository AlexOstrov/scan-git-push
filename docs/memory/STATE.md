# Текущее состояние проекта

## Текущая задача
Sprint 9 (релизная полировка) в работе: реальный прогон на двух удалённых проектах выполнен, `user-guide.html` и версия `0.2.0` обновлены. Осталось проверить производительность на репозитории > 10 000 коммитов и закоммитить релиз.

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
- Мультипроектные отчёты: на проект + `_summary_{date}` (MD/HTML), автономный HTML, autoescape.
- `scan.db` версии 1: `git_project`, `scan_run`, `file_hash`, `file_snapshot`, `req`, `point`, `point_content`; 4 PRAGMA, создание с нуля, версия ≠ 1 → `12`.
- `scan/markers.py`: только канонический `<!-- REQ-META ... -->`, legacy-предупреждения, валидация id/point.
- `scan/files.py`: `ls-tree`, include/exclude/secrets, размер, бинарность, LFS, кодировки, `blob_hash`, последний коммит файла.
- `scan/scanner.py`: ветки и `BRANCH_INCLUDE`/`BRANCH_EXCLUDE`, транзакция на проект, идемпотентность, reconciliation, GC хэшей, `--dry-run`/`--force`/`--strict`, Rich progress bar (интерактивно), сбой истории коммитов → `partial`/`11`.
- `scan` не вызывает LLM, не требует ключей и не создаёт/не меняет `analysis.db`; `LINK_REQUIREMENTS` и `ATTACH` работают по общему `url_key`.
- Тесты переписаны на временные git-репозитории: `196` проходят.
- `scan-git-push-TZ.md` v2, `dop_tz01.md` ред. 3.1, `docs/SPRINTS.md`, README.

## Архитектурные решения
- Python 3.11+, Typer + Rich + Pydantic Settings, git CLI + sqlite3.
- Отказ от GitHub API и PyGithub: источник данных — локальное зеркало.
- Единый `url_key` в `scan.db` и `analysis.db` для `ATTACH` и связи требований.
- Отчёты всегда строятся из БД за период, а не из результатов текущего запуска.
- `scan` и `analyze` раздельны; команда `run` не реализуется.
- GitHub Models и Groq удалены полностью.
- LLM-выбор ленивый: пробы только при необходимости, кэш моделей Mistral.
- `scan.db` не хранит содержимое файлов: только `blob_hash` и размер; reconciliation вместо `TRUNCATE`.

## Следующие шаги
1. Sprint 9: производительность на репозитории > 10 000 коммитов (репозитория под рукой нет).
2. Закоммитить и запушить релиз `0.2.0`, проставить тег.

## Реальный прогон (2026-11-02)
- Конфиг `config.json`: `SCAN.GIT_PROJECTS` = `example` + `KK` (GitHub), `SCAN.INCLUDE` включает `**/*.html`.
- `scan`: оба проекта `success`, 9 файлов, 5 маркеров, HTML-снимок `check-list.html`; в `KK` одна ошибка маркера `check-list.html:1446 invalid: point`, при `STRICT=false` не влияет на код. Повтор идемпотентен (`req_created=0`, `point_created=0`).
- Оба зеркала частичные: `remote.origin.promisor=true`, `partialclonefilter=blob:none`.
- `analyze`: Mistral вернул `429` (`x-ratelimit-limit-req-minute: 0`, квота ключа), поэтому прогон выполнен через локальный Ollama `qwen2.5-coder:7b` — 7 коммитов, отчёты по `example`/`KK` и сводный, ошибок нет.
- `report-from-db --days 4 --json`: код `0`, оба проекта и связи требований на месте, LLM не вызывался.

## Последнее обновление
2026-11-02
