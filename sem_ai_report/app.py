# -*- coding: utf-8 -*-
"""
AI 辅助 SEM 图像分析与实验报告生成 — Streamlit 入口。
界面按七个步骤分页展示（Streamlit tabs + 自定义 stepper 样式）。
"""

from __future__ import annotations

import base64
import html
import json
from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd
import streamlit as st
from PIL import Image

from docx_exporter import make_docx_bytes
from image_metrics import compute_image_metrics, load_uploaded_image
from openai_vision import (
    call_openai_experiment_recommendations,
    call_openai_vision_sem_json,
    normalize_sem_ai_json,
)
from report_generator import (
    build_full_report_text,
    build_local_template_report,
    build_markdown_document,
    local_next_round_fallback_markdown,
)
from utils import (
    AI_IMAGE_MAX_SIDE,
    PROXY_METRIC_TERMS_BULLETS,
    PROXY_METRIC_TERMS_TITLE,
    SEM_AI_JSON_KEYS,
    get_openai_api_key,
    image_to_png_data_url,
    save_run_outputs,
)

APP_TITLE = "AI 辅助 SEM 图像分析与实验报告生成系统"

ALLOWED_EXTENSIONS = {"tif", "tiff", "png", "jpg", "jpeg"}

STAGE_OPTIONS = [
    "原料粉体",
    "绿体/压坯",
    "中断烧结样品",
    "终态烧结样品",
    "循环后样品",
]

SESSION_RUN = "sem_last_run"

STAGE_HINTS_MD = """
| 阶段 | 建议关注（定性） |
|------|------------------|
| 原料粉体 | 颗粒尺寸、团聚、形状 |
| 绿体/压坯 | 压实均匀性、孔隙分布 |
| 中断烧结样品 | 颈部长大、孔隙演化 |
| 终态烧结样品 | 晶粒、致密化、残余孔 |
| 循环后样品 | 裂纹、破碎、表面退化 |
"""

METRICS_COL_CN: dict[str, str] = {
    "file_name": "文件名",
    "width": "宽度(px)",
    "height": "高度(px)",
    "mean_gray": "平均灰度",
    "std_gray": "灰度标准差",
    "contrast": "反差(proxy)",
    "sharpness_laplacian_var": "清晰度·Laplacian方差(proxy)",
    "edge_density": "边缘密度(proxy)",
    "dark_area_ratio": "暗区面积比(proxy)",
}

SEM_AI_COL_CN: dict[str, str] = {
    "image_name": "图像",
    "visible_morphology": "可见形貌（定性）",
    "particle_shape_observation": "颗粒/形态观察",
    "agglomeration_observation": "团聚现象",
    "pore_or_dark_region_observation": "孔隙/暗区观察",
    "crack_observation": "裂纹观察",
    "texture_uniformity": "纹理均匀性",
    "quality_issues": "成像/样品质量问题",
    "possible_research_meaning": "可能的科研含义（假设）",
    "uncertainty_and_manual_check": "不确定性与人工复核要点",
}

METRICS_GLOSSARY_MD = """
| 指标 | 解释 |
|------|------|
| mean_gray | 灰度均值，反映整体亮暗 |
| std_gray / contrast | 反映灰度离散程度与反差 |
| sharpness_laplacian_var | 清晰度 proxy，用于判断失焦风险 |
| edge_density | 边缘丰富程度 proxy，**不等同于**颗粒边界数量 |
| dark_area_ratio | 暗区面积比例 proxy，**不能直接称为孔隙率** |
| width / height | 像素尺寸，**不等于**实际物理尺寸 |
"""

SCALE_MODE_NONE = "未提供"
SCALE_MODE_BAR = "比例尺 scale bar"
SCALE_MODE_PIXEL = "pixel size"

MAG_PRESETS = ["500×", "1000×", "2000×", "5000×", "10000×", "20000×", "50000×", "自定义"]

ATMOSPHERE_OPTIONS = ["空气", "氧气", "氮气", "氩气", "真空", "其他/自定义"]

SCALE_BAR_UNIT_KEYS: tuple[str, ...] = ("nm", "μm", "mm")
SCALE_BAR_UNIT_DISPLAY = {
    "nm": "纳米 (nm)",
    "μm": "微米 (μm)",
    "mm": "毫米 (mm)",
}

PIXEL_SIZE_UNIT_KEYS: tuple[str, ...] = ("nm/pixel", "μm/pixel")
PIXEL_SIZE_UNIT_DISPLAY = {
    "nm/pixel": "nm/pixel（纳米/像素）",
    "μm/pixel": "μm/pixel（微米/像素）",
}

# 工艺参数第一行：与第二行一致采用「副标题 + 带标签主控件」布局（下拉替代勾选）
PROC_FILL_STRUCTURED = "结构化（数值+单位）"
PROC_FILL_CUSTOM = "自定义文本"
PROC_HEATING_SKIP = "未提供"
PROC_HEATING_FILL = "填写升温速率"


def _proc_subtitle(text: str) -> None:
    """工艺表单内小节标题（与 sem-process-subtitle 样式一致）。"""
    st.markdown(
        f'<p class="sem-process-subtitle">{html.escape(text)}</p>',
        unsafe_allow_html=True,
    )


def _normalize_mu_unit_label(u: str) -> str:
    return u.strip().replace("um", "μm").replace("UM", "μm").replace("µm", "μm")


def _format_temperature(custom: bool, custom_txt: str, num: float | None, unit: str | None) -> str:
    if custom:
        if not (custom_txt or "").strip():
            return "（自定义未填）"
        return (
            (custom_txt or "")
            .strip()
            .replace("°C", "℃")
            .replace("℃", "℃")
        )
    if num is None:
        return "—"
    u = unit or "℃"
    val = int(num) if float(num).is_integer() else num
    return f"{val} {u}"


def _format_sinter_time(custom: bool, custom_txt: str, num: float | None, unit: str | None) -> str:
    if custom:
        if not (custom_txt or "").strip():
            return "（自定义未填）"
        return (custom_txt or "").strip()
    if num is None:
        return "—"
    u = unit or "h"
    val = int(num) if float(num).is_integer() else num
    return f"{val} {u}"


def _format_heating_rate(use: bool, num: float | None, unit: str | None) -> str:
    if not use:
        return "未提供"
    if num is None:
        return "未提供"
    u = unit or "℃/min"
    val = int(num) if float(num).is_integer() else num
    return f"{val} {u}"


def _format_magnification(sel: str, custom_input: str) -> str:
    if sel != "自定义":
        return sel
    raw = (custom_input or "").strip().replace(" ", "").replace("x", "×").replace("X", "×")
    if not raw:
        return "（自定义未填）"
    if raw.endswith("×"):
        return raw
    core = raw.rstrip("×")
    if core.isdigit():
        return f"{core}×"
    return raw if "×" in raw else f"{raw}×"


def _format_atmosphere(sel: str, custom_txt: str) -> str:
    if sel == "其他/自定义":
        t = (custom_txt or "").strip()
        return t if t else "其他（未填）"
    return sel


def _format_scale_info_row(
    mode: str,
    sb_val: float | None,
    sb_unit: str,
    sb_note: str,
    ps_val: float | None,
    ps_unit: str,
    ps_note: str,
) -> tuple[str, bool]:
    """(写入报告的 scale_info 字符串, 是否视为已提供可用于提示的比例尺/pixel size)。"""
    if mode == SCALE_MODE_NONE:
        return "未提供", False
    if mode == SCALE_MODE_BAR:
        parts: list[str] = []
        if sb_val is None or sb_val <= 0:
            parts.append("scale bar: （数值未填）")
            return " | ".join(parts), False
        u = _normalize_mu_unit_label(sb_unit or "μm")
        val = int(sb_val) if float(sb_val).is_integer() else sb_val
        parts.append(f"scale bar: {val} {u}")
        if (sb_note or "").strip():
            parts.append(f"备注: {(sb_note or '').strip()}")
        return " | ".join(parts), True
    if mode == SCALE_MODE_PIXEL:
        parts = []
        if ps_val is None or ps_val <= 0:
            parts.append("pixel size: （数值未填）")
            return " | ".join(parts), False
        u = _normalize_mu_unit_label(ps_unit or "nm/pixel")
        val = int(ps_val) if float(ps_val).is_integer() else ps_val
        parts.append(f"pixel size: {val} {u}")
        if (ps_note or "").strip():
            parts.append(f"备注: {(ps_note or '').strip()}")
        return " | ".join(parts), True
    return "未提供", False


