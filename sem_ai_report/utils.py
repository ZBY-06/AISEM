# -*- coding: utf-8 -*-
"""通用工具：共享常量、API Key、发往模型的图像缩放与 base64 data URL、outputs 目录。"""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from PIL import Image

# AI 结构化 JSON 字段（与 prompts / OpenAI / 报表列一致）
SEM_AI_JSON_KEYS: tuple[str, ...] = (
    "image_name",
    "visible_morphology",
    "particle_shape_observation",
    "agglomeration_observation",
    "pore_or_dark_region_observation",
    "crack_observation",
    "texture_uniformity",
    "quality_issues",
    "possible_research_meaning",
    "uncertainty_and_manual_check",
)

# 发往 OpenAI 前最长边像素上限（成本控制）
AI_IMAGE_MAX_SIDE = 1600

# 第一版：表格列名仍为英文键；报告中须向用户明确「称谓约束」（非最终物理结论）
PROXY_METRIC_TERMS_TITLE = "分项称谓约束（第一版）"
PROXY_METRIC_TERMS_BULLETS: tuple[str, ...] = (
    "dark_area_ratio：仅称「暗区面积比例 proxy」，禁止解释为孔隙率或孔隙体积分数；仅代表低于选定灰度分位阈值的像素占比。",
    "edge_density：仅反映图像边缘像素占比（边缘「丰富程度」proxy）；禁止等同于颗粒边界条数或真实界面密度。",
    "sharpness_laplacian_var：仅作成像清晰度 / 对焦质量的 proxy（质控）；禁止作为分辨率或粒径等物理度量。",
    "mean_gray / std_gray / contrast：仅为灰度统计与反差 proxy；不得解释为化学成分或真实相含量。",
    "比例尺与 pixel size：若未提供可靠标尺或可核验的 pixel size，禁止输出微米单位的粒径、孔径、裂纹长度等测量结论（仅限定性描述或标注「待人工确认」）。",
)

# 项目根目录（本文件所在目录）
PROJECT_ROOT: Path = Path(__file__).resolve().parent
OUTPUTS_DIR: Path = PROJECT_ROOT / "outputs"


def ensure_outputs_dir() -> Path:
    """确保 outputs/ 存在，返回路径。"""
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUTS_DIR


def get_openai_api_key(sidebar_key: str) -> str | None:
    """环境变量 OPENAI_API_KEY 优先；否则使用侧栏密码框。禁止写死 Key。"""
    env_k = os.environ.get("OPENAI_API_KEY", "").strip()
    if env_k:
        return env_k
    sk = (sidebar_key or "").strip()
    return sk if sk else None


def resize_pil_for_api(pil_img: Image.Image, max_side: int = AI_IMAGE_MAX_SIDE) -> Image.Image:
    """最长边限制为 max_side（按比例）；仅用于 API 副本，预览仍用原图。"""
    w, h = pil_img.size
    if max(w, h) <= max_side:
        return pil_img
    scale = max_side / float(max(w, h))
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return pil_img.resize((nw, nh), Image.Resampling.LANCZOS)


def image_to_png_data_url(pil_img: Image.Image, max_side: int | None = None) -> str:
    """PNG base64 data URL，供视觉 API。max_side 给定则先缩放。"""
    img = pil_img
    if max_side is not None:
        img = resize_pil_for_api(img, max_side=max_side)
    buf = io.BytesIO()
    if img.mode not in ("RGB", "RGBA", "L"):
        pil_save = img.convert("RGB")
    else:
        pil_save = img
    pil_save.save(buf, format="PNG")
    b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def try_write_file(path: Path, data: bytes) -> tuple[bool, str]:
    """写入磁盘；失败返回 (False, 错误信息)。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return True, ""
    except OSError as e:
        return False, str(e)


def save_run_outputs(
    stamp: str,
    csv_bytes: bytes | None,
    json_bytes: bytes | None,
    docx_bytes: bytes | None,
    md_bytes: bytes | None = None,
) -> tuple[list[str], list[str]]:
    """
    将本次运行的导出副本写入 outputs/。
    返回 (成功写入的相对路径列表, 警告信息列表)。
    """
    ensure_outputs_dir()
    saved: list[str] = []
    warnings: list[str] = []
    base = OUTPUTS_DIR

    if csv_bytes:
        p = base / f"{stamp}_metrics.csv"
        ok, err = try_write_file(p, csv_bytes)
        if ok:
            saved.append(str(p.relative_to(PROJECT_ROOT)))
        else:
            warnings.append(f"未写入 metrics CSV：{err}")
    if json_bytes:
        p = base / f"{stamp}_ai_analysis.json"
        ok, err = try_write_file(p, json_bytes)
        if ok:
            saved.append(str(p.relative_to(PROJECT_ROOT)))
        else:
            warnings.append(f"未写入 AI JSON：{err}")
    if docx_bytes:
        p = base / f"{stamp}_report.docx"
        ok, err = try_write_file(p, docx_bytes)
        if ok:
            saved.append(str(p.relative_to(PROJECT_ROOT)))
        else:
            warnings.append(f"未写入 Word：{err}")
    if md_bytes:
        p = base / f"{stamp}_report.md"
        ok, err = try_write_file(p, md_bytes)
        if ok:
            saved.append(str(p.relative_to(PROJECT_ROOT)))
        else:
            warnings.append(f"未写入 Markdown：{err}")

    return saved, warnings
