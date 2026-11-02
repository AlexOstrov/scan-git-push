# scan-git-push

Анализ git-коммитов с помощью LLM: локальные зеркала, несколько проектов, отчёты в консоль, Markdown и автономный HTML.

## Возможности

- Анализ последних `ANALYSIS.GIT_DAY` коммитов в одном или нескольких проектах.
- Работа через локальные git-зеркала — без GitHub API и обязательного `GITHUB_TOKEN`.
- Все ветки с дедупликацией по SHA (настраивается через `ANALYSIS.BRANCHES`).
- Ollama (локально) и Mistral AI (онлайн, OpenAI-совместимый endpoint).
- Интерактивный выбор модели, `--model`, `--non-interactive`, кэш моделей.
- Chunking больших diff по файлам и хункам, лимит патча на файл.
- Исключение lock-файлов, минифицированных бандлов, `dist/`, `vendor/`, `*.map`.
- Дедупликация коммитов в SQLite (`analysis.db`, схема v1), миграция старой БД с бэкапом.
- Повторная генерация отчётов без LLM: `report-from-db`.
- Связь коммитов с требованиями из `scan.db` (`LINK_REQUIREMENTS`).
- Режим `scan`: поиск маркеров `<!-- REQ-META ... -->` в `*.txt`/`*.md` и ведение `db/scan.db` без LLM.
- Отчёт на каждый проект + сводный `_summary_{date}`.
- Retry с exponential backoff.
- Артефактная память для продолжения работы между сессиями.

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e .
```

Требуется Python 3.11+ и git 2.30+.

## Конфигурация

`config.json` состоит из трёх секций:

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
    "DB_PATH": "db/analysis.db",
    "OUTPUT_DIR": "reports/"
  },
  "LLM": {
    "PROVIDER": "mistral",
    "MISTRAL_MODEL": "mistral-small-latest",
    "NON_INTERACTIVE": false
  }
}
```

Файл секретов:

```ini
GITHUB_TOKEN=...       # необязателен
MISTRAL_API_KEY=...    # нужен только для провайдера mistral
```

Относительные пути разрешаются от каталога `config.json`. Полное описание — в `scan-git-push-TZ.md`; режим `scan` — в `dop_tz01.md`.

## Использование

```bash
# анализ проектов из SCAN.GIT_PROJECTS
python -m scan_git_push

# явный выбор модели и периода
python -m scan_git_push analyze --days 14 --model mistral/mistral-small-latest

# только выбранные проекты, машиночитаемый результат
python -m scan_git_push analyze --project d:/projects/repo-b --json

# повторный анализ всех коммитов периода
python -m scan_git_push analyze --reanalyze

# отчёты из БД без LLM
python -m scan_git_push report-from-db --days 30

# поиск маркеров требований в файлах (без LLM)
python -m scan_git_push scan

# только выбранные ветки/проекты, дополнительный include
python -m scan_git_push scan --project d:/projects/repo-b --branch main --include "docs/**/*.rst"

# проверка без записи БД и строгая валидация маркеров
python -m scan_git_push scan --dry-run --strict

# устаревший алиас, печатает JSON
python -m scan_git_push --scan --json
```

Коды выхода: `0` успех, `1` конфигурация, `2` аутентификация git, `3` нет моделей, `4` аргументы, `5` проекты недоступны, `8` нет `MISTRAL_API_KEY`, `9` БД, `10` ошибки маркеров в `--strict`, `11` частичный успех, `12` схема БД. Для `scan` приоритет кодов: `12 → 9 → 5 → 11 → 10 → 0`.

### Режим `scan`

`scan` читает tip выбранных веток через общее зеркало и сохраняет в `db/scan.db` проекты, снимки файлов, требования, пункты и вхождения маркеров. Содержимое файлов не хранится — только SHA-256 (`blob_hash`) и размер.

Поддерживается только канонический маркер:

```html
<!-- REQ-META id="REQ_1" point="1.1" name="Название требования" -->
```

Старые форматы (`ID_REQ`, `ID_EXAMPLE`, маркер без `-->`) не разбираются и дают предупреждение `[WARN] legacy marker format`. По умолчанию сканируются `*.txt` и `*.md`; symlink, submodule и LFS-указатели не разыменовываются. `--dry-run` не создаёт и не изменяет БД, `--force` снимает зависший `running`-запуск.

## Отчёты

```
reports/{repo}_{YYYYMMDD}.md
reports/{repo}_{YYYYMMDD}.html
reports/_summary_{YYYYMMDD}.md
reports/_summary_{YYYYMMDD}.html
```

HTML полностью автономен: встроенные CSS/JS, без CDN.

## Тестирование

```bash
pytest -q
```

Тесты используют временные git-репозитории и не обращаются к сети.
