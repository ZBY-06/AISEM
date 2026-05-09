# AI 辅助 SEM 图像分析与实验报告生成系统（本地原型）

## 项目位置（不要再用桌面上的示例路径）

实际工程目录为本机上的 **`sem_ai_report`**，例如：

```text
d:\ZBY\Study\竞赛\全国大学生物理实验竞赛\前沿物理\sem_ai_report
```

（若你的盘符或上级文件夹不同，以 Cursor / 资源管理器里 **`app.py` 所在文件夹** 为准。）

## 目录结构

```text
sem_ai_report/
├── app.py                 # Streamlit 页面入口
├── image_metrics.py       # 读图、灰度、图像 proxy 指标
├── openai_vision.py       # OpenAI Responses API 结构化分析
├── report_generator.py    # 实验报告正文拼接
├── docx_exporter.py       # Word 导出
├── prompts.py             # 提示词
├── utils.py               # 常量、API Key、缩放、base64、写入 outputs/
├── requirements.txt
├── README.md
├── .streamlit/
│   └── config.toml       # 主题色（需重启 Streamlit）
└── outputs/               # 每次完整运行后自动保存 CSV / JSON / docx 副本（成功时）
```

## 第一版需求对照（路线 B：保留多文件）

| 需求 | 实现要点 |
|------|-----------|
| 本地 Streamlit；不接 SEM / 不控镜 / 无深度学习训练 | ✅ |
| 无数据库、无登录、无云部署、非前后端分离 | ✅ |
| 多图上传 tif/tiff/png/jpg/jpeg；预览 | ✅ `image_metrics.load_uploaded_image` + Pillow |
| 样品信息：含 **图注** 与 **备注** 分列 | ✅ 分页② |
| proxy 指标表 + CSV | ✅ |
| 本地模板报告 + 「不确定性与人工复核」 | ✅ `report_generator` |
| OpenAI 可选；无 Key 可完整运行 | ✅ 侧栏开关 |
| Word（及 Markdown）导出 | ✅ `docx_exporter` + `outputs/` |

业务叙事占位：**原料形貌 → 烧结 → 终态微结构 → 电化学性能** 仅作文本链条提示；工具 **禁止编造 XRD/BET/EIS/电化学数值**（见报告第五节「数据诚信」）。

## 环境要求

- Python 3.10+（建议 3.11）
- 仅本地运行：无数据库、无 FastAPI / Flask / React

## 安装

**PowerShell（示例）：**

```powershell
cd "d:\ZBY\Study\竞赛\全国大学生物理实验竞赛\前沿物理\sem_ai_report"
conda activate semreport
pip install -r requirements.txt
```

若使用 **venv** 亦可，保证 **`pip install` 在同一环境中执行**。

## 配置 API Key（可选）

1. 环境变量：`OPENAI_API_KEY`
2. 网页侧栏密码框（仅会话内）

可选：`OPENAI_SEM_VISION_MODEL`（默认 `gpt-4o-mini`，须账号支持视觉）。

## 运行

**必须先 `cd` 到包含 `app.py` 的 `sem_ai_report` 目录**，再执行：

```powershell
python -m streamlit run app.py
```

浏览器一般为 **http://localhost:8501**。

界面主题由 **`sem_ai_report/.streamlit/config.toml`** 控制；修改该文件后需 **重新启动** Streamlit 进程才会生效。

## 界面结构（7 个分页）

主界面使用 tabs：**① 项目说明 → ⑦ 下一轮实验建议**。在 **③** 上传图片并点击运行后，结果写入会话状态，请在 **④～⑦** 查看指标、AI JSON、报告下载与实验建议草稿。

侧栏：

- **启用 AI 视觉分析**：逐图结构化 JSON（需 Key）。
- **启用下一轮实验建议**：调用 Responses 文本接口生成假设性实验规划（需 Key）；关闭时使用本地占位模板（仍**不编造**测试数值）。
- **生成 Word 报告**：失败时页面会提示，仍可下载 Markdown。

## outputs 说明

每次点击「运行分析并生成报告」且流程跑通后，会在 **`outputs/`** 下额外写入带时间戳的文件（与页面下载按钮并存）：

- `{时间戳}_metrics.csv`
- `{时间戳}_ai_analysis.json`（仅启用 AI 且生成 JSON 成功时）
- `{时间戳}_report.docx`（仅勾选 Word 且生成成功时）
- `{时间戳}_report.md`（完整 Markdown 报告）

写入失败时页面会 **warning**，不影响 CSV/下载按钮等其余功能。

## 说明

- 所有数值指标均为 **辅助性图像 proxy**，不是最终物理结论：
  - `dark_area_ratio` → **暗区面积比例 proxy**，禁止解释为**孔隙率**；
  - `edge_density` → 反映**图像边缘丰富程度**，禁止等同于**颗粒边界数量**；
  - `sharpness_laplacian_var` → **清晰度质控 proxy**，不作为分辨率或粒径度量。
- 未提供可靠比例尺或可核验 **pixel size** 时，禁止输出 **微米单位**的粒径、孔径、裂纹长度；AI 提示词同步约束。
