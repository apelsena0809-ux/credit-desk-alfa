#!/usr/bin/env python3
"""Генерация OpenAPI-спецификации из FastAPI-приложения (api.py).

Использование:
    python scripts/gen_openapi.py [путь_к_выходной_папке]

Создаёт openapi.json и openapi.yaml (если установлен PyYAML).
Спецификация пересоздаётся при изменении api.py; в репозитории хранится
сгенерированная копия openapi.yaml для просмотра без запуска сервиса.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import api  # noqa: E402


def main() -> None:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT
    spec = api.app.openapi()
    json_path = out_dir / 'openapi.json'
    json_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'OK: {json_path}')
    try:
        import yaml
    except ImportError:
        print('PyYAML не установлен — openapi.yaml пропущен (доступен openapi.json).')
        return
    yaml_path = out_dir / 'openapi.yaml'
    yaml_path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(f'OK: {yaml_path}')


if __name__ == '__main__':
    main()
