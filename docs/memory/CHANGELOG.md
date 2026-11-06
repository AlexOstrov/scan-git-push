# CHANGELOG

- [2026-10-19 20:48] Analyzed 25 commits from fullstack-demo using ollama/gemma4:12b
- [2026-10-21 21:12] Analyzed 3 commits from fullstack-demo using mistral/ministral-8b-latest

## 2026-11-05 — точность отчётов, конфига и документации

- Имена проектных отчётов: `{repo}-{sha256(url_key)[:6]}_{YYYYMMDD}.{md,html}` — одноимённые проекты больше не перезаписывают файлы; суффикс всегда детерминирован.
- Удалён неиспользуемый `ANALYSIS.CONCURRENCY` из `config.py`, примеров конфига и документации.
- Добавлен `LLM.OLLAMA_HTTP_TIMEOUT` (по умолчанию 180 с, 5–600); `LLM.HTTP_TIMEOUT` (30 с, 5–120) остаётся для Mistral.
- `scan-git-push-TZ.md`: полный DDL `analysis.db` вынесен в приложение A (включая `author_date`, `email`, `idx_commits_analyzed_at`); уточнены приоритет кодов (`12 → 9 → 2/5 → 11 → 0`, auth при частичном успехе — `11`), таймауты и ретенция по `commit_date` (неполный отчёт при `--days > DB_RETENTION_DAYS`, код `0`).
- `dop_tz01.md`: в JSON добавлены `totals.files_skipped` и `dry_run`; для `--req` задокументированы коды `9` (нет `scan.db`) и `12` (несовместимая схема); `analiz_optimizacii_TZ.md` — статус «исполнено; нормативно — ТЗ v2».
- `FEATURES.md`: в списке кодов выхода добавлен `10`.
- `analyze`: ошибки проектов санитизируются `redact_credentials()` перед записью в JSON и консоль, как в режиме `scan`.
- Тесты: `230 passed` (добавлены проверки коллизии имён и раздельных таймаутов).
- Коммит `b44983b` на `origin/main`.

## 2026-11-06 — производительность `scan` и точность парсера

- `last_commit_map` принимает список путей: для неизменённых файлов `commit_sha`/`author`/`committer` берутся из предыдущего `file_snapshot`, а `git log` вызывается только для изменённых и новых путей. Если не изменился ни один файл, история не читается; для очень большого первого запуска остаётся один полный проход.
- Парсер маркеров сохраняет обратный слэш перед неизвестным символом (`C:\users` больше не превращается в `C:users`); `\"` и `\\` по-прежнему декодируются. EBNF в `dop_tz01.md` §6.1 обновлена (`literal-backslash`).
- `dop_tz01.md` §16: статусы `req`/`point` eventually-consistent — актуальны после завершения полного запуска; `file_snapshot.parser_version DEFAULT 1` — историческое значение, при upsert пишется текущий `SCAN_PARSER_VERSION`.
- `scan-git-push-TZ.md`: `status='failed'` в `analyzed_commits` зарезервирован схемой, но текущий анализатор его не записывает (§7.3.4).
- DDL `file_snapshot` и метка `force_run` не менялись: `--force` аудитируется через `scan_run.message`.
- Тесты: `232 passed` (добавлены проверки ограничения истории и escape-последовательностей).
- Коммит `ef53b19` на `origin/main`.

## 2026-11-06 — статистика по шаблонам `SCAN.INCLUDE`

- `scan.db` → версия `4`: таблица `include_stat` (`git_project_id`, `pattern`, `position`, `files_count`, `markers_count`, `status`, timestamps, `UNIQUE(git_project_id, pattern)`) и индекс `idx_include_stat_project`.
- При `scan` таблица синхронизируется с `SCAN.INCLUDE` для всех проектов: отсутствующие шаблоны добавляются с нулевыми счётчиками, удалённые получают `status = 0`, изменяется `position`; новый проект получает строки сразу после регистрации.
- После reconciliation каждого проекта счётчики пересчитываются по успешно просканированным `file_snapshot` всех веток; файл, подходящий под несколько шаблонов, учитывается в каждом.
- `report-from-db --req`: в конце HTML выводится сводная таблица по проектам (URL, автор, шаблон, число файлов и маркеров); `--json` получил ключ `include_stats`.
- `dop_tz01.md` ред. 3.3: обновлены §0.1, §0.4, §2.1–2.2, §4.6, шаг 0, §2.1, §2.5, §7.2–7.3, §7.5, §14–16, прил. B.6; версия схемы `4`.
- `README.md`, `user-guide.html`, `scan-git-push-TZ.md` обновлены под схему v4 и новый отчёт.
- Тесты: `244 passed` (добавлены проверки схемы v4, синхронизации/архивации/реактивации шаблонов, пересчёта по веткам и пересечениям, HTML и JSON).
- Коммит `34ce176` на `origin/main`.

## 2026-11-04 — обратная связь: экспорт, номерные поля, UTC-даты

