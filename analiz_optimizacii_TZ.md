# Анализ основного ТЗ: оптимизация под требования доп. ТЗ (`scan`)

**Дата:** 01.11.2026  
**Основание:** `scan-git-push-TZ.md` (основное ТЗ), `dop_tz01.md` (редакция 3), фактический код `scan_git_push/*`  
**Статус:** анализ, решения требуют подтверждения

---

## 0. Резюме

1. **Единый список проектов.** `GIT_PROJECT` удаляется из основного ТЗ; единственный источник — `SCAN.GIT_PROJECTS`. Основной режим становится многопроектным с той же семантикой, что и `scan` (последовательно, свой `scan_run`/отчёт на проект).
2. **Зеркала вместо GitHub API.** `PyGithub` и `github_client.py` заменяются на локальное зеркало + `git log` / `git diff-tree`. Это убирает N+1 сетевых запросов, лимит 5000 запросов/час, обязательный `GITHUB_TOKEN` и усечение patch'ей, а также даёт офлайн-работу.
3. **Один менеджер зеркал на два режима.** `MirrorManager`, нормализация URL и реестр `git_project` становятся общим кодом для `scan` и анализа коммитов — самый крупный выигрыш по объёму кода и по времени (одно обновление зеркала на запуск).
4. **Слияние отчётности.** `main` и `report-from-db` сейчас дублируют генерацию отчётов; после слияния отчёт всегда строится из БД за период, а не только по «новым» коммитам текущего запуска.
5. **Единая схема анализа.** `analysis.db` получает `git_project` + `schema_version` + ключ `(git_project_id, sha)` вместо `repo_url` строкой; ad-hoc `ALTER TABLE`-миграция удаляется.
6. **Синергия режимов.** Маркеры требований + история коммитов позволяют связать коммиты с требованиями (`REQ`/`POINT`) — новая функция, которая без зеркал и `scan` была невозможна.
7. **Упрощение старта.** Пробы четырёх LLM-провайдеров при каждом запуске и обязательный интерактивный выбор модели мешают автоматизации; предлагается ленивая проверка, кэш списка моделей и `--model` / `--non-interactive`.

---

## 1. Что есть сейчас (факты из кода)

| Место | Текущее поведение | Ограничение |
|-------|-------------------|-------------|
| `config.py:25` | `git_project: str` — один проект | нет многопроектности |
| `config.py:93-98` | валидатор допускает только `https://github.com/` и `git@github.com:` | нельзя локальные пути, `ssh://`, `file://`, GitLab |
| `github_client.py:86-87` | `repo.get_commits(since=...)` | 1 запрос на список |
| `github_client.py:107` | `commit.files` внутри цикла | **N+1 запрос**: отдельный GET на каждый коммит |
| `github_client.py:108-113` | `patch` из GitHub API | patch усечён/отсутствует для больших и бинарных файлов |
| `cli.py:47-54` | `GITHUB_TOKEN` обязателен всегда | публичные репозитории и ssh-доступ невозможны |
| `cli.py:222-309` | проверяются 4 провайдера (Ollama, GitHub Models, Groq, Mistral) | 4 сетевых пробы на каждый запуск, GitHub Models закрыт, Groq геоблокирован |
| `cli.py:312` | всегда интерактивный выбор модели | нельзя запустить по cron/CI |
| `cli.py:371-381` | дедупликация по `repo_url` строкой | дубликаты при разных формах URL (`https` vs `ssh`, `.git`) |
| `cli.py:425-450` | отчёт только по `results` текущего запуска | повторный запуск без новых коммитов даёт пустой отчёт |
| `cli.py:470-573` | `report-from-db` дублирует генерацию отчётов | два пути, расхождения в поведении |
| `database.py:41-52` | `analyzed_commits(sha, repo_url)` | нет FK на проект, нет версии схемы |
| `database.py:73-82` | миграция через `ALTER TABLE` в `try/except` | нет контроля версии, скрытые ошибки |
| `analyzer.py:117-120` | файл больше лимита уходит отдельным чанком целиком | превышение контекста модели |
| `analyzer.py:154` | `[Чась {n}]` | опечатка в промпте |
| `pyproject.toml:17` | `PyGithub>=2.1.0` | обязательная зависимость от API |

