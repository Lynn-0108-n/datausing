"""JSON 文件解析：把对象/数组里的每个字符串值抽成 Segment。

路径用 ``$`` 开头的简化 JSONPath（``$.user.phone``、``$.items[0].name``），
前端左侧高亮与右侧脱敏结果都靠这个路径对齐。
"""

from __future__ import annotations

import json
import re

from .base import Document, ParseError, Segment


def _walk(node: object, path: str, out: list[Segment], hint: str = "", location=()) -> None:
    if isinstance(node, (str, int, float)) and not isinstance(node, bool):
        value = str(node)
        if value.strip():
            out.append(Segment(path=path, text=value, hint=hint, location=location))
        return
    if isinstance(node, dict):
        for key, value in node.items():
            # 键名本身不参与脱敏，但作为"字段名上下文"传给检测层：
            # {"api_key":"sk-..."} 能命中密钥规则，{"name":"张三"} 能命中姓名字段规则
            suffix = '.' + key if re.fullmatch(r'\w+', key) else '[' + json.dumps(key, ensure_ascii=False) + ']'
            _walk(value, path + suffix, out, str(key), location + (key,))
        return
    if isinstance(node, list):
        for index, value in enumerate(node):
            _walk(value, f"{path}[{index}]", out, hint, location + (index,))


def parse_json(text: str, source: str = "data.json") -> Document:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ParseError(f"JSON 解析失败：{exc}") from exc

    segments: list[Segment] = []
    _walk(data, "$", segments)
    if not segments:
        raise ParseError("JSON 中没有可检测的字符串值")
    return Document(
        source=source,
        content_type="json",
        segments=segments,
        raw=text,
        meta={"root_type": type(data).__name__},
    )