def _inject_sem_ui_styles() -> None:
    """深色玻璃拟态科研控制台（仅样式；不改变业务逻辑）。"""
    st.markdown(
        """
<style>
    /* ========== 字体全局 ========== */
    html, body, .stApp, button, input, textarea, select {
        font-family: Inter, "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif !important;
    }
    h1, h2, h3 {
        font-family: Inter, "Microsoft YaHei", sans-serif !important;
        letter-spacing: -0.02em;
        color: #f8fafc !important;
    }

    /* ========== 页面深蓝黑背 + 光斑 ========== */
    .stApp {
        color: #cbd5e1 !important;
        font-size: 14px;
        line-height: 1.55;
        background:
            radial-gradient(circle at top left, rgba(88, 80, 236, 0.22), transparent 32%),
            radial-gradient(circle at top right, rgba(20, 184, 166, 0.18), transparent 28%),
            linear-gradient(135deg, #050816 0%, #0b1020 45%, #071827 100%) !important;
    }
    [data-testid="stAppViewContainer"],
    [data-testid="stHeader"],
    section[data-testid="stMain"],
    section[data-testid="stMain"] > div {
        background: transparent !important;
    }

    /* ========== 主容器玻璃外壳 ========== */
    .main .block-container {
        max-width: 1120px;
        margin-left: auto !important;
        margin-right: auto !important;
        padding: 18px 22px 28px !important;
        background: rgba(15, 23, 42, 0.52);
        backdrop-filter: blur(14px);
        -webkit-backdrop-filter: blur(14px);
        border: 1px solid rgba(148, 163, 184, 0.22);
        border-radius: 24px;
        box-shadow:
            0 20px 60px rgba(0, 0, 0, 0.45),
            inset 0 1px 0 rgba(255, 255, 255, 0.05);
    }

    /* ========== 顶部 Header（固定网格，切换 tab 不重排） ========== */
    .sem-console-header {
        margin-bottom: 14px;
        padding-bottom: 12px;
        border-bottom: 1px solid rgba(148, 163, 184, 0.14);
        contain: layout style;
        width: 100%;
        max-width: 100%;
        box-sizing: border-box;
    }
    .sem-window-controls {
        display: flex;
        gap: 7px;
        align-items: center;
        margin-bottom: 10px;
    }
    .sem-dot {
        width: 11px;
        height: 11px;
        border-radius: 999px;
        flex-shrink: 0;
        box-shadow: inset 0 -1px 2px rgba(0,0,0,0.35);
    }
    .sem-dot-r { background: #ff5f57; }
    .sem-dot-y { background: #ffbd2e; }
    .sem-dot-g { background: #28c840; }
    .sem-header-grid {
        display: grid;
        grid-template-columns: minmax(280px, 1fr) minmax(320px, 760px);
        align-items: center;
        gap: 24px;
        width: 100%;
        max-width: 100%;
        box-sizing: border-box;
    }
    .sem-header-left {
        min-width: 0;
    }
    .sem-header-right {
        width: 100%;
        max-width: 760px;
        min-width: 0;
        justify-self: end;
        box-sizing: border-box;
    }
    .sem-status-chip-row {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 12px;
        flex-wrap: nowrap;
        width: 100%;
        min-height: 44px;
        box-sizing: border-box;
    }
    .sem-status-chip {
        height: 42px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        white-space: nowrap;
        padding: 0 18px;
        border-radius: 999px;
        font-size: 13px !important;
        font-weight: 800 !important;
        line-height: 1 !important;
        letter-spacing: 0.08em !important;
        text-transform: uppercase;
        flex-shrink: 0;
        box-sizing: border-box;
        border: 1px solid rgba(148, 163, 184, 0.28);
        background: rgba(15, 23, 42, 0.72);
        color: #cbd5e1 !important;
        font-family: Inter, "Microsoft YaHei", sans-serif !important;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.05);
    }
    .sem-status-chip-accent {
        border-color: rgba(94, 234, 212, 0.42);
        color: #5eead4 !important;
        background: rgba(20, 184, 166, 0.14);
    }
    @media (max-width: 1080px) {
        .sem-header-grid {
            grid-template-columns: 1fr;
        }
        .sem-header-right {
            justify-self: stretch;
            max-width: 100%;
        }
        .sem-status-chip-row {
            flex-wrap: wrap;
            justify-content: flex-start;
        }
    }
    .sem-head-title {
        margin: 0;
        font-size: 1.35rem;
        font-weight: 720;
        color: #f8fafc !important;
        line-height: 1.28;
        letter-spacing: -0.02em;
    }
    .sem-head-sub {
        margin: 6px 0 0 0;
        font-size: 0.8125rem;
        color: #94a3b8 !important;
        line-height: 1.5;
        max-width: 640px;
    }

    /* 工艺表单：防止右侧裁切 */
    .sem-process-grid [data-testid="column"] {
        min-width: 0 !important;
    }

    /* 工艺表单小节标题（与 Widget label 区分，统一字号） */
    .sem-process-subtitle {
        font-size: 13px !important;
        font-weight: 650 !important;
        color: #e2e8f0 !important;
        margin: 0 0 8px 0 !important;
        letter-spacing: 0.02em;
        line-height: 1.35 !important;
        font-family: Inter, "Microsoft YaHei", sans-serif !important;
    }

    /* 工艺区多列：窄屏自动折行，减轻控件挤压重叠 */
    .sem-process-responsive [data-testid="stHorizontalBlock"] {
        gap: 0.75rem 1rem !important;
        align-items: flex-start !important;
    }
    @media (max-width: 900px) {
        .sem-process-responsive [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
        }
        .sem-process-responsive [data-testid="stHorizontalBlock"] > div[data-testid="column"] {
            flex: 1 1 calc(50% - 0.5rem) !important;
            min-width: min(100%, 252px) !important;
        }
    }
    @media (max-width: 520px) {
        .sem-process-responsive [data-testid="stHorizontalBlock"] > div[data-testid="column"] {
            flex: 1 1 100% !important;
            min-width: 100% !important;
        }
    }

    /* ========== Stepper Tabs（CSS 计数器统一编号圆点） ========== */
    .stTabs [data-baseweb="tab-list"] {
        counter-reset: sem-step 0;
        gap: 8px !important;
        padding: 10px 12px !important;
        flex-wrap: wrap !important;
        background: rgba(15, 23, 42, 0.55) !important;
        border: 1px solid rgba(148, 163, 184, 0.18) !important;
        border-radius: 999px !important;
        backdrop-filter: blur(10px);
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.04);
    }
    .stTabs [data-baseweb="tab"] {
        counter-increment: sem-step !important;
        border-radius: 999px !important;
        padding: 8px 14px 8px 10px !important;
        min-height: 0 !important;
        font-size: 13px !important;
        font-weight: 560 !important;
        color: #94a3b8 !important;
        background: transparent !important;
        border: 1px solid transparent !important;
        gap: 8px !important;
        display: inline-flex !important;
        align-items: center !important;
        margin: 0 !important;
        letter-spacing: 0.01em;
    }
    .stTabs [data-baseweb="tab"]::before {
        content: counter(sem-step);
        width: 22px !important;
        height: 22px !important;
        border-radius: 999px !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        font-size: 13px !important;
        font-weight: 700 !important;
        font-family: Inter, "Microsoft YaHei", sans-serif !important;
        line-height: 1 !important;
        flex-shrink: 0 !important;
        background: rgba(51, 65, 85, 0.85);
        color: #cbd5e1 !important;
        border: 1px solid rgba(148, 163, 184, 0.22);
    }
    .stTabs [aria-selected="true"] {
        color: #f8fafc !important;
        font-weight: 650 !important;
        background: rgba(99, 102, 241, 0.14) !important;
        border: 1px solid rgba(167, 139, 250, 0.35) !important;
        box-shadow:
            0 0 24px rgba(99, 102, 241, 0.18),
            inset 0 1px 0 rgba(255,255,255,0.06) !important;
    }
    .stTabs [aria-selected="true"]::before {
        background: linear-gradient(135deg, #6366f1, #14b8a6) !important;
        color: #fff !important;
        border-color: transparent !important;
        box-shadow: 0 4px 14px rgba(99, 102, 241, 0.45);
    }
    .stTabs [data-baseweb="tab-highlight"] {
        background: transparent !important;
    }
    .stTabs [data-baseweb="tab-panel"] {
        padding-top: 12px !important;
    }

    /* ========== 通用玻璃卡片 ========== */
    .sem-card {
        background: rgba(15, 23, 42, 0.72);
        border: 1px solid rgba(148, 163, 184, 0.2);
        border-radius: 20px;
        padding: 22px;
        margin-bottom: 12px;
        box-shadow:
            inset 0 1px 0 rgba(255, 255, 255, 0.04),
            0 18px 40px rgba(0, 0, 0, 0.3);
        backdrop-filter: blur(14px);
        -webkit-backdrop-filter: blur(14px);
    }
    .sem-card-title {
        font-size: 0.8125rem;
        font-weight: 700;
        color: #5eead4 !important;
        margin: 0 0 10px 0;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    /* Tab 内容区标题：与正文间距略收紧（统一替代散落的 inline margin） */
    p.sem-card-title.sem-lead-tight {
        margin-bottom: 0.35rem !important;
    }
    p.sem-card-title.sem-lead-table {
        margin-bottom: 10px !important;
    }
    .sem-card-muted, .sem-card p {
        font-size: 14px !important;
        line-height: 1.75 !important;
        color: #cbd5e1 !important;
        margin: 0;
    }
    .sem-card-muted strong { color: #a78bfa !important; }

    /* ========== 小号 info 卡片 ========== */
    .sem-info-lite {
        font-size: 13px !important;
        line-height: 1.65 !important;
        color: #94a3b8 !important;
        padding: 12px 14px;
        border-radius: 14px;
        border: 1px solid rgba(148, 163, 184, 0.18);
        background: rgba(30, 41, 59, 0.45);
        margin-bottom: 10px;
    }

    /* ========== 流程条 ========== */
    .sem-flow-bar {
        font-size: 13px !important;
        letter-spacing: 0.02em;
        color: #94a3b8 !important;
        padding: 12px 16px;
        border-radius: 14px;
        border: 1px dashed rgba(94, 234, 212, 0.28);
        background: rgba(15, 23, 42, 0.45);
        margin-top: 6px;
    }
    .sem-flow-bar b { color: #5eead4 !important; font-weight: 600; }

    /* ========== 列表统一（避免杂乱 marker） ========== */
    .sem-card ol, .sem-card ul,
    .main .block-container ol, .main .block-container ul {
        margin-left: 1.2rem !important;
        padding-left: 0.25rem !important;
    }
    .sem-card li, .main .block-container li {
        font-size: 14px !important;
        line-height: 1.75 !important;
        color: #cbd5e1 !important;
    }
    .sem-card li::marker, .main .block-container li::marker {
        color: #5eead4 !important;
        font-weight: 700 !important;
        font-size: 13px !important;
    }

    /* ========== 按钮 ========== */
    div[data-testid="stButton"] button[kind="primary"] {
        background: linear-gradient(135deg, #6366f1, #14b8a6) !important;
        color: #fff !important;
        border: none !important;
        border-radius: 999px !important;
        font-weight: 700 !important;
        padding: 0.48rem 1.35rem !important;
        box-shadow: 0 10px 30px rgba(99, 102, 241, 0.35) !important;
    }
    div[data-testid="stButton"] button[kind="primary"]:hover {
        box-shadow: 0 12px 34px rgba(99, 102, 241, 0.42) !important;
        filter: brightness(1.05);
    }
    div[data-testid="stButton"] button[kind="secondary"] {
        background: rgba(30, 41, 59, 0.55) !important;
        color: #e2e8f0 !important;
        border: 1px solid rgba(148, 163, 184, 0.35) !important;
        border-radius: 999px !important;
        font-weight: 600 !important;
    }

    /* ========== 表单控件深色 ========== */
    label[data-testid="stWidgetLabel"] p {
        font-size: 13px !important;
        color: #94a3b8 !important;
        font-weight: 550 !important;
    }
    .stTextInput input, .stTextArea textarea,
    div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
        background-color: rgba(30, 41, 59, 0.72) !important;
        color: #f1f5f9 !important;
        border-color: rgba(148, 163, 184, 0.22) !important;
        border-radius: 12px !important;
        font-size: 14px !important;
    }
    .stNumberInput input {
        font-variant-numeric: tabular-nums !important;
        font-size: 14px !important;
    }
    [data-baseweb="textarea"] {
        border-radius: 12px !important;
    }
    .stCheckbox label span {
        color: #cbd5e1 !important;
        font-size: 13px !important;
    }
    [data-testid="stFileUploader"] section {
        padding: 18px !important;
        border: 2px dashed rgba(148, 163, 184, 0.32) !important;
        border-radius: 18px !important;
        background: rgba(15, 23, 42, 0.48) !important;
    }
    [data-testid="stFileUploader"] section:hover {
        border-color: rgba(167, 139, 250, 0.45) !important;
        box-shadow: 0 0 28px rgba(99, 102, 241, 0.12);
    }

    /* ========== DataFrame / 表格区域 ========== */
    .sem-table-wrap {
        padding: 16px;
        border-radius: 18px;
        border: 1px solid rgba(148, 163, 184, 0.18);
        background: rgba(15, 23, 42, 0.55);
        margin-bottom: 10px;
    }
    [data-testid="stDataFrame"] {
        border-radius: 12px !important;
        overflow: hidden !important;
    }

    /* ========== Alert / expander ========== */
    div[data-testid="stAlert"] {
        border-radius: 14px !important;
        background: rgba(30, 41, 59, 0.65) !important;
        border: 1px solid rgba(148, 163, 184, 0.2) !important;
        color: #cbd5e1 !important;
    }
    .streamlit-expanderHeader {
        background: rgba(30, 41, 59, 0.72) !important;
        border-radius: 12px !important;
        color: #e2e8f0 !important;
        font-size: 13px !important;
        border: 1px solid rgba(148, 163, 184, 0.15);
    }
    .streamlit-expanderContent {
        background: transparent !important;
        color: #cbd5e1 !important;
    }

    /* ========== Markdown 表格（指标释义） ========== */
    .stMarkdown table {
        font-size: 13px !important;
        color: #cbd5e1 !important;
    }
    .stMarkdown th {
        color: #5eead4 !important;
        border-color: rgba(148, 163, 184, 0.22) !important;
    }
    .stMarkdown td {
        border-color: rgba(148, 163, 184, 0.15) !important;
    }
    .stMarkdown code {
        font-family: ui-monospace, monospace !important;
        font-size: 12px !important;
        background: rgba(51, 65, 85, 0.65) !important;
        color: #e2e8f0 !important;
        border: 1px solid rgba(148, 163, 184, 0.2) !important;
        border-radius: 6px !important;
        padding: 0.12em 0.35em !important;
    }

    /* ========== Caption ========== */
    .stCaption, [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
        font-size: 12px !important;
        color: #94a3b8 !important;
    }

    /* ========== 侧栏暗色控制台 ========== */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, rgba(5, 8, 22, 0.96), rgba(15, 23, 42, 0.94)) !important;
        border-right: 1px solid rgba(148, 163, 184, 0.14) !important;
    }
    [data-testid="stSidebar"] .block-container {
        padding-top: 1.25rem !important;
    }
    .sem-sb-label {
        font-size: 0.62rem !important;
        font-weight: 750 !important;
        letter-spacing: 0.14em !important;
        text-transform: uppercase !important;
        color: #5eead4 !important;
        margin: 0.85rem 0 0.4rem 0 !important;
    }
    .sem-sb-title {
        font-size: 1rem !important;
        font-weight: 720 !important;
        color: #f8fafc !important;
        margin: 0 0 12px 0 !important;
        letter-spacing: -0.02em;
    }
    .sem-status-grid {
        font-size: 13px !important;
        color: #cbd5e1 !important;
        line-height: 1.55 !important;
    }
    .sem-status-grid dt {
        font-weight: 650 !important;
        color: #94a3b8 !important;
        margin: 0 !important;
        font-size: 11px !important;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }
    .sem-status-grid dd {
        margin: 0 0 10px 0 !important;
        padding-left: 0 !important;
        color: #e2e8f0 !important;
    }
    [data-testid="stSidebar"] .stTextInput input {
        background-color: rgba(30, 41, 59, 0.85) !important;
        color: #f8fafc !important;
        border-radius: 12px !important;
        border-color: rgba(148, 163, 184, 0.22) !important;
    }
    [data-testid="stSidebar"] .sem-card {
        padding: 16px 18px !important;
        margin-bottom: 10px !important;
    }

    /* ========== KPI 玻璃卡片 ========== */
    .sem-kpi-row {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 10px;
        margin-bottom: 12px;
    }
    @media (max-width: 900px) {
        .sem-kpi-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    .sem-kpi {
        background: rgba(15, 23, 42, 0.72);
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 16px;
        padding: 12px 14px;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.04), 0 12px 28px rgba(0,0,0,0.28);
    }
    .sem-kpi .sem-kpi-label {
        font-size: 10px !important;
        font-weight: 700 !important;
        color: #94a3b8 !important;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-bottom: 6px;
    }
    .sem-kpi .sem-kpi-val {
        font-size: 1.05rem !important;
        font-weight: 720 !important;
        color: #f8fafc !important;
        font-variant-numeric: tabular-nums;
    }

    /* ========== 报告阅读区（浅底可读） ========== */
    .sem-report-scroll {
        white-space: pre-wrap;
        word-break: break-word;
        font-family: Inter, "Microsoft YaHei", sans-serif !important;
        font-size: 13px !important;
        line-height: 1.65 !important;
        max-height: 380px;
        overflow-y: auto;
        padding: 16px 18px;
        background: #f1f5f9;
        border: 1px solid rgba(148, 163, 184, 0.45);
        border-radius: 16px;
        color: #1e293b !important;
        box-shadow: inset 0 2px 8px rgba(15, 23, 42, 0.06);
    }

    /* ========== AI 卡片 / 显微缩略图 ========== */
    .sem-ai-card-pro {
        border: 1px solid rgba(148, 163, 184, 0.2);
        border-radius: 18px;
        padding: 18px 20px;
        margin-bottom: 12px;
        background: rgba(15, 23, 42, 0.72);
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.04), 0 18px 36px rgba(0,0,0,0.28);
        backdrop-filter: blur(12px);
    }
    .sem-ai-card-pro h4 {
        margin: 0 0 12px 0;
        font-size: 14px !important;
        font-weight: 700 !important;
        color: #a78bfa !important;
        letter-spacing: 0.02em;
    }
    .sem-ai-card-pro .sem-ai-field {
        margin-bottom: 12px;
        padding-bottom: 10px;
        border-bottom: 1px solid rgba(51, 65, 85, 0.65);
        font-size: 13px !important;
        color: #cbd5e1 !important;
    }
    .sem-ai-card-pro .sem-ai-field:last-child {
        border-bottom: none;
        margin-bottom: 0;
        padding-bottom: 0;
    }
    .sem-ai-card-pro strong {
        display: block;
        font-size: 11px !important;
        color: #5eead4 !important;
        font-weight: 700 !important;
        margin-bottom: 6px;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }

    .sem-micro-card {
        background: rgba(15, 23, 42, 0.9);
        border: 1px solid rgba(94, 234, 212, 0.15);
        border-radius: 14px;
        padding: 10px;
        margin-bottom: 10px;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(255,255,255,0.04);
    }
    .sem-micro-card img {
        display: block;
        width: 100%;
        border-radius: 8px;
        margin-bottom: 8px;
    }
    .sem-micro-meta {
        font-size: 12px !important;
        color: #94a3b8 !important;
        line-height: 1.45 !important;
    }

    .sem-badge {
        display: inline-block;
        font-size: 10px !important;
        font-weight: 700 !important;
        padding: 3px 8px;
        border-radius: 6px;
        margin-right: 5px;
        margin-top: 5px;
        letter-spacing: 0.04em;
    }
    .sem-badge-teal { background: rgba(20, 184, 166, 0.22); color: #99f6e4 !important; border: 1px solid rgba(45, 212, 191, 0.25); }
    .sem-badge-glass { background: rgba(148, 163, 184, 0.12); color: #cbd5e1 !important; border: 1px solid rgba(148, 163, 184, 0.22); }
    .sem-badge-warn { background: rgba(245, 158, 11, 0.18); color: #fcd34d !important; border: 1px solid rgba(251, 191, 36, 0.25); }
    .sem-badge-danger { background: rgba(239, 68, 68, 0.22); color: #fecaca !important; border: 1px solid rgba(248, 113, 113, 0.28); }
    .sem-badge-clear { background: rgba(16, 185, 129, 0.18); color: #a7f3d0 !important; border: 1px solid rgba(52, 211, 153, 0.25); }

    /* ========== 琥珀警示（无比例尺） ========== */
    .sem-warn-amber {
        border-color: rgba(251, 191, 36, 0.45) !important;
        background: rgba(120, 53, 15, 0.35) !important;
    }
    .sem-warn-amber .sem-card-muted { color: #fde68a !important; }

    /* divider */
    hr {
        border-color: rgba(148, 163, 184, 0.14) !important;
    }
</style>
""",
        unsafe_allow_html=True,
    )