### 1.1. Оценка стоимости текущего подхода

| Сценарий | Запросов к API | Ориентир по времени |
|----------|----------------|---------------------|
| 7 дней, 50 коммитов | 51 | 10–25 c (сеть + рейт-лимит) |
| 30 дней, 500 коммитов | 501 | 2–5 мин, риск 403 при параллельных запусках |
| 5000+ коммитов (крупный репозиторий) | > 5000 | упирается в лимит 5000 запросов/час |

Дополнительно: patch'и из GitHub API для файлов с большим diff и для бинарных файлов **не приходят**, то есть LLM анализирует неполные данные — это не только скорость, но и корректность.

---

## 2. Оптимизация 1. Отказ от `GIT_PROJECT` в пользу `SCAN.GIT_PROJECTS`

### 2.1. Целевая модель конфигурации

```json
{
  "SECRETS_PATH": "d:\\projects\\fullstack-demo\\.secrets",

  "SCAN": {
    "GIT_PROJECTS": [
      "https://github.com/owner/repo-a.git",
      "git@github.com:owner/repo-b.git",
      "d:\\projects\\local-repo"
    ],
    "MIRROR_DIR": "db/repos/",
    "BRANCHES": "all",
    "INCLUDE": ["**/*.txt", "**/*.md"],
    "EXCLUDE": [],
    "EXCLUDE_SECRETS": [],
    "MAX_FILE_SIZE": 1048576,
    "DB_PATH": "db/scan.db",
    "LOG_PATH": "logs/scan.log"
  },

  "ANALYSIS": {
    "GIT_DAY": 7,
    "BRANCHES": "default",
    "MAX_DIFF_CHARS_PER_CHUNK": 12000,
    "MAX_PATCH_CHARS_PER_FILE": 4000,
    "MAX_CHUNK_RETRIES": 3,
    "BACKOFF_FACTOR": 2,
    "CONCURRENCY": 1,
    "EXCLUDE": ["*.lock", "*.min.js", "*.min.css", "*.map", "dist/**", "vendor/**"],
    "PROMPT_VERSION": 1,
    "LINK_REQUIREMENTS": false,
    "OUTPUT_DIR": "reports/",
    "MEMORY_DIR": "docs/memory/",
    "DB_PATH": "db/",
    "DB_RETENTION_DAYS": 60
  },

  "LLM": {
    "PROVIDER": "mistral",
    "MODEL": null,
    "NON_INTERACTIVE": false,
    "PROBE_OLLAMA": true,
    "ENABLE_LEGACY_PROVIDERS": false,
    "MODEL_CACHE_TTL": 3600,
    "MISTRAL_MODEL": "mistral-small-latest",
    "MISTRAL_ENDPOINT": "https://api.mistral.ai/v1",
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "OLLAMA_MODEL": "llama3.1:8b"
  }
}
```

Решения:

- `GIT_PROJECT` **удаляется**; в коде не остаётся ни поля, ни валидатора, ни fallback.
- `SCAN.GIT_PROJECTS` — единственный список проектов для **обоих** режимов.
- `ANALYSIS.BRANCHES` — ветки, по которым собираются коммиты (`default` по умолчанию; `all` — как в `scan`).
- `GIT_DAY` переносится в `ANALYSIS`; в `scan` по-прежнему игнорируется.
- Плоские `MAX_DIFF_CHARS_PER_CHUNK`, `BACKOFF_FACTOR`, `OUTPUT_DIR` и т. п. — на один релиз читаются как deprecated-алиасы с предупреждением, затем удаляются.

### 2.2. Следствия для режимов

