"""Синхронизация sources/*.yaml в таблицу source."""
import os
import re
from pathlib import Path

import yaml
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Source

_ENV_RE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _expand_env(text: str) -> str:
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), text)


def load_source_files(directory: str | None = None) -> list[dict]:
    result = []
    for path in sorted(Path(directory or settings.sources_dir).glob("*.y*ml")):
        data = yaml.safe_load(_expand_env(path.read_text(encoding="utf-8")))
        if not data:
            continue
        if data.get("demo") and not settings.demo_enabled:
            continue
        data.setdefault("name", path.stem)
        result.append(data)
    return result


async def sync_sources(session: AsyncSession, directory: str | None = None) -> None:
    """YAML — источник истины для настроек парсинга; флаг enabled после первого импорта меняется в админке."""
    files = load_source_files(directory)
    for position, data in enumerate(files):
        values = {
            "name": data["name"],
            "title": data.get("title", data["name"]),
            "base_url": data["base_url"],
            "adapter": data.get("adapter", "generic"),
            "config": data.get("config", {}),
            "is_master": bool(data.get("is_master", False)),
            "position": data.get("position", position),
        }
        stmt = insert(Source).values(**values, enabled=bool(data.get("enabled", True)))
        stmt = stmt.on_conflict_do_update(index_elements=[Source.name], set_=values)
        await session.execute(stmt)
    # Источник, чей YAML удалён (или демо выключено), перестаёт обходиться и пропадает из каталога
    await session.execute(update(Source).where(Source.name.not_in([d["name"] for d in files]))
                          .values(enabled=False))
    await session.commit()