- `scan.db` → версия `3`: у `req` и `point` добавлен `is_export` (`0` — экспорт не выполнялся, `1` — выполнен); в `point_content` добавлены `numbeartefact`, `numbercase`, `numberscenario`, `numberstep` (`TEXT DEFAULT NULL`).
- Инвалидация `is_export` — пять SQLite-триггеров: изменение `req.code/name/status`, `point.point/status`, вставка/изменение/удаление `point_content`. Повторный скан без изменений и обновление только `is_export` флаг не сбрасывают.
- `replace_occurrences` переведён на diff, чтобы неизменённые маркеры сохраняли номерные поля и признак экспорта.
- `commit_date`/`author_date` нормализуются в UTC при разборе (`rev._parse_date`), записи (`_to_utc_iso`) и при `initialize()` для существующих строк; схема `analysis.db` не менялась.
- Устранены противоречия `dop_tz01.md`: глобальное `req` в §14.4/§15.3, версия схемы в §2.2, пояснение счётчиков в §11.2, SQL в прил. B; редакция 3.2 от 04.11.2026.
- `scan-git-push-TZ.md`: чек-лист `scan` отмечен выполненным.
- README, `user-guide.html`, память проекта обновлены; тесты — `228 passed`.
- Коммит `4e98a32` на `origin/main`.
- Реальная проверка: `db/scan.db` v2 сохранена как `.v2.bak` и пересоздана в v3 (4 проекта, 6 требований, 7 пунктов, 9 вхождений); флаги `is_export` выжили повторный `scan`, затем возвращены в `0`; `report-from-db --req` — 6/7; 7 дат `analysis.db` нормализованы из `+03:00` в `+00:00`.

## 2026-11-03 — обратная связь по эксплуатации

- `point` с завершающей точкой нормализуется: `5.5.1.` → `5.5.1`; `point="."` отклоняется. `SCAN_PARSER_VERSION` → `2` (принудительный переразбор).
- Требования глобальны: `req.code` теперь `UNIQUE(code)` без `git_project_id`; схема `scan.db` → `2`; статусы требований/разделов пересчитываются по всем проектам.
- `report-from-db --req`: автономный HTML «Отчет по требованиям и разделам» (требование → раздел → вхождения: проект, ветка, коммит, файл, строка, коммитер, raw_marker) + итоги; `--project` и `--json`; `analysis.db` и LLM не нужны.
- Тесты: нормализация `point`, глобальная уникальность, схема v2, отчёт `--req` и фильтрация по проекту.
- Документация: `dop_tz01.md`, `README.md`, `user-guide.html`, память проекта.

## 2026-11-02 — релизная полировка (Sprint 9)

- Версия `0.2.0` в `pyproject.toml` и `scan_git_push/__init__.py`; CLI печатает версию из `__version__` вместо захардкоженных строк.
- `user-guide.html` переписан под вложенный конфиг `SCAN`/`ANALYSIS`/`LLM`, локальные зеркала, режим `scan` и актуальные коды выхода.
- Реальный прогон на `example` и `KK`: `scan` — 9 файлов, 5 маркеров, HTML-снимок, повтор идемпотентен; зеркала частичные (`blob:none`).
- `analyze` — 7 коммитов через Ollama `qwen2.5-coder:7b`; Mistral недоступен из-за `429` (квота ключа), ошибок анализа нет.
- `report-from-db --days 4` — код `0`, отчёты и связи требований строятся без LLM.

## 2026-11-01 — режим `scan` (`dop_tz01.md` ред. 3.1)

- Добавлен отдельный пакет `scan_git_push/scan/`: `database.py`, `markers.py`, `files.py`, `scanner.py`.
- `db/scan.db` версии 1: `git_project`, `scan_run`, `file_hash`, `file_snapshot`, `req`, `point`, `point_content`; 4 PRAGMA, создание с нуля, код `12` при несовместимой версии.
- Парсинг только канонического `<!-- REQ-META ... -->`; legacy-форматы дают `[WARN] legacy marker format`; валидация `id`/`point`.
- `scan`: `all`/`default`/список веток, `BRANCH_INCLUDE`/`BRANCH_EXCLUDE`, include/exclude/secrets, лимит размера, бинарность, LFS, symlink, submodule, кодировки.
- Идемпотентность, reconciliation требований/пунктов/снимков/веток, GC хэшей, транзакция на проект.
- CLI `scan` и устаревший алиас `--scan`; `--dry-run`, `--force`, `--strict`, `--json`, `--no-color`, `--verbose`; коды `0/1/5/9/10/11/12` с приоритетом `12 → 9 → 5 → 11 → 10 → 0`.
- Rich progress bar в интерактивном режиме; при `--json` и без TTY прогресс отключён.
- Сбой чтения истории коммитов переводит проект в `partial` (код `11`).
- `scan` не вызывает LLM, не требует токенов и не создаёт/не меняет `analysis.db`; `ATTACH` и `LINK_REQUIREMENTS` используют общий `url_key`.
- Тесты `scan` на временных git-репозиториях; всего `196` тестов проходят.

## 2026-11-01 — редакция 2

- Переход на локальные git-зеркала; PyGithub и `github_client.py` удалены.
- Конфигурация `SCAN` / `ANALYSIS` / `LLM`; удалён `GIT_PROJECT`.
- Мультипроектность: `SCAN.GIT_PROJECTS`, отчёт на проект + сводный.
- Все ветки (`ANALYSIS.BRANCHES=all`), committer date, `--no-merges`.
- `analysis.db` версии 1, миграция legacy с бэкапом, код `12`.
- Отчёты всегда из БД за период; `report-from-db --project --json`.
- `LINK_REQUIREMENTS` через `scan.db` и `url_key`.
- Удалены `github_models` и `groq`; `llm/registry.py`, `--model`, `--non-interactive`, `--reanalyze`.
- Chunking: `MAX_PATCH_CHARS_PER_FILE`, деление по хункам, `NO_PATCH_SUMMARY`.
- `ANALYSIS.EXCLUDE` с согласованным дефолтом.
- Тесты переписаны на временные git-репозитории: 114 проходят.
- Документы: `scan-git-push-TZ.md` v2, `dop_tz01.md` ред. 3.1, `docs/SPRINTS.md`, README.
- [2026-11-02 10:07] Analyzed 7 commit(s) in 2 project(s) using ollama/qwen2.5-coder:7b