| Аспект | Было | Стало |
|--------|------|-------|
| Список проектов | 1 | N, порядок сохраняется, дубликаты по `url_key` устраняются |
| Выбор модели | 1 раз на запуск | 1 раз на запуск (не на проект) — экономия и единообразие отчёта |
| Транзакции БД | одна на запуск | одна на проект (как в `scan`), сбой одного не откатывает остальные |
| Отчёты | 1 файл | отчёт на проект + сводный индекс `reports/_summary_{date}.md` |
| Код выхода | 5 при любой ошибке | `5` — все проекты недоступны, `11` — часть проектов с ошибками |
| `report-from-db` | один проект | `--project` (можно несколько) или все проекты |

### 2.3. Что меняется в коде

- `Settings.git_projects: list[str]` + `ScanSettings`/`AnalysisSettings` как вложенные Pydantic-модели.
- Общий модуль `scan_git_push/git/url.py`: `normalize_url()`, `url_key()` — один код для `scan` и анализа (сейчас в доп. ТЗ описан, в основном коде отсутствует).
- `cli.py`: `--project` (repeatable) переопределяет `SCAN.GIT_PROJECTS`; `_extract_repo_name` остаётся только для имени файла отчёта.
- Репортёры получают `project` (url + короткое имя) и не зависят от глобальных настроек.

---

## 3. Оптимизация 2. Зеркала вместо GitHub API

### 3.1. Целевой поток

```
Шаг 0. Конфиг + PRAGMA + версия схемы (как в scan)
Шаг 1. Для каждого проекта (последовательно):
   1.1 git ls-remote --heads <url>            # доступность
   1.2 git clone --mirror | git fetch --prune --all --force
   1.3 git rev-list --since=<N days> --no-merges <ref>
   1.4 git diff-tree -r -z --numstat <sha>    # файлы, +/-, бинарность
   1.5 git diff-tree -r -p --unified=3 <sha>  # patch, с локальным лимитом
   1.6 дедупликация по (git_project_id, sha) через БД
   1.7 LLM-анализ (чанкинг) + запись в БД
Шаг 2. Отчёты по проектам + сводный
```

### 3.2. Таблица соответствия «API → git»

| Данные | Сейчас (PyGithub) | Стало (git CLI) |
|--------|-------------------|-----------------|
| Список коммитов за N дней | `repo.get_commits(since=...)` | `git rev-list --since="N days ago" --no-merges <ref>` |
| SHA / автор / дата / сообщение | `commit.*` | `git log --format=%H%x00%an%x00%ae%x00%aI%x00%cI%x00%B%x00 -z` |
| Merge-коммиты | `len(commit.parents) > 1` | флаг `--no-merges` |
| Список файлов + строки | `commit.files` (GET на коммит) | `git diff-tree -r -z --numstat --no-commit-id <sha>` |
| Patch | `file.patch` (усечён) | `git diff-tree -r -p --no-commit-id --unified=3 <sha>` |
| Бинарные файлы | `patch == ""` без причины | `--numstat` даёт `-\t-\t<path>` — пропуск с явной причиной |
| Ветка по умолчанию | подразумевается GitHub | `git symbolic-ref --short HEAD` в зеркале |
| Список веток | недоступно | `git for-each-ref --format='%(refname:short)' refs/heads` |

Ключевые детали реализации:

1. **Два прохода.** Сначала `rev-list` + `--numstat` (дешёво, без patch), затем patch — только для коммитов, которые действительно пойдут в LLM и проходят фильтр `ANALYSIS.EXCLUDE`. Это защищает от гигантских patch'ей вроде `package-lock.json`.
2. **Лимит на файл.** `MAX_PATCH_CHARS_PER_FILE` (по умолчанию 4000) с обрезкой по границе хунка и пометкой `... [diff truncated: N lines]`. Текущий `_chunk_diff` отправляет большой файл целиком — это надо убрать.
3. **Потоковое чтение.** `subprocess.Popen(..., stdout=PIPE, text=True, encoding="utf-8", errors="replace")` + чтение инкрементально; не собирать весь patch репозитория в память.
4. **Без shell.** Список аргументов, `GIT_TERMINAL_PROMPT=0`, `-c core.quotepath=false`, `-c core.longpaths=true` (Windows).
5. **Merge-политика.** `--no-merges` как сейчас. Опционально `--first-parent` для «линейного» взгляда на trunk-based репозитории (`ANALYSIS.FIRST_PARENT=false`).
6. **Дата.** `--since` в git по умолчанию использует **committer date**; фиксируем это в ТЗ (сейчас используется `commit.commit.author.date` — author date). Для «последних N дней пушей» правильнее committer date.
7. **Force push.** `git fetch --prune --all --force`: иначе зеркало падает на переписанной истории.
8. **Размер зеркала.** По умолчанию полное зеркало (нужны blobs для patch). Опция `SCAN.MIRROR_FILTER=blob:none` для больших репозиториев: partial clone, blob'ы подтягиваются лениво при `diff-tree -p`. Требует поддержки на сервере (GitHub поддерживает).

