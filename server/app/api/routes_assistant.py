"""本地 AI 助手与使用场景模板接口。

端点分工：

- ``GET  /assistant/health``  助手是否可用（rules 模式恒可用，ollama 模式探测本机服务）
- ``GET  /assistant/models``  本机模型列表（ollama /api/tags）
- ``POST /assistant/suggest`` 按场景/字段名/计数推荐脱敏方案（输入不含敏感值）
- ``POST /assistant/advise``  按聚合统计生成风险研判
- ``GET  /scenarios``         场景模板清单（设置页与检测页共用）

所有端点都不落库、不改状态；ollama 不可用时 Assistant 内部自动回退本地规则，
接口仍返回 200，避免本机模型服务把检测页拖垮。

路由约定：本模块的 ``router`` 不带前缀，和 ``routes_detect`` 等现有模块保持一致；
main.py 应把它纳入现有的 ``APIRouter(prefix="/api/v1", dependencies=[...])``
外层路由（同一个 include_router 循环），这样 /api/v1 前缀、鉴权依赖只保留一份。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..assistant import Assistant
from ..core.config import Settings
from ..scenarios import get_scenario, list_scenarios, scenario_to_out
from .deps import settings_dep
from .schemas import (
    AssistantAdviseIn,
    AssistantAdviseOut,
    AssistantHealthOut,
    AssistantModelsOut,
    AssistantSuggestionOut,
    AssistantSuggestIn,
    ScenarioOut,
    ScenariosOut,
)

router = APIRouter(tags=["assistant"])


def _build_assistant(settings: Settings) -> Assistant:
    """按请求构造助手；构造不联网，健康检查/模型列表也只是短超时探测。"""
    return Assistant(settings)


@router.get("/assistant/health", response_model=AssistantHealthOut, summary="本地 AI 助手状态")
def assistant_health(settings: Settings = Depends(settings_dep)) -> AssistantHealthOut:
    return AssistantHealthOut(**_build_assistant(settings).health())


@router.get("/assistant/models", response_model=AssistantModelsOut, summary="本机模型列表")
def assistant_models(settings: Settings = Depends(settings_dep)) -> AssistantModelsOut:
    return AssistantModelsOut(**_build_assistant(settings).models())


@router.post("/assistant/suggest", response_model=AssistantSuggestionOut, summary="推荐脱敏方案")
def assistant_suggest(
    payload: AssistantSuggestIn,
    settings: Settings = Depends(settings_dep),
) -> AssistantSuggestionOut:
    """未知场景直接 400：让前端把场景键拼错的问题暴露在开发期，而不是静默套默认值。"""
    if payload.scenario and get_scenario(payload.scenario) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未知场景")
    suggestion = _build_assistant(settings).suggest(payload.model_dump())
    return AssistantSuggestionOut(**suggestion)


@router.post("/assistant/advise", response_model=AssistantAdviseOut, summary="风险研判")
def assistant_advise(
    payload: AssistantAdviseIn,
    settings: Settings = Depends(settings_dep),
) -> AssistantAdviseOut:
    advice = _build_assistant(settings).advise(payload.model_dump())
    return AssistantAdviseOut(**advice)


@router.get("/scenarios", response_model=ScenariosOut, summary="使用场景模板")
def scenarios() -> ScenariosOut:
    items = list_scenarios()
    return ScenariosOut(
        scenarios=[ScenarioOut(**scenario_to_out(item)) for item in items],
        total=len(items),
    )
