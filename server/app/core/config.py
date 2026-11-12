"""运行配置。

所有配置项都可以用 ``FLOWMASK_`` 前缀的环境变量覆盖，默认值面向本机演示：
数据写在 ``server/data``，不依赖任何外部服务；L2/L3 默认关闭（模型未接入时
识别链会自动降级为 L1-only，并在结果里标记 ``degraded``）。
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .paths import default_data_dir

# 源码运行 = server/；打成 exe 后 = 用户目录（%LOCALAPPDATA%\\FlowMask）
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = default_data_dir()


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(f"FLOWMASK_{name}")
    return default if value is None or value == "" else value


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(f"FLOWMASK_{name}")
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(f"FLOWMASK_{name}")
    if value is None or value == "":
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(f"FLOWMASK_{name}")
    if value is None or value == "":
        return default
    return int(value)


THEMES = ("light", "dark", "system")


def _parse_theme(value: str) -> str:
    """主题值只有三种；配错了退回 system，避免一个笔误让前端起不来。"""
    normalized = (value or "").strip().lower()
    return normalized if normalized in THEMES else "system"


LEVEL_VALUES = ("SAFE", "L1", "L2", "L3")


def _parse_level(value: str) -> str:
    """告警阈值只接受四级；配错退回 L1（不因一个笔误把告警全关掉或起不来）。"""
    normalized = (value or "").strip().upper()
    return normalized if normalized in LEVEL_VALUES else "L1"


AUDIT_SECRET_FILE = "audit_secret.key"
EXPORT_SECRET_FILE = "export_secret.key"


def _load_secret(data_dir: Path, filename: str, env_name: str) -> tuple[str, str]:
    """确定一个本地密钥，返回 ``(密钥, 来源)``。

    优先级：环境变量 → ``<data_dir>/<filename>`` → 首次启动自动生成并落盘 →
    落盘失败时退回进程内存随机值。

    不再内置固定演示密钥：固定密钥等于把签名私钥写在源码里，任何拿到源码的人都能
    伪造一整条自洽的审计链。自动生成的密钥跟随数据目录，重启后仍能校验历史记录。
    """
    explicit = os.environ.get(env_name, "").strip()
    if explicit:
        return explicit, "env"

    path = data_dir / filename
    try:
        stored = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        stored = ""
    except OSError:
        # 目录不可读/被占用：不阻塞启动，代价是重启后旧签名验不过，需人工处理
        return secrets.token_hex(32), "memory"
    if stored:
        return stored, "file"

    generated = secrets.token_hex(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(generated + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # Windows 上 chmod 语义有限，失败不影响功能
    except OSError:
        return generated, "memory"
    return generated, "generated"


def _load_audit_secret(data_dir: Path) -> tuple[str, str]:
    """审计哈希链签名密钥（``FLOWMASK_AUDIT_SECRET``）。"""
    return _load_secret(data_dir, AUDIT_SECRET_FILE, "FLOWMASK_AUDIT_SECRET")


def _load_export_secret(data_dir: Path) -> tuple[str, str]:
    """脱敏导出用的密钥（哈希加盐 / 伪名 / 可逆加密，``FLOWMASK_EXPORT_SECRET``）。

    与审计密钥分开：导出密钥泄露不应影响审计链签名，反之亦然。
    """
    return _load_secret(data_dir, EXPORT_SECRET_FILE, "FLOWMASK_EXPORT_SECRET")


@dataclass(frozen=True)
class Settings:
    """一份不可变的运行配置快照。"""

    data_dir: Path
    db_path: Path
    # 留空表示不鉴权（本机演示默认）；部署时设置 Bearer Token
    api_token: str
    # 审计哈希链签名密钥（HMAC-SHA256）；优先 FLOWMASK_AUDIT_SECRET，否则自动生成并落盘
    audit_secret: str
    # 密钥来源：env（环境变量）/ file（已有密钥文件）/ generated（本次首次生成）/ memory（未持久化）
    audit_secret_source: str
    # 脱敏导出密钥（哈希加盐 / 伪名 / 可逆加密）；优先 FLOWMASK_EXPORT_SECRET，否则自动生成并落盘
    export_secret: str
    export_secret_source: str
    # 动态脱敏：可查看原文的操作人白名单（逗号分隔）；空表示都只能看脱敏副本
    reveal_operators: str
    enable_l2: bool
    enable_l3: bool
    l2_endpoint: str
    l3_endpoint: str
    l3_model: str
    # L3 调用方式：http（本仓 l3_service）/ ollama（/api/generate）
    l3_mode: str
    l2_timeout_s: float
    l3_timeout_s: float
    max_upload_mb: int
    cors_origins: tuple[str, ...]
    # L1.5 实体识别（人名/地址），默认关闭：开启会额外拉起一个 NER 模型
    enable_ner: bool
    ner_model_dir: str
    ner_device: str
    # 前端默认主题：light / dark / system（system 表示跟随操作系统外观）。
    # 用户在界面里手动选过之后，个人偏好存在库里并优先于这个默认值。
    default_theme: str
    # 告警触发阈值：检测结果级别 ≥ 该值才生成告警（L3/L2/L1/SAFE）。
    # 默认 L1：所有敏感命中都进告警队列；演示"只看高危"可设 FLOWMASK_ALERT_MIN_LEVEL=L3。
    alert_min_level: str
    # 第三方插件：逗号分隔的 ``module:register`` 入口；空表示只用内置插件。
    plugin_modules: str
    # 图片 OCR（可选依赖 requirements-ocr.txt）：默认关闭，未安装时接口返回明确提示
    ocr_enabled: bool
    # OCR 输入图的最大边长（像素），过大先缩放，避免 CPU 推理过慢
    ocr_max_side: int
    # 本地 AI 助手：rules（默认，纯本地规则）/ ollama（本机模型生成，失败自动降级 rules）
    assistant_enabled: bool
    assistant_mode: str
    assistant_endpoint: str
    assistant_model: str
    assistant_timeout_s: float

    @classmethod
    def load(cls) -> "Settings":
        data_dir = Path(_env_str("DATA_DIR", str(DEFAULT_DATA_DIR))).resolve()
        db_path = Path(_env_str("DB_PATH", str(data_dir / "flowmask.db"))).resolve()
        origins = _env_str(
            "CORS_ORIGINS",
            "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8080",
        )
        audit_secret, audit_secret_source = _load_audit_secret(data_dir)
        export_secret, export_secret_source = _load_export_secret(data_dir)
        return cls(
            data_dir=data_dir,
            db_path=db_path,
            api_token=_env_str("API_TOKEN", ""),
            audit_secret=audit_secret,
            audit_secret_source=audit_secret_source,
            export_secret=export_secret,
            export_secret_source=export_secret_source,
            reveal_operators=_env_str("REVEAL_OPERATORS", ""),
            enable_l2=_env_bool("ENABLE_L2", False),
            enable_l3=_env_bool("ENABLE_L3", False),
            l2_endpoint=_env_str("L2_ENDPOINT", "http://127.0.0.1:8091/v1/classify"),
            l3_endpoint=_env_str("L3_ENDPOINT", "http://127.0.0.1:8092/v1/judge"),
            l3_model=_env_str("L3_MODEL", "qwen3guard-gen-0.6b"),
            l3_mode=_env_str("L3_MODE", "http").strip().lower(),
            l2_timeout_s=_env_float("L2_TIMEOUT_S", 0.5),
            l3_timeout_s=_env_float("L3_TIMEOUT_S", 5.0),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", 20),
            cors_origins=tuple(x.strip() for x in origins.split(",") if x.strip()),
            enable_ner=_env_bool("ENABLE_NER", False),
            ner_model_dir=_env_str("NER_MODEL_DIR", ""),
            ner_device=_env_str("NER_DEVICE", "cpu").strip().lower(),
            default_theme=_parse_theme(_env_str("DEFAULT_THEME", "system")),
            alert_min_level=_parse_level(_env_str("ALERT_MIN_LEVEL", "L1")),
            plugin_modules=_env_str("PLUGINS", ""),
            ocr_enabled=_env_bool("OCR_ENABLED", False),
            ocr_max_side=_env_int("OCR_MAX_SIDE", 1600),
            assistant_enabled=_env_bool("ASSISTANT_ENABLED", True),
            assistant_mode=_env_str("ASSISTANT_MODE", "rules").strip().lower(),
            assistant_endpoint=_env_str("ASSISTANT_ENDPOINT", "http://127.0.0.1:11434/api/generate"),
            assistant_model=_env_str("ASSISTANT_MODEL", "qwen2.5:1.5b"),
            assistant_timeout_s=_env_float("ASSISTANT_TIMEOUT_S", 5.0),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例（测试里用 ``get_settings.cache_clear()`` 重置）。"""
    return Settings.load()