### 3.3. Выигрыш

| Показатель | API | Зеркало |
|------------|-----|---------|
| Сетевых запросов на 50 коммитов | 51 | 1 fetch (инкрементальный) |
| Сетевых запросов на 500 коммитов | 501 | 1 fetch |
| Время на 500 коммитов | 2–5 мин | 2–5 c локально |
| Лимит 5000/час | актуален | отсутствует |
| Полнота patch | усечён/отсутствует | полный (с локальным cap) |
| Приватный репозиторий | только token | ssh / credential helper / `GIT_ASKPASS` |
| Офлайн | нет | да (при наличии зеркала) |
| Не-GitHub хостинги | нет | да (GitLab, Bitbucket, локальные пути) |

### 3.4. Риски и меры

| Риск | Мера |
|------|------|
| Диск: зеркало хранит всю историю | `MIRROR_FILTER=blob:none`, предупреждение при размере > порога, `git gc --auto` |
| Первый clone большого репозитория | прогресс-бар, таймаут, `--filter=blob:none` |
| Force push / переписанная история | `fetch --force --prune` |
| Windows: длинные пути, антивирусные блокировки | `core.longpaths=true`, retry на `PermissionError` |
| Приватные репозитории | `GIT_ASKPASS`/credential helper; токен не попадает в URL и логи |
| Submodules / LFS | не разыменовываются; в patch виден gitlink/pointer — документируется |

---

## 4. Оптимизация 3. Объединение функций и алгоритмов

### 4.1. Сводная таблица «функция → как сейчас → как станет»

| Функция | Сейчас | Стало | Эффект |
|---------|--------|-------|--------|
| Доступ к репозиторию | `GitHubClient` (PyGithub) | `git/mirror.py` + `git/rev.py` | −1 зависимость, +скорость, +офлайн |
| Нормализация URL | нет | `git/url.py` (общий с `scan`) | стабильный `url_key`, нет дублей |
| Реестр проектов | нет (`repo_url` строкой) | `git_project` (общий DDL с `scan.db`) | единая идентичность проекта |
| Сканирование файлов | — | `scan` (уже есть) | переиспользуется в анализе для `LINK_REQUIREMENTS` |
| Чтение blob'ов | PyGithub | `git cat-file --batch` | пакетно, быстро |
| Дедупликация коммитов | `(sha, repo_url)` | `(git_project_id, sha)` | корректно при смене формы URL |
| Отчёты | 2 пути (`main`, `report-from-db`) | `ReportService` из БД | один код, отчёт за весь период |
| Версия схемы | нет | `schema_version` + код `12` | предсказуемость, как в `scan` |
| Пробы LLM | 4 провайдера всегда | лениво + кэш + `--model` | быстрый старт, автоматизация |
| Секреты | `GITHUB_TOKEN` всегда | только для выбранного LLM/приватного git | меньше трения |
| Память проекта | changelog на запуск | одна запись с итогами обоих режимов | меньше шума |

### 4.2. Единый `git`-слой

```
scan_git_push/git/
├── __init__.py
├── url.py          # normalize_url, url_key, is_local_path, sanitize_url
├── mirror.py       # MirrorManager: ensure(), fetch(), path_for(url_key)
├── rev.py          # list_commits(), numstat(), patch(), default_branch(), branches()
└── models.py       # CommitData, FileChange (перенос из github_client.py)
```

