"""审计链密钥来源测试：环境变量 / 密钥文件 / 首次生成 / 内存兜底。

背景：旧实现把 ``FLOWMASK_AUDIT_SECRET`` 的缺省值硬编码成 ``flowmask-demo-secret``，
等于把 HMAC 私钥写在源码里——任何拿到源码的人都能伪造一条自洽的审计链。
现在的口径是：环境变量优先；否则用数据目录下的 ``audit_secret.key``；
首次启动自动生成 64 位随机 hex 并落盘；连落盘都失败时退回进程内存随机值。
"""

from __future__ import annotations

import re

import pytest

from app.audit import append_entry, canonical_hash, verify_chain
from app.core.config import AUDIT_SECRET_FILE, get_settings

HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: 升级前写进源码的演示密钥；只用于验证「旧链需要显式指定旧密钥」的兼容路径。
LEGACY_DEMO_SECRET = "flowmask-demo-secret"


def _load_settings():
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture()
def generated_key_env(monkeypatch, tmp_env):
    """清掉环境变量覆盖，强制走「密钥文件 / 首次生成」分支。"""
    monkeypatch.delenv("FLOWMASK_AUDIT_SECRET", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _append_one(db, *, task_id: str) -> None:
    append_entry(
        db,
        task_id=task_id,
        input_hash=canonical_hash({"i": task_id}),
        output_hash=canonical_hash({"o": task_id}),
        model_chain="l1",
        confidence_vector="[]",
        rule_id="idcard18",
    )


def test_env_secret_wins(monkeypatch, generated_key_env) -> None:
    monkeypatch.setenv("FLOWMASK_AUDIT_SECRET", "from-env-secret")
    settings = _load_settings()
    assert settings.audit_secret == "from-env-secret"
    assert settings.audit_secret_source == "env"


def test_first_start_generates_key_file(generated_key_env) -> None:
    settings = _load_settings()
    assert settings.audit_secret_source == "generated"
    assert HEX64.match(settings.audit_secret), settings.audit_secret

    path = settings.data_dir / AUDIT_SECRET_FILE
    assert path.is_file(), "首次启动应把生成的密钥写入数据目录"
    assert path.read_text(encoding="utf-8").strip() == settings.audit_secret


def test_key_file_is_reused_after_reload(generated_key_env) -> None:
    first = _load_settings()
    assert first.audit_secret_source == "generated"

    get_settings.cache_clear()
    second = get_settings()
    assert second.audit_secret_source == "file"
    assert second.audit_secret == first.audit_secret, "重启后必须复用同一个密钥，否则旧链全部验不过"


def test_existing_key_file_is_used(generated_key_env, tmp_path) -> None:
    key_dir = tmp_path / "data"
    key_dir.mkdir(parents=True, exist_ok=True)
    (key_dir / AUDIT_SECRET_FILE).write_text("my-own-key\n", encoding="utf-8")

    settings = _load_settings()
    assert settings.audit_secret == "my-own-key"
    assert settings.audit_secret_source == "file"


def test_blank_env_secret_falls_back_to_file(monkeypatch, generated_key_env) -> None:
    """``.env`` 里留空的 FLOWMASK_AUDIT_SECRET 不能变成空密钥。"""
    monkeypatch.setenv("FLOWMASK_AUDIT_SECRET", "   ")
    settings = _load_settings()
    assert settings.audit_secret_source in {"generated", "file"}
    assert HEX64.match(settings.audit_secret)


def test_verify_chain_accepts_generated_secret(database) -> None:
    _append_one(database, task_id="fresh")
    result = verify_chain(database)
    assert result["ok"] is True
    assert result["total"] == 1
    # 新密钥下，用演示密钥「猜」是验不过的
    assert verify_chain(database, secret=LEGACY_DEMO_SECRET)["ok"] is False


def test_legacy_demo_secret_chain_needs_old_key(monkeypatch, database) -> None:
    """升级前用演示密钥签的历史记录：新密钥验不过，显式给旧密钥才行。"""
    monkeypatch.setenv("FLOWMASK_AUDIT_SECRET", LEGACY_DEMO_SECRET)
    get_settings.cache_clear()
    _append_one(database, task_id="legacy")

    monkeypatch.delenv("FLOWMASK_AUDIT_SECRET")
    get_settings.cache_clear()

    result = verify_chain(database)
    assert result["ok"] is False
    assert "签名" in result["reason"]

    compatible = verify_chain(database, secret=LEGACY_DEMO_SECRET)
    assert compatible["ok"] is True and compatible["total"] == 1


def test_unwritable_key_file_falls_back_to_memory(monkeypatch, tmp_path) -> None:
    """数据目录不可写时不能起不来：退回内存随机密钥（代价是重启后旧链验不过）。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("FLOWMASK_DATA_DIR", str(blocker / "data"))
    monkeypatch.setenv("FLOWMASK_DB_PATH", str(blocker / "data" / "flowmask.db"))
    monkeypatch.delenv("FLOWMASK_AUDIT_SECRET", raising=False)
    try:
        settings = _load_settings()
        assert settings.audit_secret_source == "memory"
        assert HEX64.match(settings.audit_secret), settings.audit_secret
    finally:
        get_settings.cache_clear()
