# Техническое задание: Анализ git-коммитов проекта с LLM

**Версия:** 2.0  
**Дата:** 01.11.2026  
**Заменяет:** редакцию 1 (анализ через GitHub API)

---

## 1. Цель проекта

Разработать Python-утилиту, которая анализирует последние `ANALYSIS.GIT_DAY` коммитов одного или нескольких git-проектов и формирует саммари с помощью LLM (локальная Ollama или Mistral AI). Результат выводится в консоль, Markdown и самостоятельный интерактивный HTML-отчёт.

Ключевые изменения редакции 2:

- работа через **локальные git-зеркала**, без GitHub API и без обязательного `GITHUB_TOKEN`;
- **несколько проектов** в одном запуске (`SCAN.GIT_PROJECTS`);
- единая БД анализов `analysis.db` со схемой версии `1`;
- отчёты всегда строятся **из БД за период**, поэтому повторный запуск без новых коммитов не даёт пустой отчёт;
- режим `scan` (доп. ТЗ `dop_tz01.md`) и режим `analyze` используют **общие зеркала**; отдельная команда `run` не требуется;
- провайдеры `github_models` и `groq` удалены полностью.

Режим `scan` описан в `dop_tz01.md` (редакция 3) и в этой редакции основного ТЗ не дублируется.

---

## 2. Режимы работы

| Режим | Команда | Назначение | LLM |
|-------|---------|------------|-----|
| Анализ | `analyze` (алиас `main`) | Коммиты за период → саммари → отчёты | Да |
| Скан требований | `scan` | Маркеры `REQ-META` из `*.txt`/`*.md` → `scan.db` | Нет |
| Отчёты из БД | `report-from-db` | Повторная генерация отчётов без LLM | Нет |

`scan` и `analyze` независимы и используют один каталог зеркал `SCAN.MIRROR_DIR`. Команда `run`, объединяющая оба режима, не реализуется: достаточно выполнить `scan`, затем `analyze`.

---

## 3. Стек разработки

| Компонент | Выбор |
|-----------|-------|
| Язык | Python 3.11+ |
| Доступ к git | git CLI >= 2.30 (`subprocess`, без библиотек) |
| Ollama API | `ollama` (Python SDK) |
| Mistral API | `httpx` (OpenAI-совместимый endpoint) |
| CLI | `typer` |
| Вывод | `rich` (консоль), `jinja2` (HTML, autoescape) |
| Конфигурация | `pydantic-settings`, вложенные `SCAN` / `ANALYSIS` / `LLM` |
| Логирование | `loguru` |
| База данных | `sqlite3` (встроенная) |

`PyGithub`, `github_models` и `groq` в зависимостях и коде отсутствуют.

---

## 4. Конфигурационный файл

### 4.1 Структура `config.json`

