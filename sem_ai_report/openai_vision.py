# -*- coding: utf-8 -*-
"""OpenAI Responses API：SEM 图像结构化 JSON 分析。"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from openai import OpenAI

from prompts import ai_sem_json_instructions, experiment_recommendations_instructions
from utils import SEM_AI_JSON_KEYS

OPENAI_VISION_MODEL = os.environ.get("OPENAI_SEM_VISION_MODEL", "gpt-4o-mini")


def _responses_output_text(response: Any) -> str:
    """从 Responses 返回对象提取文本。"""
    out = getattr(response, "output_text", None)
    if isinstance(out, str) and out.strip():
        return out.strip()
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for c in getattr(item, "content", []) or []:
            if getattr(c, "type", None) == "output_text":
                parts.append(getattr(c, "text", "") or "")
    text = "\n".join(p for p in parts if p)
    return text.strip()


def extract_json_object_from_model_text(text: str) -> dict[str, Any] | None:
    """解析模型输出中的 JSON（兼容 ```json 围栏）。"""
    raw = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if m:
        raw = m.group(1).strip()
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    i0 = raw.find("{")
    i1 = raw.rfind("}")
    if i0 >= 0 and i1 > i0:
        try:
            obj = json.loads(raw[i0 : i1 + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def normalize_sem_ai_json(raw: dict[str, Any] | None, image_name: str) -> dict[str, str]:
    """补齐键；image_name 以参数为准。"""
    out: dict[str, str] = {}
    for k in SEM_AI_JSON_KEYS:
        if k == "image_name":
            continue
        val = ""
        if raw and k in raw and raw[k] is not None:
            val = str(raw[k]).strip()
        out[k] = val if val else "（待人工确认）"
    out["image_name"] = image_name
    return out


def call_openai_vision_sem_json(
    api_key: str,
    image_name: str,
    data_url_png: str,
    user_context: str,
    has_scale_hint: bool,
) -> dict[str, str]:
    """
    单张图调用 Responses API；解析失败或 API 异常时返回占位字典，不向外抛异常。
    """
    client = OpenAI(api_key=api_key)
    sys_text = ai_sem_json_instructions()
    scale_clause = (
        "用户声称已填写比例尺/像素信息，但你仍不得编造微米级测量值；仅可作定性辅助。"
        if has_scale_hint
        else "用户未提供可靠比例尺信息：禁止给出真实尺寸或粒径数值，只能定性描述。"
    )
    user_text = (
        f"当前图像文件名 image_name 必须为：{image_name}\n"
        f"{scale_clause}\n"
        "请仅依据图像可见内容填写 JSON 各字段。\n"
        f"用户提供的非图像文字背景（禁止当作你在图中测量到的结果）：\n{user_context}\n"
    )
    user_payload = [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": user_text},
                {"type": "input_image", "image_url": data_url_png},
            ],
        }
    ]
    try:
        try:
            response = client.responses.create(
                model=OPENAI_VISION_MODEL,
                instructions=sys_text,
                input=user_payload,
            )
        except TypeError:
            merged = sys_text + "\n\n" + user_text
            response = client.responses.create(
                model=OPENAI_VISION_MODEL,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": merged},
                            {"type": "input_image", "image_url": data_url_png},
                        ],
                    }
                ],
            )
        merged_text = _responses_output_text(response)
        if not merged_text:
            base = normalize_sem_ai_json(None, image_name)
            base["uncertainty_and_manual_check"] = (
                "模型返回空文本，无法解析 JSON；请检查模型版本或重试。（待人工确认）"
            )
            return base
        parsed = extract_json_object_from_model_text(merged_text)
        if parsed is None:
            base = normalize_sem_ai_json(None, image_name)
            snippet = merged_text[:800].replace("\n", " ")
            base["uncertainty_and_manual_check"] = (
                f"模型输出无法解析为 JSON，请人工核对原始回复片段：{snippet}…（待人工确认）"
            )
            return base
        norm = normalize_sem_ai_json(parsed, image_name)
        norm["image_name"] = image_name
        return norm
    except Exception as e:  # noqa: BLE001
        base = normalize_sem_ai_json(None, image_name)
        base["uncertainty_and_manual_check"] = f"API 调用或解析过程异常：{e}（待人工确认）"
        base["visible_morphology"] = "（因 API 异常未完成模型推断，待人工确认）"
        return base


def call_openai_experiment_recommendations(api_key: str, context_text: str) -> str:
    """
    文本模式 Responses：下一轮实验建议（Markdown）。失败返回说明字符串，不抛异常。
    """
    client = OpenAI(api_key=api_key)
    sys_text = experiment_recommendations_instructions()
    user_payload = [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": context_text[:12000]}],
        }
    ]
    try:
        try:
            response = client.responses.create(
                model=OPENAI_VISION_MODEL,
                instructions=sys_text,
                input=user_payload,
            )
        except TypeError:
            merged = sys_text + "\n\n" + context_text[:12000]
            response = client.responses.create(
                model=OPENAI_VISION_MODEL,
                input=[
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": merged}],
                    }
                ],
            )
        out = _responses_output_text(response)
        return out if out.strip() else "（模型返回为空，待人工确认）"
    except Exception as e:  # noqa: BLE001
        return (
            f"（下一轮实验建议 API 调用失败：{e}；请查看本地模板建议或重试。待人工确认）"
        )