def _render_dashboard_header() -> None:
    st.markdown(
        f"""
<div class="sem-console-header">
  <div class="sem-window-controls">
    <span class="sem-dot sem-dot-r" title="close"></span>
    <span class="sem-dot sem-dot-y" title="minimize"></span>
    <span class="sem-dot sem-dot-g" title="maximize"></span>
  </div>
  <div class="sem-header-grid">
    <div class="sem-header-left">
      <p class="sem-head-title">{html.escape(APP_TITLE)}</p>
      <p class="sem-head-sub">
        Nb₂O₅ morphology analysis · proxy metrics · human-reviewed report drafting
      </p>
    </div>
    <div class="sem-header-right">
      <div class="sem-status-chip-row">
        <span class="sem-status-chip sem-status-chip-accent">Offline Prototype</span>
        <span class="sem-status-chip">Human Review Required</span>
        <span class="sem-status-chip">No Scale → No Real Size</span>
        <span class="sem-status-chip">SEM Image QA</span>
      </div>
    </div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def _pill_png_thumbnail(raw: bytes, max_side: int = 280) -> tuple[str, tuple[int, int]]:
    """返回 data URI 与解码尺寸（与管线读图逻辑一致）。"""
    _gray, pil_img = load_uploaded_image(raw)
    pil_rgb = pil_img.convert("RGB")
    w, h = pil_rgb.size
    thumb = pil_rgb.copy()
    if hasattr(Image, "Resampling"):
        thumb.thumbnail((max_side, max_side), resample=Image.Resampling.LANCZOS)
    else:
        thumb.thumbnail((max_side, max_side))
    buf = BytesIO()
    thumb.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}", (w, h)


def _quality_badge_labels(row: pd.Series, df: pd.DataFrame) -> list[tuple[str, str]]:
    """返回 [(css_class, label), ...] 简易 QA 徽标。"""
    out: list[tuple[str, str]] = []
    try:
        q_sh = float(row["sharpness_laplacian_var"])
        q_ct = float(row["contrast"])
        sh_low = q_sh < float(df["sharpness_laplacian_var"].quantile(0.25))
        ct_low = q_ct < float(df["contrast"].quantile(0.25))
        if ct_low:
            out.append(("sem-badge-warn", "Low contrast"))
        if sh_low:
            out.append(("sem-badge-warn", "Possible blur"))
        if not ct_low and not sh_low:
            out.append(("sem-badge-clear", "Clear"))
        out.append(("sem-badge-glass", "Needs review"))
    except Exception:  # noqa: BLE001
        out.append(("sem-badge-glass", "Needs review"))
    return out


def _format_ai_card_pro_html(image_fname: str, row: dict[str, Any]) -> str:
    """每张 SEM 独立卡片：分项字段（可读 HTML）。"""
    order = [
        ("visible_morphology", "可见形貌描述"),
        ("particle_shape_observation", "颗粒形状观察"),
        ("agglomeration_observation", "团聚/分散观察"),
        ("pore_or_dark_region_observation", "暗区/孔隙观察（proxy）"),
        ("crack_observation", "裂纹观察"),
        ("texture_uniformity", "纹理均匀性"),
        ("quality_issues", "图像质量问题"),
        ("possible_research_meaning", "研究含义（假设）"),
        ("uncertainty_and_manual_check", "不确定性与人工确认建议"),
    ]
    fields_html = []
    for key, title_cn in order:
        if key not in row:
            continue
        val = html.escape(str(row.get(key, "")))
        fields_html.append(
            f'<div class="sem-ai-field"><strong>{html.escape(title_cn)}</strong>{val}</div>'
        )
    title = html.escape(image_fname)
    inner = "".join(fields_html)
    return f'<div class="sem-ai-card-pro"><h4>{title}</h4>{inner}</div>'


def _execute_analysis(
    uploaded_files: list[Any] | None,
    sample_id: str,
    material: str,
    stage: str,
    sinter_temp: str,
    sinter_time: str,
    mag: str,
    scale_info: str,
    caption: str,
    notes: str,
    api_key: str | None,
    use_ai: bool,
    next_ai_eff: bool,
    gen_word: bool,
    *,
    heating_rate: str,
    atmosphere: str,
    process_note: str,
    has_scale_hint: bool,
) -> None:
    """触发完整分析管线（仅编排 UI，逻辑在 _analyze_pipeline）。"""
    if not uploaded_files:
        st.warning("请先上传至少一张图像（「图片上传」或报告工作台运行即可）。")
        return
    with st.spinner("正在计算 proxy 指标并生成报告草稿…"):
        result = _analyze_pipeline(
            list(uploaded_files),
            sample_id,
            material,
            stage,
            sinter_temp,
            sinter_time,
            mag,
            scale_info,
            caption,
            notes,
            api_key,
            use_ai,
            next_ai_eff,
            gen_word,
            heating_rate=heating_rate,
            atmosphere=atmosphere,
            process_note=process_note,
            has_scale_hint=has_scale_hint,
        )
    if not result.get("ok"):
        st.error(result.get("error", "未知错误"))
    else:
        st.session_state[SESSION_RUN] = result
        st.success("分析完成。请在「图像指标」～「实验建议」查看结果；已尝试写入 outputs/。")


def _analyze_pipeline(
    uploaded_files: list[Any],
    sample_id: str,
    material: str,
    stage: str,
    sinter_temp: str,
    sinter_time: str,
    mag: str,
    scale_info: str,
    caption: str,
    notes: str,
    api_key: str | None,
    use_ai_vision: bool,
    use_ai_next_round: bool,
    gen_word: bool,
    *,
    heating_rate: str = "",
    atmosphere: str = "",
    process_note: str = "",
    has_scale_hint: bool = False,
) -> dict[str, Any]:
    """串联：读图 → proxy 指标 → 本地模板 → 可选 OpenAI → Word/Markdown → outputs。"""
    rows: list[dict[str, Any]] = []
    ai_blocks: list[tuple[str, dict[str, str]]] = []
    effective_ai = bool(use_ai_vision and api_key)

    name_to_bytes = {uf.name: uf.getvalue() for uf in uploaded_files}

    for uf in uploaded_files:
        name = uf.name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_EXTENSIONS:
            continue
        raw = name_to_bytes.get(name, b"")
        try:
            gray, _pil = load_uploaded_image(raw)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"读取失败：{name} — {e}"}

        m = compute_image_metrics(gray)
        rows.append({"file_name": name, **m})

    if not rows:
        return {"ok": False, "error": "没有成功解析的图像。"}

    df = pd.DataFrame(rows)
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_bytes = df.to_csv(index=False).encode("utf-8-sig")

    user_ctx = (
        f"样品编号={sample_id}; 材料={material}; 阶段={stage}; "
        f"烧结温度={sinter_temp}; 烧结时间={sinter_time}; 升温速率={heating_rate}; 气氛={atmosphere}; "
        f"倍率={mag}; 比例尺/像素信息={scale_info or '无'}; 工艺备注={process_note or '无'}; "
        f"图注={caption or '无'}; 备注={notes or '无'}"
    )

    local_report = build_local_template_report(
        sample_id=sample_id,
        material=material,
        stage=stage,
        sinter_temp=sinter_temp,
        sinter_time=sinter_time,
        mag=mag,
        scale_info=scale_info or "",
        caption=caption or "",
        notes=notes or "",
        df=df,
        has_scale_hint=has_scale_hint,
        heating_rate=heating_rate,
        atmosphere=atmosphere,
        process_note=process_note,
    )

    json_bytes: bytes | None = None

    if effective_ai:
        ok_names = [str(x) for x in df["file_name"].tolist()]
        for name in ok_names:
            raw = name_to_bytes.get(name, b"")
            try:
                _, pil_img = load_uploaded_image(raw)
                data_url = image_to_png_data_url(pil_img, max_side=AI_IMAGE_MAX_SIDE)
                row = call_openai_vision_sem_json(
                    api_key=api_key,
                    image_name=name,
                    data_url_png=data_url,
                    user_context=user_ctx,
                    has_scale_hint=has_scale_hint,
                )
                ai_blocks.append((name, row))
            except Exception as e:  # noqa: BLE001
                fb = normalize_sem_ai_json(None, name)
                fb["uncertainty_and_manual_check"] = (
                    f"页面端捕获异常：{e}（待人工确认）"
                )
                fb["visible_morphology"] = "（未完成 AI 推断，待人工确认）"
                ai_blocks.append((name, fb))
        try:
            json_bytes = json.dumps(
                [d for _, d in ai_blocks], ensure_ascii=False, indent=2
            ).encode("utf-8-sig")
        except Exception:  # noqa: BLE001
            json_bytes = None

    if use_ai_next_round and api_key:
        summary_parts = [
            user_ctx,
            "\n【图像 proxy 指标 CSV 摘要】\n",
            df.to_csv(index=False)[:6000],
        ]
        if ai_blocks:
            summary_parts.append("\n【AI 结构化摘要】\n")
            summary_parts.append(
                json.dumps([d for _, d in ai_blocks], ensure_ascii=False)[:6000]
            )
        experiment_md = call_openai_experiment_recommendations(
            api_key, "".join(summary_parts)
        )
    else:
        experiment_md = local_next_round_fallback_markdown()

    full_text = build_full_report_text(
        local_report,
        ai_blocks if effective_ai else None,
        experiment_section=experiment_md,
    )

    md_file_bytes = build_markdown_document(APP_TITLE, full_text).encode("utf-8-sig")

    docx_bytes: bytes | None = None
    if gen_word:
        try:
            docx_bytes = make_docx_bytes(
                title=APP_TITLE,
                sample_id=sample_id,
                material=material,
                stage=stage,
                sinter_temp=sinter_temp,
                sinter_time=sinter_time,
                mag=mag,
                scale_info=scale_info or "",
                caption=caption or "",
                notes=notes or "",
                df=df,
                full_report_text=full_text,
                heating_rate=heating_rate,
                atmosphere=atmosphere,
                process_note=process_note,
            )
        except Exception:  # noqa: BLE001
            docx_bytes = None

    saved, outs_warnings = save_run_outputs(
        run_stamp, csv_bytes, json_bytes, docx_bytes, md_bytes=md_file_bytes
    )

    return {
        "ok": True,
        "df": df,
        "ai_blocks": ai_blocks,
        "effective_ai": effective_ai,
        "csv_bytes": csv_bytes,
        "json_bytes": json_bytes,
        "docx_bytes": docx_bytes,
        "md_bytes": md_file_bytes,
        "full_text": full_text,
        "experiment_md": experiment_md,
        "run_stamp": run_stamp,
        "saved": saved,
        "outs_warnings": outs_warnings,
        "uploaded_names": list(name_to_bytes.keys()),
        "name_to_bytes": name_to_bytes,
    }


def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_sem_ui_styles()
    _render_dashboard_header()

    # ----- 侧栏：暗色控制台 -----
    st.sidebar.markdown('<p class="sem-sb-title">控制台</p>', unsafe_allow_html=True)

    st.sidebar.markdown('<p class="sem-sb-label">API 与选项</p>', unsafe_allow_html=True)
    sidebar_api = st.sidebar.text_input(
        "OpenAI API Key（可留空，优先使用环境变量 OPENAI_API_KEY）",
        type="password",
        help="不会在代码中保存；仅写入当前浏览器会话。",
    )
    use_ai = st.sidebar.checkbox("启用 AI 视觉分析（逐图 JSON）", value=False)
    next_ai = st.sidebar.checkbox(
        "启用下一轮实验建议（Responses 文本，需 API Key）",
        value=False,
        help="若关闭则使用本地占位模板建议；均不构成已测数据。",
    )
    gen_word = st.sidebar.checkbox("生成 Word 报告（勾选后提供下载）", value=True)

    api_key = get_openai_api_key(sidebar_api)
    next_ai_eff = next_ai and bool(api_key)
    api_ready = bool(api_key)
    vision_mode = "AI vision + proxy" if (use_ai and api_ready) else "本地 proxy"

    st.sidebar.markdown('<p class="sem-sb-label">状态</p>', unsafe_allow_html=True)
    st.sidebar.markdown(
        f"""
<div class="sem-card sem-status-grid">
  <dl class="sem-status-grid">
    <dt>当前模式</dt><dd>离线原型</dd>
    <dt>API 状态</dt><dd>{'已启用（Key 可用）' if api_ready else '未启用 / 无 Key'}</dd>
    <dt>Word 导出</dt><dd>{'开启' if gen_word else '关闭'}</dd>
    <dt>图像分析</dt><dd>{vision_mode}</dd>
  </dl>
</div>
""",
        unsafe_allow_html=True,
    )

    if use_ai and not api_key:
        st.sidebar.warning("未检测到 Key：视觉 AI 将跳过。")
    if next_ai and not api_key:
        st.sidebar.warning("未检测到 Key：实验建议将使用本地模板。")

    st.sidebar.markdown('<p class="sem-sb-label">安全边界</p>', unsafe_allow_html=True)
    st.sidebar.markdown(
        '<div class="sem-info-lite">不接真实 SEM · 不自动控镜 · 指标仅为 proxy · 结论须人工复核。</div>',
        unsafe_allow_html=True,
    )

    st.sidebar.markdown('<p class="sem-sb-label">快捷</p>', unsafe_allow_html=True)
    st.sidebar.markdown(
        '<div class="sem-info-lite" style="margin-bottom:8px;">上传图像：顶部步骤 <b style="color:#5eead4;">图片上传</b>；生成草稿：<b style="color:#a78bfa;">报告生成</b>。</div>',
        unsafe_allow_html=True,
    )
    if st.sidebar.button("重置分析会话（清空本次结果）", key="sb_reset_run"):
        if SESSION_RUN in st.session_state:
            del st.session_state[SESSION_RUN]
        st.rerun()
    if st.sidebar.button("清空 Streamlit 缓存（一般无需使用）", key="sb_clear_cache"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.sidebar.success("已执行缓存清空。")

    tabs = st.tabs(
        [
            "项目说明",
            "样品信息",
            "图片上传",
            "图像指标",
            "AI 形貌分析",
            "报告生成",
            "实验建议",
        ]
    )

    # 工艺比例尺是否可用于「已提供标定」类提示（样品信息 tab 内赋值）
    process_has_scale_hint = False

    # ---------- ① 项目说明 ----------
    with tabs[0]:
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown(
                '<div class="sem-card"><p class="sem-card-title">系统做什么</p>'
                '<p class="sem-card-muted">上传 SEM 图像，提取图像 <strong>proxy</strong> 指标，辅助形貌观察与实验报告草稿（须人工复核）。</p></div>',
                unsafe_allow_html=True,
            )
        with col_b:
            st.markdown(
                '<div class="sem-card"><p class="sem-card-title">系统不做什么</p>'
                '<p class="sem-card-muted">不自动控镜；不替代人工判断；无可靠比例尺时不输出真实微米尺寸；不编造 XRD/BET/EIS/电化学数据。</p></div>',
                unsafe_allow_html=True,
            )
        with col_c:
            st.markdown(
                '<div class="sem-card"><p class="sem-card-title">适用样品</p>'
                '<p class="sem-card-muted">Nb₂O₅ 原料粉体、压坯、中断烧结、终态烧结、循环后样品的 SEM <strong>初步</strong>分析。</p></div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            '<div class="sem-flow-bar">'
            "<b>SEM Upload</b> → <b>Image QA</b> → <b>Proxy Metrics</b> → <b>Morphology</b> → "
            "<b>Report</b> → <b>Next Experiment</b>"
            "</div>",
            unsafe_allow_html=True,
            )

    # ---------- ② 样品信息 ----------
    with tabs[1]:
        top_l, top_r = st.columns([5, 7])
        with top_l:
            st.markdown(
                '<div class="sem-card"><p class="sem-card-title">样品基础信息</p>',
                unsafe_allow_html=True,
            )
            sample_id = st.text_input("样品编号 Sample_ID", value="S-001")
            material = st.text_input("材料名称", value="Nb2O5")
            stage = st.selectbox("样品阶段", options=STAGE_OPTIONS, index=0)
            st.markdown("</div>", unsafe_allow_html=True)

        with top_r:
            st.markdown(
                '<div class="sem-card sem-process-grid sem-process-responsive"><p class="sem-card-title">工艺与采集参数</p>'
                '<div class="sem-info-lite" style="margin-top:0;margin-bottom:14px;">'
                "倍率仅记录成像放大倍数；若要进行真实粒径、孔径或裂纹长度计算，必须提供比例尺或 pixel size。"
                "本系统默认只输出图像 <strong>proxy</strong> 指标。</div>",
                unsafe_allow_html=True,
            )

            proc_mag_custom = ""
            proc_sb_val = None
            proc_sb_unit = "μm"
            proc_sb_note = ""
            proc_ps_val = None
            proc_ps_unit = "nm/pixel"
            proc_ps_note = ""

            r1a, r1b, r1c = st.columns(3)
            with r1a:
                _proc_subtitle("烧结温度")
                proc_temp_fill = st.selectbox(
                    "填写方式",
                    [PROC_FILL_STRUCTURED, PROC_FILL_CUSTOM],
                    index=0,
                    key="proc_temp_fill_mode",
                )
                proc_temp_custom = proc_temp_fill == PROC_FILL_CUSTOM
                if proc_temp_custom:
                    proc_temp_custom_txt = st.text_input(
                        "温度说明",
                        placeholder="如：室温、未烧结、分段升温至 1200℃",
                        key="proc_temp_custom_txt",
                    )
                    proc_temp_num = None
                    proc_temp_unit = None
                else:
                    proc_temp_custom_txt = ""
                    proc_temp_num = st.number_input(
                        "烧结温度数值",
                        value=1200.0,
                        step=1.0,
                        key="proc_temp_num",
                    )
                    proc_temp_unit = st.selectbox(
                        "单位",
                        ["℃", "K"],
                        index=0,
                        key="proc_temp_unit",
                    )

            with r1b:
                _proc_subtitle("烧结时间")
                proc_time_fill = st.selectbox(
                    "填写方式",
                    [PROC_FILL_STRUCTURED, PROC_FILL_CUSTOM],
                    index=0,
                    key="proc_time_fill_mode",
                )
                proc_time_custom = proc_time_fill == PROC_FILL_CUSTOM
                if proc_time_custom:
                    proc_time_custom_txt = st.text_input(
                        "时间说明",
                        placeholder="如：保温 2 h，升温速率 5 ℃/min",
                        key="proc_time_custom_txt",
                    )
                    proc_time_num = None
                    proc_time_unit = None
                else:
                    proc_time_custom_txt = ""
                    proc_time_num = st.number_input(
                        "烧结时间数值",
                        value=2.0,
                        step=0.5,
                        key="proc_time_num",
                    )
                    proc_time_unit = st.selectbox(
                        "单位",
                        ["min", "h", "s"],
                        index=1,
                        key="proc_time_unit",
                    )

            with r1c:
                _proc_subtitle("升温速率（可选）")
                proc_hr_choice = st.selectbox(
                    "选项",
                    [PROC_HEATING_SKIP, PROC_HEATING_FILL],
                    index=0,
                    key="proc_hr_fill_mode",
                )
                proc_hr_use = proc_hr_choice == PROC_HEATING_FILL
                if proc_hr_use:
                    proc_hr_num = st.number_input(
                        "升温速率数值",
                        value=5.0,
                        step=0.1,
                        key="proc_hr_num",
                    )
                    proc_hr_unit = st.selectbox(
                        "单位",
                        ["℃/min", "℃/h", "K/min"],
                        index=0,
                        key="proc_hr_unit",
                    )
                else:
                    proc_hr_num = None
                    proc_hr_unit = None

            r2a, r2b, r2c = st.columns(3)
            with r2a:
                _proc_subtitle("成像倍率")
                proc_mag_sel = st.selectbox(
                    "常用倍率",
                    options=MAG_PRESETS,
                    index=MAG_PRESETS.index("5000×"),
                    key="proc_mag_sel",
                )
                if proc_mag_sel == "自定义":
                    proc_mag_custom = st.text_input(
                        "自定义倍率",
                        placeholder="如：5000 或 8000×",
                        key="proc_mag_custom",
                    )

            with r2b:
                _proc_subtitle("比例尺 / pixel size")
                proc_scale_mode = st.selectbox(
                    "类型",
                    options=[SCALE_MODE_NONE, SCALE_MODE_BAR, SCALE_MODE_PIXEL],
                    index=0,
                    key="proc_scale_mode",
                )

            with r2c:
                _proc_subtitle("气氛")
                proc_atm_sel = st.selectbox(
                    "气氛",
                    options=ATMOSPHERE_OPTIONS,
                    index=0,
                    key="proc_atm_sel",
                )
                proc_atm_custom = ""
                if proc_atm_sel == "其他/自定义":
                    proc_atm_custom = st.text_input(
                        "自定义气氛",
                        key="proc_atm_custom_field",
                    )

            if proc_scale_mode == SCALE_MODE_BAR:
                _proc_subtitle("标尺长度（scale bar）")
                b1, b2, b3 = st.columns([2, 2, 4])
                with b1:
                    proc_sb_val = st.number_input(
                        "比例尺数值",
                        value=50.0,
                        step=0.1,
                        min_value=0.0,
                        key="proc_sb_val",
                    )
                with b2:
                    proc_sb_unit = st.selectbox(
                        "单位",
                        list(SCALE_BAR_UNIT_KEYS),
                        index=1,
                        format_func=lambda k: SCALE_BAR_UNIT_DISPLAY[k],
                        key="proc_sb_unit",
                    )
                with b3:
                    proc_sb_note = st.text_input(
                        "备注（可选）",
                        placeholder="如：图中标尺长度对应 50 μm",
                        key="proc_sb_note",
                    )
            elif proc_scale_mode == SCALE_MODE_PIXEL:
                _proc_subtitle("像素标定（pixel size）")
                p1, p2, p3 = st.columns([2, 2, 4])
                with p1:
                    proc_ps_val = st.number_input(
                        "pixel size 数值",
                        value=2.5,
                        step=0.01,
                        min_value=0.0,
                        key="proc_ps_val",
                    )
                with p2:
                    proc_ps_unit = st.selectbox(
                        "单位",
                        list(PIXEL_SIZE_UNIT_KEYS),
                        index=0,
                        format_func=lambda k: PIXEL_SIZE_UNIT_DISPLAY[k],
                        key="proc_ps_unit",
                    )
                with p3:
                    proc_ps_note = st.text_input(
                        "备注（可选）",
                        key="proc_ps_note",
                    )

            if proc_scale_mode == SCALE_MODE_NONE:
                st.markdown(
                    '<div class="sem-card sem-warn-amber" style="padding:14px 16px;margin-top:8px;">'
                    '<p class="sem-card-muted" style="margin:0;">'
                    "<strong>未提供比例尺或 pixel size：</strong>"
                    "本系统<strong>不会</strong>输出真实微米/纳米尺度下的粒径、孔径或裂纹长度。"
                    "</p></div>",
                    unsafe_allow_html=True,
                )

            _proc_subtitle("工艺备注")
            process_note = st.text_area(
                "工艺备注（可选）",
                placeholder="如：升温程序、气氛切换、样品支架等",
                height=88,
                key="proc_process_note",
                label_visibility="collapsed",
            )

            st.markdown("</div>", unsafe_allow_html=True)

        sinter_temp = _format_temperature(
            proc_temp_custom,
            proc_temp_custom_txt,
            proc_temp_num,
            proc_temp_unit,
        )
        sinter_time = _format_sinter_time(
            proc_time_custom,
            proc_time_custom_txt,
            proc_time_num,
            proc_time_unit,
        )
        heating_rate = _format_heating_rate(proc_hr_use, proc_hr_num, proc_hr_unit)
        mag = _format_magnification(proc_mag_sel, proc_mag_custom)
        atmosphere = _format_atmosphere(proc_atm_sel, proc_atm_custom)
        scale_info, process_has_scale_hint = _format_scale_info_row(
            proc_scale_mode,
            proc_sb_val,
            proc_sb_unit,
            proc_sb_note,
            proc_ps_val,
            proc_ps_unit,
            proc_ps_note,
        )

        process_info = {
            "sintering_temperature": sinter_temp,
            "sintering_time": sinter_time,
            "heating_rate": heating_rate,
            "magnification": mag,
            "scale_info": scale_info,
            "atmosphere": atmosphere,
            "process_note": process_note or "",
        }
        st.session_state["sem_process_info"] = process_info

        with st.expander("各样品阶段的观察侧重点（说明）", expanded=False):
            st.markdown(STAGE_HINTS_MD)

        st.markdown(
            '<div class="sem-card"><p class="sem-card-title">图注与备注</p>',
            unsafe_allow_html=True,
        )
        caption = st.text_area(
            "图片说明（图注）",
            placeholder="例如：样品制备、测试条件、视场说明等",
            label_visibility="visible",
        )
        notes = st.text_area(
            "备注",
            placeholder="其它备注（批次、操作者缩写等；不做自动解析）",
            label_visibility="visible",
        )
        st.markdown("</div>", unsafe_allow_html=True)
        st.caption(
            "结构化工艺字段见上文「工艺与采集参数」（process_info）；"
            "若比例尺类型为「未提供」，本程序不对像素换算为真实微米/纳米尺度测量结论。"
        )

    # ---------- ③ 图片上传 ----------
    run_btn_tab3 = False
    with tabs[2]:
        st.markdown(
            '<div class="sem-card"><p class="sem-card-title">SEM image intake · 上传与预览</p>',
            unsafe_allow_html=True,
        )
        uploaded = st.file_uploader(
            "支持 tif / tiff / png / jpg / jpeg（可多选）",
            type=sorted(ALLOWED_EXTENSIONS),
            accept_multiple_files=True,
            key="sem_uploader",
        )
        run_btn_tab3 = st.button(
            "运行分析并生成报告",
            type="primary",
            key="btn_run_tab3",
            help="与「报告生成」页的「生成实验报告草稿」为同一套分析管线。",
        )
        st.markdown("</div>", unsafe_allow_html=True)

        if uploaded:
            # st.markdown('<div class="sem-card-dark"><p class="sem-card-title">预览网格（显微查看器风格）</p></div>', unsafe_allow_html=True)
            grid_cols = st.columns(3)
            for idx, uf in enumerate(uploaded):
                ext = uf.name.rsplit(".", 1)[-1].lower() if "." in uf.name else ""
                raw = uf.getvalue()
                big_hint = len(raw) > 4 * 1024 * 1024
                col = grid_cols[idx % 3]
                with col:
                    badges_html = (
                        '<span class="sem-badge sem-badge-teal">SEM candidate</span>'
                    )
                    if ext in {"tif", "tiff"}:
                        badges_html += (
                            '<span class="sem-badge sem-badge-glass">TIF loaded</span>'
                        )
                    try:
                        data_uri, (w, h) = _pill_png_thumbnail(raw)
                        if big_hint:
                            badges_html += '<span class="sem-badge sem-badge-warn">Large file</span>'
                        meta_lines = (
                            f"<div>{badges_html}</div>"
                            f'<div class="sem-micro-meta"><strong>{html.escape(uf.name)}</strong><br/>'
                            f"{w}×{h} px · {ext.upper()} · OK"
                            "</div>"
                        )
                        if max(w, h) > AI_IMAGE_MAX_SIDE:
                            meta_lines += f'<div class="sem-micro-meta" style="margin-top:4px;color:#fcd34d;">'
                            f"发送 AI 时将缩放至最长边 ≤ {AI_IMAGE_MAX_SIDE}px；"
                            f"本地 proxy 仍基于完整读取。</div>"
                        st.markdown(
                            f'<div class="sem-micro-card">'
                            f'<img src="{data_uri}" alt=""/>'
                            f"{meta_lines}</div>",
                            unsafe_allow_html=True,
                        )
                    except Exception as e:  # noqa: BLE001
                        st.markdown(
                            f'<div class="sem-micro-card" style="border-color:#b91c1c;">'
                            f'<div class="sem-micro-meta">'
                            f'<span class="sem-badge sem-badge-danger">Read error</span><br/>'
                            f"<strong>{html.escape(uf.name)}</strong><br/>"
                            f"{html.escape(str(e))}"
                            f"</div></div>",
                            unsafe_allow_html=True,
                        )
            if any(len(uf.getvalue()) > 4 * 1024 * 1024 for uf in uploaded):
                st.info(
                    "部分图像体积较大：发送至视觉 AI 时会压缩；本地 proxy 指标仍基于当前解码结果。"
                )
        else:
            st.info("请上传至少一张图像后再运行分析。")

    # ---------- ⑥ 报告生成（首段：主按钮，与管线触发挂钩） ----------
    run_btn_tab6 = False
    with tabs[5]:
        rx1, rx2 = st.columns([1.35, 1])
        with rx1:
            st.markdown(
                '<div class="sem-card"><p class="sem-card-title">报告工作台 · SEM Console</p>'
                '<p class="sem-card-muted">与「图片上传」共用同一管线；运行后刷新本节下方的<strong style="color:#a78bfa;">状态</strong>与<strong style="color:#5eead4;">预览</strong>。</p></div>',
                unsafe_allow_html=True,
            )
        with rx2:
            st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)
            run_btn_tab6 = st.button(
                "生成实验报告草稿",
                type="primary",
                key="btn_run_tab6",
                help="与「图片上传」页的「运行分析并生成报告」相同。",
            )

    if run_btn_tab3 or run_btn_tab6:
        _execute_analysis(
            list(uploaded) if uploaded else None,
            sample_id,
            material,
            stage,
            sinter_temp,
            sinter_time,
            mag,
            scale_info,
            caption,
            notes,
            api_key,
            use_ai,
            next_ai_eff,
            gen_word,
            heating_rate=heating_rate,
            atmosphere=atmosphere,
            process_note=process_note,
            has_scale_hint=process_has_scale_hint,
        )

    run = st.session_state.get(SESSION_RUN)

    # ---------- ④ 指标 ----------
    with tabs[3]:
        st.markdown(
            '<p class="sem-card-title sem-lead-tight">基础图像指标仪表盘（proxy）</p>',
            unsafe_allow_html=True,
        )
        if not run:
            st.markdown(
                '<div class="sem-card"><p class="sem-card-muted">在「图片上传」或「报告生成」运行分析后，此处展示 KPI 与汇总表。</p></div>',
                unsafe_allow_html=True,
            )
        else:
            df = run["df"]
            n_img = len(df)
            ms = float(df["sharpness_laplacian_var"].mean())
            me = float(df["edge_density"].mean())
            mdark = float(df["dark_area_ratio"].mean())
            kpi_html = f"""
<div class="sem-kpi-row">
  <div class="sem-kpi"><div class="sem-kpi-label">图片数量</div><div class="sem-kpi-val">{n_img}</div></div>
  <div class="sem-kpi"><div class="sem-kpi-label">平均清晰度 proxy</div><div class="sem-kpi-val">{ms:.3f}</div></div>
  <div class="sem-kpi"><div class="sem-kpi-label">平均边缘密度 proxy</div><div class="sem-kpi-val">{me:.5f}</div></div>
  <div class="sem-kpi"><div class="sem-kpi-label">平均暗区比例 proxy</div><div class="sem-kpi-val">{mdark:.5f}</div></div>
</div>
"""
            st.markdown(kpi_html, unsafe_allow_html=True)

            if not process_has_scale_hint:
                st.markdown(
                    '<div class="sem-card sem-warn-amber">'
                    '<p class="sem-card-muted" style="margin:0;">'
                    "<strong>未提供比例尺或 pixel size：</strong>"
                    "本系统<strong>不会</strong>输出真实微米/纳米尺度下的粒径、孔径或裂纹长度。"
                    "</p></div>",
                    unsafe_allow_html=True,
                )

            st.markdown(
                '<div class="sem-info-lite"><strong style="color:#5eead4;">proxy 声明：</strong>'
                "以下指标均为图像 proxy，用于质控与辅助观察；"
                "<strong>不直接等价</strong>于粒径、孔隙率或真实物理尺寸。</div>",
                unsafe_allow_html=True,
            )

            with st.expander("指标释义（info）", expanded=False):
                st.markdown(METRICS_GLOSSARY_MD)
            with st.expander(f"{PROXY_METRIC_TERMS_TITLE}（必读）", expanded=False):
                st.markdown(
                    f"### {PROXY_METRIC_TERMS_TITLE}\n\n"
                    + "\n".join(f"- {b}" for b in PROXY_METRIC_TERMS_BULLETS)
                )
            df_show = df.rename(columns={k: v for k, v in METRICS_COL_CN.items() if k in df.columns})
            st.markdown('<div class="sem-table-wrap">', unsafe_allow_html=True)
            st.markdown('<p class="sem-card-title sem-lead-table">指标汇总表</p>', unsafe_allow_html=True)
            st.dataframe(df_show, use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)
            st.download_button(
                label="下载指标表 CSV",
                data=run["csv_bytes"],
                file_name="sem_image_proxy_metrics.csv",
                mime="text/csv",
                key="dl_csv_tab4",
            )

    # ---------- ⑤ AI ----------
    with tabs[4]:
        st.markdown(
            '<p class="sem-card-title sem-lead-tight">AI 形貌分析（结构化）</p>',
            unsafe_allow_html=True,
        )
        if not run:
            st.markdown(
                '<div class="sem-card"><p class="sem-card-muted">'
                "当前尚无运行结果。启用 OpenAI 并完成分析后，可生成每张 SEM 的结构化观察草稿。"
                "</p></div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div class="sem-card"><p class="sem-card-muted">'
                "<strong>本地 proxy 模式说明：</strong>"
                "启用 API 后，可对每张图输出颗粒形状、团聚、暗区/孔隙 proxy、裂纹迹象、"
                "图像质量问题和人工复核建议（均为定性草稿）。"
                "</p></div>",
                unsafe_allow_html=True,
            )
        elif not run["effective_ai"]:
            st.markdown(
                '<div class="sem-card"><p class="sem-card-muted">'
                "本次未启用视觉 AI 或未提供 Key：<strong>无模型结构化输出</strong>。"
                "仍为本地 proxy 分析模式。"
                "</p></div>",
                unsafe_allow_html=True,
            )
        else:
            ai_blocks = run["ai_blocks"]
            ai_df = pd.DataFrame(
                [d for _, d in ai_blocks], columns=list(SEM_AI_JSON_KEYS)
            )
            ai_df_show = ai_df.rename(
                columns={k: v for k, v in SEM_AI_COL_CN.items() if k in ai_df.columns}
            )
            st.caption("摘要表（中文列名）；每张图的解读见下方卡片。")
            st.markdown('<div class="sem-table-wrap">', unsafe_allow_html=True)
            st.dataframe(ai_df_show, use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)
            jb = run.get("json_bytes")
            if jb:
                st.download_button(
                    label="下载 AI 分析 JSON（机器可读）",
                    data=jb,
                    file_name="sem_ai_vision_analysis.json",
                    mime="application/json",
                    key="dl_json_tab5",
                )
            df_metrics = run["df"].set_index("file_name")
            for fname, row in ai_blocks:
                try:
                    mrow = df_metrics.loc[fname]
                    qa_parts = []
                    for cls, lb in _quality_badge_labels(mrow, run["df"]):
                        qa_parts.append(
                            f'<span class="sem-badge {cls}">{html.escape(lb)}</span>'
                        )
                    qa_html = "".join(qa_parts)
                except Exception:  # noqa: BLE001
                    qa_html = '<span class="sem-badge sem-badge-glass">Needs review</span>'
                card_html = _format_ai_card_pro_html(fname, row)
                st.markdown(
                    f'<div style="margin-bottom:6px;">{qa_html}</div>{card_html}',
                    unsafe_allow_html=True,
                )

    # ---------- ⑥ 报告生成（末段：状态、预览、下载；须在管线执行之后渲染） ----------
    with tabs[5]:
        uploaded_flag = bool(uploaded)
        run_done = bool(run)
        has_ai_eff = bool(run and run.get("effective_ai"))
        docx_ok = bool(run and run.get("docx_bytes"))

        if not run:
            st.markdown(
                '<div class="sem-card"><p class="sem-card-muted">'
                "尚未运行分析：请在顶部步骤「图片上传」或本节上方的<strong style=\"color:#a78bfa;\">生成实验报告草稿</strong>执行。"
                "</p></div>",
                unsafe_allow_html=True,
            )
        else:
            ft = run["full_text"]
            st.markdown(
                '<div class="sem-card"><p class="sem-card-title">人工复核</p>'
                '<p class="sem-card-muted">AI 与 proxy 均为草稿；尺寸、孔隙与性能结论须<strong>复核</strong>与<strong>实验验证</strong>。</p></div>',
                unsafe_allow_html=True,
            )

            row_lr_l, row_lr_r = st.columns([1.15, 1])
            with row_lr_l:
                st.markdown(
                    f"""
<div class="sem-card"><p class="sem-card-title">运行状态</p>
<dl class="sem-status-grid">
  <dt>上传图像（本会话）</dt><dd>{'是' if uploaded_flag else '否'}</dd>
  <dt>本次运行结果</dt><dd>{'已生成' if run_done else '否'}</dd>
  <dt>视觉 AI</dt><dd>{'参与' if has_ai_eff else '未参与（本地 proxy）'}</dd>
  <dt>Word</dt><dd>{'可下载' if docx_ok else ('失败/未生成' if run_done else '—')}</dd>
</dl></div>
""",
                    unsafe_allow_html=True,
                )
            with row_lr_r:
                st.markdown(
                    '<div class="sem-card"><p class="sem-card-title">导出</p>'
                    '<p class="sem-card-muted" style="margin-bottom:12px;">下载报告与指标数据（文件名默认见浏览器）。</p></div>',
                    unsafe_allow_html=True,
                )
                st.download_button(
                    label="Markdown（.md）",
                    data=run["md_bytes"],
                    file_name="sem_report.md",
                    mime="text/markdown",
                    key="dl_md_tab6",
                    use_container_width=True,
                )
                dx = run.get("docx_bytes")
                if dx:
                    st.download_button(
                        label="Word（.docx）",
                        data=dx,
                        file_name="sem_report.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key="dl_docx_tab6",
                        use_container_width=True,
                    )
                elif gen_word:
                    st.warning("Word 生成失败或未勾选生成选项。")
                st.download_button(
                    label="指标 CSV",
                    data=run["csv_bytes"],
                    file_name="sem_image_proxy_metrics.csv",
                    mime="text/csv",
                    key="dl_csv_tab6_rep",
                    use_container_width=True,
                )

            st.markdown(
                '<div class="sem-card"><p class="sem-card-title">报告预览（可读区）</p></div>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div class="sem-report-scroll">{html.escape(ft)}</div>',
                unsafe_allow_html=True,
            )
            st.caption("预览为纯文本；下载 Markdown / Word 用于排版与提交。")

    # ---------- ⑦ 下一轮 ----------
    with tabs[6]:
        st.markdown(
            '<p class="sem-card-title sem-lead-tight">实验规划（假设性建议）</p>',
            unsafe_allow_html=True,
        )
        st.markdown(
            """
<div class="sem-card"><p class="sem-card-title">建议补拍图像（假设）</p>
<ul class="sem-card-muted" style="margin:0;padding-left:1.1rem;">
<li>不同倍率下的对照视场；</li>
<li>样品不同区域（边缘 vs 中心）；</li>
<li>多张重复视场以降低偶然性；</li>
<li>结构薄弱区的针对性成像。</li>
</ul></div>
<div class="sem-card"><p class="sem-card-title">建议补充表征（假设）</p>
<ul class="sem-card-muted" style="margin:0;padding-left:1.1rem;">
<li><strong>XRD（假设）</strong>：用于晶相确认思路，非本工具输出；</li>
<li><strong>BET（假设）</strong>：辅助比表面积方向的实验设计；</li>
<li><strong>EIS（假设）</strong>：离子/电子输运验证需单独实验；</li>
<li><strong>电化学测试（假设）</strong>：性能结论须实验数据支撑；</li>
<li><strong>粒径统计（假设）</strong>：验证形貌趋势需定量手段。</li>
</ul></div>
<div class="sem-card"><p class="sem-card-title">风险与不确定性（假设）</p>
<ul class="sem-card-muted" style="margin:0;padding-left:1.1rem;">
<li>单张 SEM 代表性可能不足；</li>
<li>无比例尺则难以量化真实尺寸；</li>
<li>暗区<strong>不一定</strong>等于孔隙；</li>
<li>分割/标注类结论需人工验证。</li>
</ul></div>
""",
            unsafe_allow_html=True,
        )
        if run:
            st.markdown('<div class="sem-card"><p class="sem-card-title">本轮生成的建议草稿</p>', unsafe_allow_html=True)
            st.markdown(run["experiment_md"])
            st.markdown("</div>", unsafe_allow_html=True)
            st.caption(
                "以上内容仅为流程层面的假设性建议，不构成已测结论；不得据此编造 XRD/BET/EIS 等数值。"
            )
        else:
            st.markdown(
                '<div class="sem-info-lite">运行分析后，此处追加<strong style="color:#5eead4;">本轮</strong>本地模板或 API 生成的实验规划<strong>草稿（假设）</strong>。</div>',
                unsafe_allow_html=True,
            )

    if run:
        if run.get("saved"):
            st.sidebar.success("已写入 outputs/：" + "、".join(run["saved"]))
        for w in run.get("outs_warnings") or []:
            st.sidebar.warning(w)


if __name__ == "__main__":
    main()