```json
{
  "SECRETS_PATH": "d:\\projects\\fullstack-demo\\.secrets",
  "SCAN": {
    "GIT_PROJECTS": [
      "https://github.com/owner/repo-a.git",
      "d:/projects/repo-b"
    ],
    "DB_PATH": "db/scan.db",
    "MIRROR_DIR": "db/repos/",
    "MIRROR_FILTER": "auto",
    "BRANCHES": "all",
    "BRANCH_INCLUDE": [],
    "BRANCH_EXCLUDE": [],
    "INCLUDE": ["**/*.txt", "**/*.md"],
    "EXCLUDE": [],
    "EXCLUDE_SECRETS": [],
    "MAX_FILE_SIZE": 1048576,
    "ENCODING": "utf-8",
    "FALLBACK_ENCODING": "cp1251",
    "STRICT": false,
    "GC_HASHES": true,
    "LOG_PATH": "logs/scan.log"
  },
  "ANALYSIS": {
    "GIT_DAY": 7,
    "BRANCHES": "all",
    "FIRST_PARENT": false,
    "MAX_DIFF_CHARS_PER_CHUNK": 12000,
    "MAX_PATCH_CHARS_PER_FILE": 4000,
    "MAX_CHUNK_RETRIES": 3,
    "BACKOFF_FACTOR": 2,
    "EXCLUDE": [
      "*.lock",
      "package-lock.json",
      "pnpm-lock.yaml",
      "yarn.lock",
      "composer.lock",
      "poetry.lock",
      "Cargo.lock",
      "*.min.js",
      "*.min.css",
      "*.map",
      "dist/**",
      "build/**",
      "vendor/**",
      "node_modules/**",
      ".git/**"
    ],
    "PROMPT_VERSION": 1,
    "LINK_REQUIREMENTS": true,
    "OUTPUT_DIR": "reports/",
    "MEMORY_DIR": "docs/memory/",
    "LOG_PATH": "logs/analysis.log",
    "DB_PATH": "db/analysis.db",
    "DB_RETENTION_DAYS": 60
  },
  "LLM": {
    "PROVIDER": "mistral",
    "MODEL": null,
    "NON_INTERACTIVE": false,
    "PROBE_OLLAMA": true,
    "MODEL_CACHE_TTL": 3600,
    "HTTP_TIMEOUT": 30,
    "OLLAMA_HTTP_TIMEOUT": 180,
    "MISTRAL_MODEL": "mistral-small-latest",
    "MISTRAL_ENDPOINT": "https://api.mistral.ai/v1",
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "OLLAMA_MODEL": "llama3.1:8b"
  }
}
```

### 4.2 Правила

- Относительные пути (`DB_PATH`, `MIRROR_DIR`, `OUTPUT_DIR`, `MEMORY_DIR`, логи) разрешаются относительно каталога `config.json`.
- Неизвестные ключи внутри `SCAN`/`ANALYSIS`/`LLM` — ошибка конфигурации (`extra="forbid"`).
- Плоский конфиг редакции 1 (`GIT_PROJECT`, `GIT_DAY`, `LLM_PROVIDER`, …) однократно транслируется в новую структуру с предупреждением. `GIT_PROJECT` вместе с секциями — ошибка.
- Удалённые ключи (`GH_MODELS_*`, `GROQ_*`, `GITHUB_MODELS_TOKEN`, `GROQ_API_KEY`) игнорируются с предупреждением.

---

## 5. Доступ к репозиториям и аутентификация

### 5.1 Зеркала

Для каждого проекта создаётся одно bare-зеркало:

```
db/repos/<repo-name>-<sha256(url_key)[:10]>.git
```

- Первый запуск: `git clone --mirror`.
- Повторный: `git remote update --prune` в существующем зеркале.
- `SCAN.MIRROR_FILTER`:
  - `auto` (по умолчанию) — `--filter=blob:none` для удалённых URL, полный клон для локальных путей;
  - `blob:none` — partial clone всегда;
  - `none` — полный клон.
- Ошибки классифицируются: аутентификация (`GitAuthError`), репозиторий не найден (`GitNotFoundError`), git недоступен (`GitUnavailableError`).

### 5.2 Секреты

```ini
GITHUB_TOKEN=...       # необязателен; может использоваться credential helper
MISTRAL_API_KEY=...    # обязателен только для провайдера mistral
```

- Наличие файла секретов проверяется лениво — при первом обращении к секрету.
- `GITHUB_TOKEN` не является обязательным: публичные репозитории читаются без него.
- Учётные данные не сохраняются в URL, конфиге, БД и отчётах. Для HTTPS логин и пароль удаляются; для SSH сохраняется пользователь (`git@`), пароль удаляется.
- Git запускается с `GIT_TERMINAL_PROMPT=0`, чтобы не блокироваться на запросе пароля.

---

## 6. Выбор LLM-модели

1. Провайдеры: только `ollama` и `mistral`.
2. Ollama проверяется через `list()`; Mistral — минимальным chat-запросом (HTTP 429 считается доступностью).
3. Список моделей Mistral кэшируется в `db/llm_models.json` на `LLM.MODEL_CACHE_TTL` секунд.
4. Режимы выбора:
   - `--model provider/model` — явный выбор;
   - `LLM.MODEL` — значение по умолчанию;
   - интерактивный список доступных моделей;
   - `--non-interactive` / `LLM.NON_INTERACTIVE` — без запроса, используется `LLM.PROVIDER` + соответствующий `*_MODEL`.
