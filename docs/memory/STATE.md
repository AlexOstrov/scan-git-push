# Текущее состояние проекта

## Текущая задача
Разработка утилиты scan-git-push: анализ git push изменений с LLM-анализатором

## Реализованы
- ТЗ актуализировано (полное, непротиворечивое, реализуемое)
- Базовая конфигурация проекта (opencode.json, config.json)

## Архитектурные решения
- Python 3.11+ с Typer + Rich + Pydantic Settings
- LLM провайдеры: Ollama + GitHub Models API (бесплатный тариф)
- Вывод: консоль (rich) + Markdown + интерактивный HTML
- Retry с exponential backoff (3 попытки, factor 2)
- Chunking больших diff по границам файлов (лимит 12000 символов)
- Артефактная память в docs/memory/ с формат STATE.md

## Следующие шаги
1. Создать структуру проекта (pyproject.toml, каталоги)
2. Реализовать конфигурацию (config.py с pydantic-settings)
3. Реализовать GitHub-клиент с retry (github_client.py)
4. Реализовать LLM-провайдеры (ollama.py, github_models.py)
5. Реализовать анализатор коммитов с chunking (analyzer.py)
6. Реализовать репортёры (console.py, markdown.py, html.py)
7. Реализовать менеджер памяти (memory/manager.py)
8. Написать тесты

## Последнее обновление
2026-10-18
