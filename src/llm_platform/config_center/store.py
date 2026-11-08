"""配置存储：config_revision 表 + Redis pub/sub 热更新。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import ConfigError, NotFoundError
from ..logging import get_logger
from ..models.entities import ConfigRevision

logger = get_logger("config_center.store")

VALID_NAMESPACES = {"routing", "limit", "canary", "alert"}


class ConfigStore:
    """配置版本管理：全量快照 + 单调递增 version。"""

    def __init__(self, db: Session, redis_client=None) -> None:
        self.db = db
        self.redis = redis_client

    def get_current(self, namespace: str) -> dict[str, Any] | None:
        """获取指定命名空间的最新已应用配置。"""
        stmt = (
            select(ConfigRevision)
            .where(ConfigRevision.namespace == namespace)
            .where(ConfigRevision.status == "applied")
            .order_by(ConfigRevision.version.desc())
            .limit(1)
        )
        rev = self.db.execute(stmt).scalar_one_or_none()
        return rev.payload if rev else None

    def put(self, namespace: str, payload: dict[str, Any], created_by: str) -> ConfigRevision:
        """提交全量配置快照，version+1，发布 Redis pub/sub 广播。

        Raises:
            ConfigError: namespace 非法。
        """
        if namespace not in VALID_NAMESPACES:
            raise ConfigError(
                message=f"非法配置命名空间: {namespace}",
                valid_namespaces=list(VALID_NAMESPACES),
            )

        # 获取当前最大版本号
        stmt = (
            select(ConfigRevision.version)
            .where(ConfigRevision.namespace == namespace)
            .order_by(ConfigRevision.version.desc())
            .limit(1)
        )
        max_version = self.db.execute(stmt).scalar() or 0

        rev = ConfigRevision(
            namespace=namespace,
            version=max_version + 1,
            payload=payload,
            status="applied",
            created_by=created_by,
            applied_at=datetime.now(UTC),
        )
        self.db.add(rev)
        self.db.commit()
        self.db.refresh(rev)

        # Redis pub/sub 广播（订阅方 ≤5s 生效）
        self._broadcast(namespace, payload)

        logger.info("config_applied", namespace=namespace, version=rev.version)
        return rev

    def list_revisions(self, namespace: str) -> list[ConfigRevision]:
        """变更历史。"""
        stmt = (
            select(ConfigRevision)
            .where(ConfigRevision.namespace == namespace)
            .order_by(ConfigRevision.version.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def rollback(self, namespace: str, created_by: str) -> ConfigRevision:
        """回滚至上一版本（等价于以旧 payload 再 PUT 一次）。"""
        revisions = self.list_revisions(namespace)
        if len(revisions) < 2:
            raise NotFoundError(message="无可回滚的历史版本", namespace=namespace)

        prev_payload = revisions[1].payload  # 上一个版本
        return self.put(namespace, prev_payload, created_by)

    def _broadcast(self, namespace: str, payload: dict[str, Any]) -> None:
        if self.redis:
            try:
                self.redis.publish(f"cfg:changed:{namespace}", str(payload))  # type: ignore[union-attr]
            except Exception as exc:
                logger.warning("config_broadcast_failed", error=str(exc))
