# -*- coding: utf-8 -*-
"""SEM 图像读取、灰度化与图像 proxy 指标（非严格物理量）。"""

from __future__ import annotations

import io
from typing import Any

import cv2
import numpy as np
from PIL import Image


def _pil_to_gray_uint8(img: Image.Image) -> np.ndarray:
    """Pillow → uint8 单通道灰度；兼容灰度 / RGB / RGBA 等。"""
    mode = img.mode
    if mode == "L":
        gray = np.asarray(img, dtype=np.uint8)
    elif mode in ("RGB", "RGBA"):
        rgb = img.convert("RGB")
        arr = np.asarray(rgb, dtype=np.uint8)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    else:
        rgb = img.convert("RGB")
        arr = np.asarray(rgb, dtype=np.uint8)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    return gray


def load_uploaded_image(file_bytes: bytes) -> tuple[np.ndarray, Image.Image]:
    """从字节读取图像（含 TIF）；返回 (灰度 ndarray, 原始 Pillow 图像)。"""
    pil_img = Image.open(io.BytesIO(file_bytes))
    pil_img.load()
    gray = _pil_to_gray_uint8(pil_img)
    return gray, pil_img


def compute_image_metrics(gray: np.ndarray) -> dict[str, Any]:
    """
    计算第一版「辅助性 proxy」指标（非最终物理结论）。

    - dark_area_ratio：低于灰度 25% 分位的像素占比 → 仅称「暗区面积比例 proxy」，禁止解释为孔隙率。
    - edge_density：Canny 边缘像素占比 → 仅反映图像边缘丰富程度，禁止等同于颗粒边界数量。
    - sharpness_laplacian_var：Laplacian 响应方差 → 仅作清晰度 / 成像质控 proxy，不作分辨率或粒径度量。
    - 无比例尺或可核验 pixel size 时，禁止据此给出微米粒径/孔径/裂纹长度。
    """
    g = gray.astype(np.float64)
    h, w = gray.shape[:2]
    mean_gray = float(np.mean(g))
    std_gray = float(np.std(g))
    contrast = float((gray.max() - gray.min()) / 255.0) if gray.size else 0.0

    lap = cv2.Laplacian(g, cv2.CV_64F)
    sharpness_laplacian_var = float(lap.var())

    edges = cv2.Canny(gray, threshold1=50, threshold2=150)
    edge_density = float(np.mean(edges > 0))

    p25 = float(np.percentile(gray, 25))
    dark_area_ratio = float(np.mean(gray < p25))

    return {
        "width": int(w),
        "height": int(h),
        "mean_gray": round(mean_gray, 4),
        "std_gray": round(std_gray, 4),
        "contrast": round(contrast, 6),
        "sharpness_laplacian_var": round(sharpness_laplacian_var, 4),
        "edge_density": round(edge_density, 6),
        "dark_area_ratio": round(dark_area_ratio, 6),
    }