`scan` использует `mirror.py` + `rev.py` (деревья, blob'ы), анализ — `rev.py` (коммиты, diff). Один кэш `db/repos/<sha256(url_key)>.git` на проект для обоих режимов.

### 4.3. Объединённый запуск `scan` + анализ

Один fetch — два прохода:

```bash
python -m scan_git_push run --days 7        # scan → анализ → отчёты
python -m scan_git_push scan                # только маркеры (как в dop_tz01)
python -m scan_git_push analyze             # только коммиты (бывший main)
```

Это устраняет двойное обновление зеркала и даёт согласованное состояние: отчёт строится по тем же коммитам, для которых известно соответствие требованиям.

### 4.4. Единая отчётность

Сейчас `main` строит отчёт по `results` текущего запуска (`cli.py:425-450`), а `report-from-db` — отдельным кодом (`cli.py:546-571`). После объединения:

```python
rows = analysis_db.get_analyses(project_id, since=days)   # всегда из БД
results = [to_result(r) for r in rows]
report_service.generate(project, results, days)
```

- `analyze` = «собрать → проанализировать → сохранить → отчёт из БД»;
- `report-from-db` = «отчёт из БД» без первых двух шагов;
- повторный запуск без новых коммитов даёт полноценный отчёт, а не пустой.

Многопроектная выдача:

```
reports/
├── {project_a}_{date}.md / .html
├── {project_b}_{date}.md / .html
└── _summary_{date}.md / .html      # сводка по всем проектам, фильтр по проекту
```

### 4.5. Схема `analysis.db` (версия 1)

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS git_project (          -- тот же DDL, что в scan.db
    id         INTEGER PRIMARY KEY,
    url        TEXT NOT NULL,
    url_key    TEXT NOT NULL UNIQUE,
    author     TEXT,
    status     INTEGER NOT NULL DEFAULT 1 CHECK (status IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS analyzed_commits (
    id             INTEGER PRIMARY KEY,
    git_project_id INTEGER NOT NULL REFERENCES git_project(id) ON DELETE CASCADE,
    sha            TEXT    NOT NULL,
    commit_date    TEXT    NOT NULL,      -- committer date, ISO 8601 UTC
    analyzed_at    TEXT    NOT NULL,
    status         TEXT    NOT NULL DEFAULT 'success'
                           CHECK (status IN ('success', 'failed')),
    summary        TEXT,
    author         TEXT,
    committer      TEXT,
    message        TEXT,
    files_count    INTEGER NOT NULL DEFAULT 0,
    additions      INTEGER NOT NULL DEFAULT 0,
    deletions      INTEGER NOT NULL DEFAULT 0,
    chunks_count   INTEGER NOT NULL DEFAULT 0,
    provider       TEXT,
    model          TEXT,
    prompt_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (git_project_id, sha)
);

CREATE TABLE IF NOT EXISTS commit_files (
    commit_id   INTEGER NOT NULL REFERENCES analyzed_commits(id) ON DELETE CASCADE,
    path        TEXT    NOT NULL,
    file_status TEXT    NOT NULL,
    additions   INTEGER NOT NULL DEFAULT 0,
    deletions   INTEGER NOT NULL DEFAULT 0,
    is_binary   INTEGER NOT NULL DEFAULT 0 CHECK (is_binary IN (0, 1)),
    PRIMARY KEY (commit_id, path)
);

CREATE INDEX IF NOT EXISTS idx_commits_project_date
    ON analyzed_commits(git_project_id, commit_date);
```

Важно: `db/analysis.db` **уже существует** (28 КБ, 24.10.2026), поэтому, в отличие от `scan.db`, здесь нужна одноразовая миграция `legacy → 1`:

1. создать `git_project` из `SELECT DISTINCT repo_url` с вычислением `url_key`;
2. создать новые таблицы, перенести данные (`INSERT ... SELECT`), сопоставив `repo_url → git_project_id`;
3. переименовать старые таблицы в `*_legacy` и удалить после проверки (или оставить как бэкап);
4. записать `schema_version = 1`;
5. предусмотреть `--check-db`/`--rebuild-db` для ручного восстановления.

### 4.6. LLM-слой

| Проблема | Решение |
|----------|---------|
| 4 пробы на запуск | проверять `Mistral` только при наличии ключа, `Ollama` — если `PROBE_OLLAMA=true`; GitHub Models и Groq — только при `ENABLE_LEGACY_PROVIDERS=true` |
| Список моделей каждый раз | кэш в `db/llm_models.json` с TTL `MODEL_CACHE_TTL` |
| Интерактивный выбор | `--model provider/model`, `--non-interactive`; при `LLM.MODEL` в конфиге выбор не запрашивается |
| Повторный анализ другим моделью | `--reanalyze` + поля `provider`, `model`, `prompt_version` в БД |
| Много мелких коммитов | `ANALYSIS.BATCH_MAX_COMMITS` (по умолчанию 1 = выключено): группировать только коммиты с суммарным diff < 2000 символов |
| Последовательный анализ | `ANALYSIS.CONCURRENCY` (1 по умолчанию; 2–4 для Mistral, 1 для локальной Ollama) |

### 4.7. Прочие улучшения основного ТЗ

1. **`analyzer.py`**: убрать «файл больше лимита → отдельный чанк целиком», добавить `MAX_PATCH_CHARS_PER_FILE`; исправить `[Чась ...]` → `[Часть ...]`; вынести `SYSTEM_PROMPT` в версионируемый модуль (`PROMPT_VERSION`).
2. **`ANALYSIS.EXCLUDE`**: не отправлять в LLM lock-файлы, минифицированные бандлы, карты, снапшоты, `vendor/`, `dist/` — типично 30–70 % объёма diff в JS/фронтенд-репозиториях.
3. **Единая обработка ошибок**: `11` (частичный сбой) добавляется в основное ТЗ; ошибка одного коммита/проекта не роняет запуск.
4. **`--json`** для `analyze` и `report-from-db` (как в `scan`): автоматизация и CI.
5. **Логи**: `logs/analysis.log`, `rotation 10 MB`, `retention 30 days` — как в `scan`; сейчас только stdout.
6. **Память**: `MemoryManager.append_changelog` вызывать один раз на запуск с итогами по всем проектам; добавить в `STATE.md` счётчики scan/analysis.
7. **Коды выхода** (пересмотр раздела 6.1):

| Код | Значение после перехода на зеркала |
|-----|-------------------------------------|
| 1 | ошибка конфигурации/окружения (нет `git`, неверный путь) |
| 2 | требуется аутентификация git (401/403) или нет учётных данных для приватного репозитория |
| 3 | нет доступных LLM-моделей |
| 4 | неверные аргументы CLI |
| 5 | репозиторий недоступен (все проекты) |
| 6, 7 | зарезервированы (устаревшие провайдеры) |
| 8 | нет `MISTRAL_API_KEY` при выбранном Mistral |
| 9 | ошибка БД |
| 10 | зарезервирован под strict-валидацию (`scan`) |
| 11 | частичный сбой (часть проектов/коммитов) |
| 12 | несовместимая схема БД |

---

## 5. Новая возможность: связь коммитов с требованиями

Появляется только при совместном использовании зеркал и `scan`.

**Идея.** Для каждого проанализированного коммита известны изменённые пути. В `scan.db` для этих путей есть маркеры `REQ-META`. Значит, можно показать:

- какие требования/пункты затронуты коммитом;
- какие требования менялись за период (и кем);
- где маркер есть, а изменений по пункту не было (потенциальный «забытый» пункт);
- какие требования не имеют ни одного коммита за период (риск «мёртвых» требований).

**Реализация (Phase 3).**

```sql
ATTACH DATABASE 'db/scan.db' AS scan;

SELECT DISTINCT r.code, p.point, r.name
FROM scan.point_content pc
JOIN scan.point p ON p.id = pc.point_id
JOIN scan.req r   ON r.id = p.req_id
JOIN scan.file_snapshot fs ON fs.id = pc.file_id
JOIN scan.git_project gp   ON gp.id = r.git_project_id
WHERE gp.url_key = ?                 -- общий ключ проекта
  AND fs.path IN (...)               -- изменённые пути коммита
  AND p.status = 1 AND r.status = 1;
```

Требуется: одинаковый `url_key` в обеих БД (следствие единого `git/url.py`). Добавляется секция отчёта «Затронутые требования» и флаг `ANALYSIS.LINK_REQUIREMENTS`.

Ограничение: сопоставление идёт по пути и текущему состоянию tip, а не по историческому дереву коммита. Для точного соответствия «коммит → маркеры на момент коммита» нужен `git cat-file` по дереву коммита — это Phase 4, если понадобится.

---

## 6. Целевая структура проекта

```
scan_git_push/
├── cli.py                  # команды: run, scan, analyze, report-from-db
├── config.py               # Settings: ScanSettings, AnalysisSettings, LLMSettings
├── git/
│   ├── url.py              # normalize_url, url_key
│   ├── mirror.py           # MirrorManager (ensure/fetch/path)
│   ├── rev.py              # list_commits, numstat, patch, branches
│   └── models.py           # CommitData, FileChange
├── scan/                   # режим scan (dop_tz01)
│   ├── parser.py
│   ├── walker.py
│   └── service.py
├── analysis/
│   ├── collector.py        # коммиты из зеркала + фильтры
│   ├── chunker.py          # разбиение diff
│   ├── analyzer.py         # LLM-анализ
│   └── service.py          # оркестрация по проектам
├── db/
│   ├── scan_db.py
│   └── analysis_db.py
├── llm/                    # ollama, mistral (+ legacy за флагом)
├── reporters/              # console, markdown, html, summary
└── memory/manager.py
```

Удаляется: `github_client.py`, зависимость `PyGithub`.  
Остаётся: `typer`, `rich`, `jinja2`, `httpx`, `pydantic-settings`, `loguru`, `sqlite3`.

---

## 7. План внедрения

| Фаза | Содержание | Оценка | Риск |
|------|-----------|--------|------|
| 0 | Конфиг: `SCAN.GIT_PROJECTS`, `ANALYSIS`, `LLM`; удаление `GIT_PROJECT`; deprecated-алиасы; `git/url.py` | 0.5 дня | низкий |
| 1 | `git/mirror.py` + `git/rev.py`; замена `GitHubClient`; удаление PyGithub; тесты на временных репозиториях | 1.5–2 дня | средний (парсинг git) |
| 2 | `analysis.db` v1 + миграция существующей БД + `schema_version` | 1 день | средний (данные) |
| 3 | Слияние отчётности, мультипроектные отчёты, `--json` | 1 день | низкий |
| 4 | LLM: ленивые пробы, кэш моделей, `--model`, `--non-interactive`, `--reanalyze` | 0.5–1 день | низкий |
| 5 | `run` (scan + анализ на одном зеркале), `LINK_REQUIREMENTS` | 1–1.5 дня | средний |
| 6 | Обновление тестов, README, `docs/memory`, основного ТЗ | 0.5–1 день | низкий |

Итого: ~6–8 рабочих дней. Критический путь — фазы 1 и 2.

Порядок безопасного перехода:

1. Фаза 0 + 1: анализ продолжает работать, но уже через зеркала (функциональность 1:1).
2. Фаза 2: миграция БД с бэкапом `analysis.db.bak`.
3. Фаза 3–4: улучшения, не меняющие данные.
4. Фаза 5: новые возможности.

---

## 8. Что нужно изменить в основном ТЗ (`scan-git-push-TZ.md`)

| Раздел | Правка |
|--------|--------|
| 1. Цель | «анализ последних `GIT_DAY` коммитов в одном или нескольких git-проектах по локальным зеркалам» |
| 2. Стек | убрать `PyGithub`; добавить «git CLI (>= 2.30)»; `sqlite3` остаётся |
| 3. Конфигурация | новая вложенная структура `SCAN` / `ANALYSIS` / `LLM`; `GIT_PROJECT` удалён |
| 4. Аутентификация | `GITHUB_TOKEN` не обязателен; git-доступ через ssh/credential helper; LLM-токены — по выбранному провайдеру |
| 6. Ошибки | добавить `11` (частичный сбой), `12` (схема); уточнить `2` и `5` |
| 7.1. Сбор данных | зеркало + `rev-list`/`diff-tree`; committer date; многопроектность |
| 7.2. Chunking | `MAX_PATCH_CHARS_PER_FILE`; запрет «файл целиком одним чанком» |
| 7.3. Дедупликация | ключ `(git_project_id, sha)`, `url_key` вместо `repo_url` |
| 7.5. report-from-db | общий `ReportService`; `--project`, `--json` |
| 8. Отчёты | отчёт на проект + сводный индекс; фильтр по проекту в HTML |
| 10. Структура | `git/`, `scan/`, `analysis/`, `db/` |
| 11. Приёмка | многопроектность, зеркала, отсутствие обязательного `GITHUB_TOKEN`, `--non-interactive`, миграция БД |
| 12. НФТ | лимит patch на файл, потоковое чтение, `synchronous = NORMAL`, `busy_timeout` |

---

## 9. Обновлённые критерии приёмки (проект)

- [ ] `GIT_PROJECT` отсутствует в коде и конфиге; проекты берутся из `SCAN.GIT_PROJECTS`.
- [ ] Два и более проекта обрабатываются последовательно; отчёт формируется на каждый проект и сводный.
- [ ] Для каждого проекта создаётся/обновляется одно зеркало; повторный запуск не клонирует заново.
- [ ] Коммиты за `ANALYSIS.GIT_DAY` собираются локально, без обращений к GitHub API.
- [ ] Patch берётся из git и обрезается по `MAX_PATCH_CHARS_PER_FILE`.
- [ ] `PyGithub` отсутствует в зависимостях.
- [ ] Запуск без `GITHUB_TOKEN` работает для публичных репозиториев и Ollama.
- [ ] `--model`, `--non-interactive` позволяют запуск без интерактивного выбора.
- [ ] `report-from-db --project` формирует отчёт по выбранным проектам.
- [ ] Повторный запуск без новых коммитов формирует непустой отчёт за период.
- [ ] `analysis.db` версии `1`; миграция существующей БД выполняется с бэкапом.
- [ ] `schema_version` в `analysis.db`; при несовместимости — код `12`.
- [ ] Ошибка одного проекта не прерывает остальные; код `11`.
- [ ] Тесты используют временные git-репозитории, а не моки PyGithub.

---

## 10. Что менять не нужно

- `typer` + `rich` + `jinja2` — стек оптимален.
- Chunking по границам файлов — правильный подход, нужен только cap на файл.
- Исключение merge-коммитов — сохраняется (`--no-merges`).
- Артефактная память — сохраняется, меняется только granularity записи.
- Формат отчётов (MD + self-contained HTML) — сохраняется.
- Отдельные БД `scan.db` / `analysis.db` — сохраняются (требование доп. ТЗ), связь через `url_key` и `ATTACH`.

---

## 11. Открытые вопросы

1. **Ветки анализа:** только ветка по умолчанию (`default`) или все ветки (`all`) с дедупликацией по SHA? Предложение: `default`, `all` — по флагу.
2. **Объединённая команда `run`:** нужна ли сразу или достаточно двух раздельных (`scan` + `analyze`) на общем зеркале?
3. **`LINK_REQUIREMENTS`:** включать в эту итерацию (Phase 5) или отложить?
4. **Partial clone (`--filter=blob:none`):** включать по умолчанию для больших репозиториев или оставить опцией?
5. **Судьба `github_models`/`groq`:** удалить код полностью или оставить за флагом `ENABLE_LEGACY_PROVIDERS`?
6. **`ANALYSIS.EXCLUDE` по умолчанию:** согласовать список (lock-файлы, минифицированные бандлы, `dist/`, `vendor/`, `*.map`).
