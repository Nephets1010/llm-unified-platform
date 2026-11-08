"""配置热更新 Watcher：Redis pub/sub 订阅 + 版本轮询兜底（≤5s 生效）。

配置消费方经 register_callback 订阅，变更时 notify_change 回调（ADR-008）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..logging import get_logger

logger = get_logger("config_center.watcher")

# 配置回调注册表：namespace -> list[callback]
_callbacks: dict[str, list[Callable[[dict], None]]] = {}

# 版本号缓存（轮询兜底）
_last_versions: dict[str, int] = {}


def register_callback(namespace: str, callback: Callable[[dict], None]) -> None:
    """注册配置变更回调（订阅方 ≤5s 生效）。"""
    if namespace not in _callbacks:
        _callbacks[namespace] = []
    _callbacks[namespace].append(callback)
    logger.info("config_callback_registered", namespace=namespace)


def notify_change(namespace: str, payload: dict[str, Any]) -> None:
    """通知所有订阅方配置变更。"""
    for cb in _callbacks.get(namespace, []):
        try:
            cb(payload)
        except Exception as exc:
            logger.warning("config_callback_error", namespace=namespace, error=str(exc))


class ConfigWatcher:
    """配置变更监听器（Redis pub/sub + 版本轮询兜底）。"""

    def __init__(self, redis_client=None) -> None:
        self.redis = redis_client
        self._running = False

    async def start(self) -> None:
        """启动 Redis pub/sub 监听。"""
        if not self.redis:
            logger.warning("config_watcher_no_redis")
            return

        self._running = True
        try:
            pubsub = self.redis.pubsub()  # type: ignore[union-attr]
            channels = [f"cfg:changed:{ns}" for ns in ("routing", "limit", "canary", "alert")]
            await pubsub.subscribe(*channels)  # type: ignore[union-attr]
            logger.info("config_watcher_started", channels=channels)

            while self._running:
                try:
                    message = await asyncio.wait_for(pubsub.get_message(), timeout=5.0)  # type: ignore[union-attr]
                    if message and message.get("type") == "message":
                        channel = message["channel"]
                        if isinstance(channel, bytes):
                            channel = channel.decode()
                        ns = channel.replace("cfg:changed:", "")
                        raw = message.get("data", b"")
                        if isinstance(raw, bytes):
                            import json
                            try:
                                payload = json.loads(raw)
                            except Exception:
                                payload = {}
                        else:
                            payload = raw if isinstance(raw, dict) else {}
                        notify_change(ns, payload)
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:
                    logger.warning("config_watcher_message_error", error=str(exc))
                    await asyncio.sleep(1)

        except Exception as exc:
            logger.warning("config_watcher_error", error=str(exc))

    def stop(self) -> None:
        self._running = False

    def poll_versions(self, store) -> None:  # noqa: ANN001
        """版本号轮询兜底（定时调用，检测变更并 notify）。"""
        for ns in ("routing", "limit", "canary", "alert"):
            try:
                revisions = store.list_revisions(ns)
                if not revisions:
                    continue
                latest = revisions[0].version
                old = _last_versions.get(ns, 0)
                if latest > old:
                    _last_versions[ns] = latest
                    payload = store.get_current(ns) or {}
                    notify_change(ns, payload)
                    logger.info("config_polled_change", namespace=ns, version=latest)
            except Exception as exc:
                logger.warning("config_poll_error", namespace=ns, error=str(exc))
