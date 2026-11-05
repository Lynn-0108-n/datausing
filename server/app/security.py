"""识别与输出之间的安全边界：失败关闭，只记录稳定错误码和随机关联号。"""

from __future__ import annotations

import logging
import uuid

from fastapi.responses import JSONResponse

POLICY_VERSION = "protected-v1"
logger = logging.getLogger(__name__)


class SecurityError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.error_id = uuid.uuid4().hex
        super().__init__(code)


async def security_error_response(_request, exc: SecurityError):
    logger.warning("%s error_id=%s", exc.code, exc.error_id)
    return JSONResponse(status_code=exc.status_code, content={
        "detail": exc.message, "code": exc.code, "error_id": exc.error_id,
    })


def protected_spans(text: str, spans: list[dict]) -> list[dict]:
    """验证并合并交叠范围；不能因忽略无效位置或重叠尾部而输出原值。"""
    ordered = []
    for span in spans:
        start, end = span.get('start'), span.get('end')
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(text)):
            raise SecurityError('MASK_LOCATION_INVALID', '脱敏位置无效，请重新检测')
        ordered.append(dict(span))
    merged = []
    for span in sorted(ordered, key=lambda item: (item['start'], item['end'])):
        if merged and span['start'] < merged[-1]['end']:
            merged[-1]['end'] = max(merged[-1]['end'], span['end'])
        else:
            merged.append(span)
    return merged


def protected_text(text: str, finding, requested_level=None) -> str:
    """持久化和未授权视图至少partial；高危或更强请求使用block。"""
    from .identify.masking import mask_spans_with_strategy
    from .identify.types import Level

    spans = protected_spans(text, finding.spans)
    if not spans:
        return '[已屏蔽:敏感信息]'  # 有类别证据但无精确位置时，不沿用可能含原值的副本
    high = finding.level == Level.HIGH or requested_level == Level.HIGH
    try:
        return mask_spans_with_strategy(text, spans, 'block' if high else 'partial')
    except SecurityError:
        raise
    except Exception:
        raise SecurityError('MASKING_FAILED', '脱敏处理失败，请重新检测') from None
