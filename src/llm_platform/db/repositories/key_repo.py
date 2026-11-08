"""API Key 仓储：唯一 SQL 出口。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.entities import ApiKey


class KeyRepo:
    """API Key CRUD 与鉴权查询。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create_key(
        self,
        *,
        key_hash: str,
        key_prefix: str,
        tenant_id: int,
        project_id: int,
        name: str = "default",
        scopes: list[str] | None = None,
        rpm_limit: int = 60,
        tpm_limit: int = 60_000,
        expires_at: datetime | None = None,
    ) -> ApiKey:
        key = ApiKey(
            key_hash=key_hash,
            key_prefix=key_prefix,
            tenant_id=tenant_id,
            project_id=project_id,
            name=name,
            scopes=scopes or ["chat"],
            status=1,
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            expires_at=expires_at,
        )
        self.db.add(key)
        self.db.commit()
        self.db.refresh(key)
        return key

    def get_key_by_hash(self, key_hash: str) -> ApiKey | None:
        stmt = select(ApiKey).where(ApiKey.key_hash == key_hash)
        return self.db.execute(stmt).scalar_one_or_none()

    def get_key(self, key_id: int) -> ApiKey | None:
        return self.db.get(ApiKey, key_id)

    def list_keys(self, project_id: int, page: int = 1, page_size: int = 20) -> tuple[int, list[ApiKey]]:
        base = select(ApiKey).where(ApiKey.project_id == project_id)
        total = len(self.db.execute(base).scalars().all())
        stmt = base.offset((page - 1) * page_size).limit(page_size)
        items = list(self.db.execute(stmt).scalars().all())
        return total, items

    def revoke_key(self, key_id: int) -> ApiKey | None:
        key = self.get_key(key_id)
        if not key:
            return None
        key.status = 0
        key.revoked_at = datetime.now(UTC)
        self.db.commit()
        self.db.refresh(key)
        return key

    def update_last_used(self, key_id: int) -> None:
        key = self.get_key(key_id)
        if key:
            key.last_used_at = datetime.now(UTC)
            self.db.commit()
