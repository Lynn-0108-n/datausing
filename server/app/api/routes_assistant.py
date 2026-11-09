"""AI 助手路由：统计解读 + 方案推荐。

设计要点：
- **入参用 ``dict = Body(...)`` 而不是直接声明 Pydantic 模型**：避免 FastAPI 把
  ``ValidationError`` 直接转成 422 并回显 ``exc.errors()``（含字段路径，可能泄露请求结构）；
- **手动 ``model_validate`` + 捕获 ValidationError**：返回 **400** + 固定文案
  「请求格式不正确」，不拼接错误详情；
- **只接收聚合计数 / 用途标签**：路由层不接触原文 / masked / path，从源头杜绝泄露。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import ValidationError

from ..assistant import advise, recommend
from ..core.config import Settings
from .deps import settings_dep
from .schemas import AssistantAdviseIn, AssistantAdviseOut, RecommendIn, RecommendOut

router = APIRouter(tags=["assistant"])


@router.post(
    "/assistant/advise",
    response_model=AssistantAdviseOut,
    summary="AI 解读：聚合计数",
)
def assistant_advise(
    payload: dict = Body(...),
    settings: Settings = Depends(settings_dep),
) -> AssistantAdviseOut:
    """统计解读：输入聚合计数 → 输出总体评估 + 建议。

    只接收聚合计数与类别分布，**不接收原文 / masked / path**；
    默认走规则引擎，配置 ``ASSISTANT_MODE=ollama``（仅限本机回环）后可启用模型增强。
    """
    try:
        parsed = AssistantAdviseIn.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请求格式不正确",
        ) from exc
    return advise(parsed, settings)


@router.post(
    "/assistant/recommend",
    response_model=RecommendOut,
    summary="AI 推荐脱敏方案",
)
def assistant_recommend(
    payload: dict = Body(...),
    settings: Settings = Depends(settings_dep),
) -> RecommendOut:
    """方案推荐：输入用途 + 输出要求 → 输出推荐策略 + 理由。

    纯规则引擎匹配（写死的推荐表），不调模型；
    输入只含用途标签与要求标签，不含任何原文。
    """
    try:
        parsed = RecommendIn.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请求格式不正确",
        ) from exc
    return recommend(parsed, settings)