5. Модель и провайдер записываются в БД для каждого коммита.

---

## 7. Анализ изменений

### 7.1 Сбор коммитов

1. Обновить зеркало проекта.
2. Выбрать ветки согласно `ANALYSIS.BRANCHES`:
   - `all` (по умолчанию) — все локальные ветки, дедупликация SHA выполняет сам `git log`;
   - `default` — ветка по умолчанию;
   - список имён — явные `refs/heads/*`.
3. Получить коммиты за `ANALYSIS.GIT_DAY` дней по **committer date** (`%cI`, `git log --since`), без merge-коммитов (`--no-merges`).
4. Для каждого коммита получить SHA, автора, даты, сообщение и список файлов (`diff-tree --numstat`, `--name-status`, `--root`).
5. Применить `ANALYSIS.EXCLUDE` к списку файлов до загрузки патчей.
6. Патчи читать потоково, с ограничением `MAX_PATCH_CHARS_PER_FILE`; бинарные файлы помечаются и не отправляются в LLM.

### 7.2 Chunking

1. Патч каждого файла обрезается до `MAX_PATCH_CHARS_PER_FILE` (по умолчанию 4000) по границе хунка.
2. Файлы группируются в чанки до `MAX_DIFF_CHARS_PER_CHUNK` (по умолчанию 12000).
3. Файл, превышающий лимит чанка, делится по хункам; каждая часть помечается `part N/M`.
4. Каждый чанк анализируется отдельно, затем саммари объединяются одним запросом.
5. Если текстовых патчей нет (только бинарные/удалённые/исключённые файлы), LLM не вызывается, сохраняется фиксированное пояснение.

### 7.3 Дедупликация через БД

1. Ключ — `(git_project_id, sha)`, где `git_project.url_key` — канонический идентификатор проекта.
2. Уже успешно проанализированные коммиты исключаются из обработки.
3. `--reanalyze` игнорирует кэш и повторно анализирует коммиты.
4. Неуспешный анализ не сохраняется и будет повторён.
5. Записи старше `DB_RETENTION_DAYS` удаляются после успешного запуска по столбцу `analyzed_commits.commit_date` (дата коммита).
6. Если запрошенный `--days` больше `DB_RETENTION_DAYS`, данные за пределами хранения уже удалены: отчёт формируется по оставшимся записям, считается неполным, но завершается с кодом `0`.

### 7.4 `report-from-db`

1. Читает сохранённые анализы за период (`--days`, по умолчанию `ANALYSIS.GIT_DAY`).
2. Поддерживает `--project` (повторяемый) и `--json`.
3. Формирует отчёты по каждому проекту и сводный отчёт; LLM не вызывается.
4. Если проектов в БД нет — сообщение и код `0`.
5. `--req` переключает команду на HTML-отчёт «Отчет по требованиям и разделам» из `scan.db` (без `analysis.db` и LLM): группировка требование → раздел, для каждого вхождения — проект, ветка, коммит, файл, номер строки, коммитер и `raw_marker`; итоги по требованиям и разделам. Файл `reports/requirements_YYYYMMDD.html`; `--project` фильтрует проекты, `--json` возвращает `report`, `requirements`, `sections`, `projects`, `exit_code`. Подробности — в `dop_tz01.md`.

---

## 8. База данных `analysis.db`

Схема версии `1`:

- `schema_version(version, applied_at)`;
- `git_project(id, url, url_key UNIQUE, author, status, created_at, updated_at)`;
- `analyzed_commits(..., UNIQUE(git_project_id, sha))`;
- `commit_files(commit_id, path, file_status, additions, deletions, is_binary, truncated, PRIMARY KEY(commit_id, path))`.

Полный DDL со всеми столбцами и индексами — в приложении A. Правила:

