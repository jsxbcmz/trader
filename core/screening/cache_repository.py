from __future__ import annotations

from pathlib import Path

from core.data.base_json_repository import BaseJsonRepository

from .cache_models import ScreeningCacheEntry

CACHE_DIR_NAME = "screening_cache"
CACHE_FILE_NAME = "screening_cache.json"


class ScreeningCacheRepository(BaseJsonRepository):
    def __init__(self, root: Path):
        self.root = Path(root)
        super().__init__(self.root / CACHE_DIR_NAME / CACHE_FILE_NAME)

    def load_all(self) -> list[ScreeningCacheEntry]:
        raw = self._read_json()
        if raw is None:
            return []
        try:
            return [ScreeningCacheEntry.from_dict(item) for item in raw]
        except (KeyError, TypeError):
            return []

    def save_all(self, entries: list[ScreeningCacheEntry]) -> None:
        payload = [entry.to_dict() for entry in entries]
        self._write_json(payload)

    def find(
        self,
        target_date: str,
        template_id: str,
        tdx_source_hash: str,
    ) -> ScreeningCacheEntry | None:
        """根据缓存键查找匹配的缓存条目"""
        for entry in self.load_all():
            if (
                entry.target_date == target_date
                and entry.template_id == template_id
                and entry.tdx_source_hash == tdx_source_hash
            ):
                return entry
        return None

    def upsert(self, entry: ScreeningCacheEntry) -> None:
        """插入或更新缓存条目（按缓存键匹配）"""
        entries = self.load_all()
        replaced = False
        for index, existing in enumerate(entries):
            if (
                existing.target_date == entry.target_date
                and existing.template_id == entry.template_id
                and existing.tdx_source_hash == entry.tdx_source_hash
            ):
                entries[index] = entry
                replaced = True
                break
        if not replaced:
            entries.append(entry)
        self.save_all(entries)