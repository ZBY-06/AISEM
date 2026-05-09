# -*- coding: utf-8 -*-
"""实验报告正文（本地模板 + AI 块拼接）。"""

from __future__ import annotations

import pandas as pd

from utils import (
    PROXY_METRIC_TERMS_BULLETS,
    PROXY_METRIC_TERMS_TITLE,
    SEM_AI_JSON_KEYS,
)


def build_local_template_report(
    sample_id: str,
    material: str,
    stage: str,
    sinter_temp: str,
    sinter_time: str,
    mag: str,
    scale_info: str,
    caption: str,
    notes: str,
    df: pd.DataFrame,
    has_scale_hint: bool,
    *,
    heating_rate: str = "",
    atmosphere: str = "",
    process_note: str = "",
) -> str:
    """不调用 API 的本地模板报告正文（含样品信息与 proxy 声明）。"""
    lines: list[str] = []
    lines.append("【实验报告（本地模板）】")
    lines.append("")
    lines.append("一、样品与工艺信息（用户填写）")
    lines.append(f"  样品编号：{sample_id}")
    lines.append(f"  材料名称：{material}")
    lines.append(f"  样品阶段：{stage}")
    lines.append(f"  烧结温度：{sinter_temp}")
    lines.append(f"  烧结时间：{sinter_time}")
    lines.append(f"  电镜倍率（记录值）：{mag}")
    lines.append(f"  升温速率：{heating_rate or '（未提供）'}")
    lines.append(f"  气氛：{atmosphere or '（未提供）'}")
    lines.append(f"  像素尺寸或比例尺信息：{scale_info or '（未填写）'}")
    lines.append(f"  工艺备注：{process_note or '（无）'}")
    lines.append(f"  图注（图片说明）：{caption or '（无）'}")
    lines.append(f"  备注：{notes or '（无）'}")
    lines.append("")
    lines.append("二、图像与测量说明")
    lines.append(
        "  本节数值均为基于单张二维图像的「图像 proxy 指标」，受成像条件、衬度、边缘效应等影响，"
        "不能等同于严格物理量（如真实粒径分布、真实孔隙率等）；仅为辅助性草稿，不是最终物理结论。"
    )
    lines.append(f"  {PROXY_METRIC_TERMS_TITLE}")
    for b in PROXY_METRIC_TERMS_BULLETS:
        lines.append(f"  - {b}")
    if not has_scale_hint:
        lines.append(
            "  未提供可用的比例尺/像素标定信息时，本报告不对像素尺度做微米等单位换算，"
            "也不据此推断真实尺寸。"
        )
    else:
        lines.append(
            "  已填写比例尺/像素相关信息，但本原型仍不对图像做自动物理标定换算；"
            "真实尺寸需结合电镜软件或人工标定后确认。"
        )
    lines.append("")
    lines.append("三、各图基础图像 proxy 指标汇总（见页面表格与 CSV）")
    lines.append(f"  共 {len(df)} 张图像。")
    if not df.empty:
        for _, row in df.iterrows():
            lines.append(
                f"  - {row.get('file_name', '')}: "
                f"{row.get('width')}×{row.get('height')} px, "
                f"mean_gray={row.get('mean_gray')}, "
                f"edge_density={row.get('edge_density')}"
            )
    lines.append("")
    lines.append("四、形貌文字描述")
    lines.append(
        "  （本地模板未启用 AI 时）此处需实验人员根据 SEM 形貌自行补全；"
        "若已启用 AI，见下文「AI 结构化视觉分析（JSON 字段）」，须经人工复核。"
    )
    lines.append("")
    lines.append("五、可解释研究链条（占位）")
    lines.append(
        "  原料形貌 → 烧结过程 → 终态微结构 → 电化学性能 的关联分析应由研究者在实验数据与文献基础上完成；"
        "本工具仅提供图像 proxy 与辅助描述草稿。"
    )
    lines.append(
        "  【数据诚信】本报告不包含也未声称已完成 XRD、BET、EIS、循环或容量等测试；"
        "禁止在本工具输出中编造上述任一数值或未开展实验的结论。"
    )
    lines.append("")
    lines.append("六、不确定性与人工复核（Uncertainty and Manual Review）")
    lines.append(
        "  AI 输出与图像 proxy 指标均为辅助草稿，可能存在误判或遗漏；禁止不经复核写入正式论文结论。"
        "涉及尺寸、孔隙、裂纹定量及电化学推断的结论必须由人工结合原始数据与补充表征确认。"
    )
    return "\n".join(lines)


def format_sem_ai_dict_for_report(d: dict[str, str]) -> str:
    """单张图 AI 结构化结果 → 报告用多行文本。"""
    lines: list[str] = []
    for k in SEM_AI_JSON_KEYS:
        lines.append(f"{k}: {d.get(k, '')}")
    return "\n".join(lines)


def build_full_report_text(
    local_part: str,
    ai_blocks: list[tuple[str, dict[str, str]]] | None,
    experiment_section: str | None = None,
) -> str:
    """拼接完整报告文本；可选追加下一轮实验建议（假设性）。"""
    blocks: list[str] = [local_part]
    if ai_blocks:
        blocks.append("")
        blocks.append("【AI 结构化视觉分析（JSON 字段展开，待人工确认，非计量结论）】")
        for fname, row in ai_blocks:
            blocks.append(f"--- 文件：{fname} ---")
            blocks.append(format_sem_ai_dict_for_report(row))
            blocks.append("")
    if experiment_section and experiment_section.strip():
        blocks.append("")
        blocks.append("【下一轮实验建议（假设性，须实验与其它表征验证；不得视为已测数据）】")
        blocks.append(experiment_section.strip())
    return "\n".join(blocks).strip()


def build_markdown_document(title: str, plain_body: str) -> str:
    """导出简易 Markdown 文档（正文可为纯文本或含二级标题）。"""
    return f"# {title}\n\n{plain_body.strip()}\n"


def local_next_round_fallback_markdown() -> str:
    """未启用 API 时的占位级实验规划提示（非测量结论）。"""
    return (
        "## 本地模板建议（未调用 AI 实验规划）\n"
        "- 若图像疑似失焦、过曝或充电条纹，建议在仪器条件允许下调整参数后补拍对照视场。（假设性，须验证）\n"
        "- 若需关联 Nb₂O₅ 烧结显微结构演变，建议结合课题组已有能力安排**真实实验**后的补充表征；"
        "**禁止**在本工具中编造 XRD / BET / EIS 等任何数值。（须验证）\n"
        "- 多尺度对照时可补充不同放大倍数视场，并保持标尺/记录条件一致。（待人工确认）\n"
        "\n"
        "## 不确定性与人工复核\n"
        "- 以上仅为流程占位建议，须由课题负责人依据目标与预算筛选。（须人工确认）\n"
    )