- `url_key`: remote — `host/path` без схемы, учётных данных и `.git`; local — абсолютный POSIX-путь. Тот же ключ используется в `scan.db`, что позволяет связывать БД через `ATTACH`.
- PRAGMA: `foreign_keys = ON`, `journal_mode = WAL`, `synchronous = NORMAL` (до транзакции), `busy_timeout = 5000`.
- Удаление проекта каскадно удаляет коммиты и файлы.
- Legacy-БД редакции 1 мигрируется один раз с бэкапом `analysis.db.bak`; версия схемы, отличная от `1`, — ошибка `12`.

Подробная схема `scan.db` — в `dop_tz01.md`.

---

## 9. Связь коммитов с требованиями

При `ANALYSIS.LINK_REQUIREMENTS=true`:

1. Если `scan.db` существует, выполняется read-only запрос по `url_key` проекта.
2. Для файлов, затронутых коммитом, выбираются активные требования и пункты, в файлах которых найдены маркеры.
3. Результат добавляется в `AnalysisResult.requirements` и отображается в консоли, Markdown и HTML.
4. Если `scan.db` отсутствует или имеет несовместимую схему — связывание пропускается с предупреждением, анализ продолжается.

---

## 10. Форматы вывода

### 10.1 Консоль

- Заголовок проекта и URL.
- Для каждого коммита: SHA, автор, дата, сообщение, таблица файлов, требования, саммари.
- Итоговая статистика: коммиты, файлы, строки, чанки, требования.

### 10.2 Markdown

`reports/{repo}-{sha256(url_key)[:6]}_{YYYYMMDD}.md`:

- проект, период (committer date), дата генерации;
- по каждому коммиту: SHA, автор, сообщение, таблица файлов, требования, саммари;
- общий итог.

### 10.3 HTML

`reports/{repo}-{sha256(url_key)[:6]}_{YYYYMMDD}.html`:

- полностью автономный файл (встроенные CSS/JS, без CDN);
- боковая навигация по коммитам;
- карточки статистики;
- бейджи требований и чанков;
- значения экранируются (Jinja2 `autoescape`).

### 10.4 Сводный отчёт

`reports/_summary_{YYYYMMDD}.md` и `.html`:

- таблица проектов (коммиты, файлы, строки, требования);
- фильтр по проекту в HTML;
- детализация по каждому проекту.

---

## 11. CLI и коды выхода

```
python -m scan_git_push [main|analyze] [--config PATH] [--project URL ...]
       [--days N] [--model PROVIDER/MODEL] [--non-interactive] [--json] [--reanalyze]
python -m scan_git_push report-from-db [--config PATH] [--project URL ...]
       [--days N] [--req] [--json]
python -m scan_git_push scan ...        # см. dop_tz01.md
```

| Код | Значение |
|-----|----------|
| `0` | Успех |
| `1` | Ошибка конфигурации/окружения (нет config, нет git, нет проектов) |
| `2` | Ошибка аутентификации git |
| `3` | Нет доступных LLM-моделей |
| `4` | Неверные аргументы CLI |
| `5` | Ни один проект не доступен |
| `8` | Отсутствует `MISTRAL_API_KEY` при выборе Mistral |
| `9` | Ошибка БД |
| `11` | Частичный успех: часть проектов/коммитов не обработана |
| `12` | Несовместимая схема БД |

Приоритет кодов анализа: `12` → `9` → (нет обработанных проектов: `2` при ошибке аутентификации, иначе `5`) → `11` → `0`. Ошибка аутентификации одного проекта при успешной обработке остальных даёт `11`, а не `2`. Для режима `scan` действует приоритет из `dop_tz01.md`.

`--json` печатает в stdout один JSON-объект (`generated_at`, `days`, `model`, `projects[]`, `summary`, `errors`, `analyzed_now`, `exit_code`); человекочитаемый вывод уходит в stderr.

---

## 12. Обработка ошибок и retry

| Ситуация | Действие |
|----------|----------|
| Ошибка одного проекта | Записать в `errors`, продолжить остальные; итог `11` |
| Ошибка одного коммита | Записать в лог, пропустить коммит; итог `11` |
| Сетевые ошибки LLM | До `MAX_CHUNK_RETRIES` попыток, backoff `BACKOFF_FACTOR` |
| Таймаут LLM | `LLM.OLLAMA_HTTP_TIMEOUT` (180 с) для Ollama; `LLM.HTTP_TIMEOUT` (30 с) для Mistral |
| Ошибка git-команды | Классификация и сообщение; проект пропускается |
| Ошибка записи в БД | Завершение с кодом `9` |
| Несовместимая схема | Завершение с кодом `12` |

---

## 13. Артефактная память

Файлы `docs/memory/{STATE,FEATURES,DECISIONS,CHANGELOG}.md`:

- `STATE.md` читается при старте сессии;
- после успешного анализа в `CHANGELOG.md` добавляется запись;
- архитектурные решения фиксируются в `DECISIONS.md`;
- реализованные возможности — в `FEATURES.md`.

---

## 14. Структура проекта

```
scan-git-push/
├── scan_git_push/
│   ├── cli.py                # Typer: main/analyze, report-from-db, scan
│   ├── config.py             # SCAN/ANALYSIS/LLM + legacy-апгрейд
│   ├── database.py           # analysis.db v1, миграция
│   ├── analyzer.py           # prompt, chunk analysis, merge
│   ├── git/
│   │   ├── url.py            # normalize_url, url_key, repo_name
│   │   ├── runner.py         # subprocess git, классификация ошибок
│   │   ├── mirror.py         # клонирование/обновление зеркал
│   │   ├── rev.py            # log/numstat/patch
│   │   ├── models.py         # CommitData, FileChange
│   │   └── errors.py
│   ├── analysis/
│   │   ├── collector.py      # сбор коммитов проекта
│   │   ├── chunker.py        # build_chunks, split_hunks
│   │   ├── filters.py        # glob ** и EXCLUDE
│   │   ├── linker.py         # связь с scan.db
│   │   └── results.py        # строки БД → AnalysisResult
│   ├── llm/
│   │   ├── base.py
│   │   ├── ollama.py
│   │   ├── openai_compatible.py
│   │   ├── mistral.py
│   │   └── registry.py       # discovery, cache, resolve/build
│   ├── reporters/
│   │   ├── console.py
│   │   ├── markdown.py
│   │   ├── html.py
│   │   ├── summary.py
│   │   └── service.py
│   └── memory/manager.py
├── docs/
│   ├── memory/{STATE,FEATURES,DECISIONS,CHANGELOG}.md
│   └── SPRINTS.md
├── db/                       # analysis.db, scan.db, repos/ (создаётся)
├── reports/
├── tests/
├── config.json
├── pyproject.toml
├── scan-git-push-TZ.md
└── dop_tz01.md
```

---

## 15. Критерии приёмки

- [x] `GIT_PROJECT` отсутствует в коде и конфиге; проекты берутся из `SCAN.GIT_PROJECTS`.
- [x] Два и более проекта обрабатываются последовательно; отчёт формируется на каждый проект и сводный.
- [x] Для каждого проекта создаётся/обновляется одно зеркало; повторный запуск не клонирует заново.
- [x] Коммиты собираются локально, без обращений к GitHub API; все ветки с дедупликацией SHA.
- [x] `PyGithub`, `github_models`, `groq` удалены из кода и зависимостей.
- [x] `GITHUB_TOKEN` не обязателен; Ollama работает без секретов.
- [x] `--model`, `--non-interactive`, `--json`, `--reanalyze` работают.
- [x] `report-from-db` и `report-from-db --project --json` работают.
- [x] Повторный запуск без новых коммитов формирует непустой отчёт за период.
- [x] `analysis.db` версии `1`; legacy-БД мигрирует с бэкапом; версия ≠ 1 → код `12`.
- [x] Ошибка одного проекта не прерывает остальные; код `11`.
- [x] Patch ограничен `MAX_PATCH_CHARS_PER_FILE`; chunking по файлам и хункам.
- [x] `ANALYSIS.EXCLUDE` применяется до загрузки патчей.
- [x] HTML-отчёт автономен и экранирует значения.
- [x] `LINK_REQUIREMENTS` связывает коммиты с маркерами `scan.db`.
- [x] Тесты используют временные git-репозитории, а не моки PyGithub.
- [x] Команда `scan` реализована по `dop_tz01.md` (Sprint 8); в Sprint 10 добавлены глобальные требования и `report-from-db --req`, ред. 3.2 — `is_export` и номерные поля.

---

## 16. Нефункциональные требования

| Требование | Значение |
|------------|----------|
| Python | >= 3.11 |
| git | >= 2.30 |
| Зависимости | только из `pyproject.toml` |
| Таймаут HTTP | Ollama: 180 с (`LLM.OLLAMA_HTTP_TIMEOUT`); Mistral: 30 с (`LLM.HTTP_TIMEOUT`) |
| Retry LLM | `MAX_CHUNK_RETRIES`, backoff `BACKOFF_FACTOR` |
| Размер чанка | <= `MAX_DIFF_CHARS_PER_CHUNK` |
| Патч на файл | <= `MAX_PATCH_CHARS_PER_FILE` |
| Память | потоковое чтение `git diff-tree`, без загрузки всего diff |
| SQLite | WAL, `synchronous = NORMAL`, `busy_timeout = 5000` |
| Вывод | UTF-8 |
| Логирование | rich + файл `logs/` с ротацией |
| Отчёты | MD + HTML без внешних зависимостей |

---

## Приложение A. Полный DDL `analysis.db`

Нормативная схема версии `1` (соответствует `SCHEMA_SQL` в `scan_git_push/database.py`):

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS git_project (
    id          INTEGER PRIMARY KEY,
    url         TEXT    NOT NULL,
    url_key     TEXT    NOT NULL UNIQUE,
    author      TEXT,
    status      INTEGER NOT NULL DEFAULT 1 CHECK (status IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS analyzed_commits (
    id              INTEGER PRIMARY KEY,
    git_project_id  INTEGER NOT NULL REFERENCES git_project(id) ON DELETE CASCADE,
    sha             TEXT    NOT NULL,
    commit_date     TEXT    NOT NULL,
    author_date     TEXT,
    analyzed_at     TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'success'
                            CHECK (status IN ('success', 'failed')),
    summary         TEXT,
    author          TEXT,
    committer       TEXT,
    email           TEXT,
    message         TEXT,
    files_count     INTEGER NOT NULL DEFAULT 0,
    additions       INTEGER NOT NULL DEFAULT 0,
    deletions       INTEGER NOT NULL DEFAULT 0,
    chunks_count    INTEGER NOT NULL DEFAULT 0,
    provider        TEXT,
    model           TEXT,
    prompt_version  INTEGER NOT NULL DEFAULT 1,
    UNIQUE (git_project_id, sha)
);

CREATE TABLE IF NOT EXISTS commit_files (
    commit_id   INTEGER NOT NULL REFERENCES analyzed_commits(id) ON DELETE CASCADE,
    path        TEXT    NOT NULL,
    file_status TEXT    NOT NULL DEFAULT 'modified',
    additions   INTEGER NOT NULL DEFAULT 0,
    deletions   INTEGER NOT NULL DEFAULT 0,
    is_binary   INTEGER NOT NULL DEFAULT 0 CHECK (is_binary IN (0, 1)),
    truncated   INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),
    PRIMARY KEY (commit_id, path)
);

CREATE INDEX IF NOT EXISTS idx_commits_project_date
    ON analyzed_commits(git_project_id, commit_date);

CREATE INDEX IF NOT EXISTS idx_commits_analyzed_at
    ON analyzed_commits(analyzed_at);
```

`commit_date` и `author_date` хранятся в UTC (`+00:00`). Индекс `idx_commits_analyzed_at` служит для диагностики по времени анализа; ретенция `DB_RETENTION_DAYS` выполняется по `commit_date` — см. §7.3.

