# AI 精灵图智能分割工作台

这套工作流面向布局不稳定的 AI 精灵图。它不再先按固定尺寸切格，而是先对整张图抠图，再识别角色主体、武器、飞散物、文字气泡和特效等独立组件，推断帧锚点，并把分离组件归到最合理的帧。低置信结果会进入可视化核对队列，避免“静默切错”。

## 已实现能力

- 整图抠图：自动在连通色背景和 `rembg` 之间选择，默认插画模型为 `isnet-anime`。
- 蒙版精修：融合语义蒙版与画布颜色，清除头发、尾巴、文字内部的封闭背景，并对边缘羽化、去除浅色污染。
- 点选修正：在“精修”视图点击残留背景或误删前景，只更新一个颜色连通区域，无需重跑 AI 模型。
- 圈选恢复：强力精修误删多颜色发饰、头发或服装时，用套索圈住局部并从保留的初抠图恢复；圈外结果完全不变。
- 圈选精修：全局强力模式仍有残留时，圈住局部并对原图裁片重新执行任务原有的背景移除；局部模型只提出候选，系统再用整图画布颜色、平坦度和语义前景置信度过滤，只清除圈内有背景证据的像素。
- 智能帧识别：不要求等宽、等距或规则网格，可自动推断行列和帧数。
- 实例归属：把与角色分离的武器、纸张、爆炸碎片、运动线等归入对应帧。
- 置信度核对：可在 GUI 中点击任一彩色组件，改归属或忽略。
- 动画对齐：导出透明紧凑帧、统一尺寸底部居中帧、逐帧蒙版、Sprite Sheet 和 manifest。
- 动画组导入：统一“选择素材”入口，拖放时自动判断图片或文件夹；保留子目录结构，并提供画布侧边导航。检测到动画组后，整组分析、整组导出、批处理进度与 result 预览统一进入右侧底部悬浮操作坞，不会随参数和结果内容滚出视野。
- 素材历史管理：AI 处理记录与普通处理历史使用处理后透明图作为缩略图，缺失时回退到源图；素材与处理历史可多选移入项目回收站，测试样例默认隐藏，导出成品不受影响。
- 三种入口共用同一引擎：GUI、命令行/文件夹监控、HTTP API。
- 本地优先：核心流程无需联网；RTX 4060 上已验证 CUDA 13 + cuDNN 9 + ONNX Runtime GPU。

## 启动 GUI

双击：

```text
start_gui.bat
```

或在 PowerShell 中运行：

```powershell
.\start_gui.ps1
```

浏览器会打开 `http://127.0.0.1:7865`。左侧素材库默认只展示 `input` 和处理历史，开发用 `test` 样例不会干扰日常使用。GUI 的标准操作顺序是：

1. 点击左侧唯一的“导入素材”键可一次选择单张或多张图片：单张直接打开，多张自动进入动画组确认。也可以把图片或整个文件夹直接拖入左侧导入区或中央工作区，系统会递归识别并自动判断单张、多张或文件夹。文件夹与多图导入前会显示有效图片、子目录、总大小和跳过项，可修改动画组名称后确认。
   素材组会按最近导入或修改时间排列；点击“输入队列”“动画组”“AI 处理记录”或“处理历史”的分组标题可折叠/展开，折叠状态会在当前浏览器中保留。动画组内部仍按文件名自然排序，以维持正确帧序。由 AI 命令行客户端创建或导出的任务会自动带上 `ai` 标签，并集中进入“AI处理记录”页签，仍可打开继续局部修正或管理。
2. 导入动画组后，左侧只展示组名、当前位置与素材；上一张、下一张位于中央画布两侧，也可使用 `← / →` 方向键。已有分析任务的图片会自动恢复最近一次分析和局部修改结果，不会退回未分析界面。右侧底部悬浮操作坞会同时显示“运行智能分析”“整组分析”和“导出整组”；上方参数与结果区可独立滚动。处理中可选择“完成当前后停止”。
3. 若生成端知道帧数，填入“预期帧数”；不知道就保持 0。整组分析会把当前提示应用到组内所有图片。
4. 蒙版精修默认使用“保守”，优先清理角色边缘的背景色残留并保护语义前景；需要完全保留初抠结果时可选择“关闭”，确认仍有残留后再选择“标准”或“强力”。
5. 点击“运行智能分析”，在“初抠 / 精修 / 蒙版”之间对比结果。
6. 若仍有封闭背景，选择“清除背景”并在精修图中点击；单色小块误删可用“恢复前景”。
7. 若强力精修误删了头发、发饰等多颜色结构，选择“圈选恢复初抠”，按住鼠标围住目标，松开后点击“应用恢复”。
8. 若全局强力精修后仍有背景，选择“圈选精修”，圈住残留并选择程度后应用。建议从“标准”开始；强度越高，允许识别的浅色背景范围越大，但明显偏离画布颜色或语义置信度高的角色像素仍会受到保护。
9. 在“实例归属”视图检查彩色边框；红色边缘表示建议核对，不等于一定出错。
10. 点击组件编号或直接点击画布中的组件，指定到 F1、F2……，也可以忽略。
11. 单张或整组导出时可直接输入目标目录，也可点击“选择文件夹”打开 Windows 文件夹选择器。路径和内容类型按“源图片”分别记忆，而不是全局复用最近一次设置：同一张图片重新分析、产生新任务 ID 后仍会自动带回自己的导出设置，切换到另一张素材不会串用路径；动画组另有独立的整组设置。默认只交付 `frames_aligned`，也可选择紧凑帧、Sprite Sheet、蒙版或完整处理包。动画组会保留素材的相对目录，避免同名帧覆盖；`result` 仍集中提供 Sprite Sheet 预览。导出完成后的打开文件夹按钮会新建并置前资源管理器窗口。
12. 要整理素材时点击左栏“管理”，可跨“素材 / AI处理记录 / 处理历史”多选并移入回收站。删除素材时默认连同对应历史一起整理；导出目录保持不变，原文件保存在 `workspace/trash/<时间批次>/`，便于误操作后找回。

“行/列”只是可选提示，不再是切割边界。对于 AI 生成流程，优先传入已知的 `expected_frames`，通常比二次猜测帧数更可靠。

### 界面主题

点击右上角设置按钮，在“界面风格”中可以实时预览并切换：

- **工业工作台**：深色、紧凑、强调信息密度。
- **多巴胺乐园**：糖果黄、钴蓝、亮粉和薄荷绿，使用更大胆的圆体与重黑展示字体。
- **文艺纸刊（默认）**：暖纸、墨色、朱砂与松绿，采用楷体、宋体和衬线字体组合。

点击“保存设置”后，主题会同时保存到服务配置和浏览器本地缓存，重新启动仍会恢复；点击“取消”则撤销本次实时预览。主题只改变显示效果，不影响分割参数、任务状态和导出结果。

桌面宽度大于 860 px 时，素材队列、中央画布和处理面板之间有两条可拖拽边界栏，可像 Windows 窗口一样无级调整宽度；双击边界栏恢复默认宽度，聚焦边界栏后也可用方向键微调。布局会保存在当前浏览器中。

## 智能分割原理

```text
整张源图
  → 初步背景移除 / Alpha 蒙版
  → 语义前景保护 + 全图背景颜色建模
  → 封闭背景孔洞判断
  → Trimap 式边缘羽化 + RGB 去白边
  → 8 邻域前景组件标记
  → 主体候选 + 空间非极大抑制
  → 自动行列排序与帧锚点
  → 分离组件按距离、尺度、方向和归一化代价归属
  → 置信度与人工核对队列
  → 统一锚点导出
```

这比固定网格更适合不等距、不等宽、两行交错以及带飞散特效的图片。它也有明确边界：如果两个相邻帧在原图里已经大面积粘连、互相遮挡，单张合成图不存在足够信息保证全自动恢复。此时应传入帧数提示并在 GUI 核对；生产生成端最好保留最小帧间距或直接逐帧生成。

## 命令行与监控

处理单图，帧数完全自动识别：

```powershell
.\process.ps1 -InputPath .\test\cruncher2-death.png
```

已知是 6 帧时：

```powershell
.\process.ps1 -InputPath .\test\cruncher2-death.png -ExpectedFrames 6
```

监控 `input`，新文件自动处理：

```powershell
.\watch.ps1
```

旧版固定网格仍可兼容调用：

```powershell
.\process.ps1 -InputPath .\test001.png -Segmentation grid -Rows 2 -Columns 3
```

直接调用 Python 可使用灵敏度、置信线、模型和推理后端等完整参数：

```powershell
.\.venv\Scripts\python.exe .\sprite_workflow.py --help
```

## 接入 AI 工作流

GUI 服务同时提供稳定的自动化入口：

```http
POST http://127.0.0.1:7865/api/v1/process
Content-Type: application/json

{
  "source_path": "input/generated_sheet.png",
  "expected_frames": 6,
  "background_mode": "auto",
  "background_model": "isnet-anime",
  "provider": "auto",
  "refine_mode": "conservative",
  "refine_tolerance": 18,
  "matte_width": 3,
  "export": true
}
```

响应包含完整 job、帧布局、组件归属、核对列表和导出路径。若要做人工审核闭环，可依次调用：

- `POST /api/v1/import-folder`：显式导入完整动画文件夹，递归保留相对目录结构；请求字段为 `folder_path`、可选 `collection` 和 `overwrite`，响应会返回可直接交给处理接口的 `sources` 列表。
- `POST /api/analyze`：只分析，不导出。
- `POST /api/upload`：上传单张图片；文件夹导入时额外提交 multipart 字段 `relative_path`，服务会安全保留 `input` 下的相对目录结构并自动处理重名。
- `POST /api/library/trash`：把指定 `source_paths` 和 `job_ids` 移入项目回收站；`include_related_jobs` 控制是否同时整理素材对应的处理历史，导出成品不会被删除。
- `GET /api/jobs/{job_id}`：读取任务和当前核对状态。
- `POST /api/jobs/{job_id}/refine`：从保留的初抠图重新精修蒙版，不重跑模型。
- `POST /api/jobs/{job_id}/mask-point`：按源图坐标清除背景或恢复前景颜色连通区域。
- `POST /api/jobs/{job_id}/mask-region`：按源图多边形范围从初抠图局部恢复，并羽化圈选边缘。
- `POST /api/jobs/{job_id}/refine-region`：裁出源图多边形周围的上下文，重跑原背景移除器和指定强度精修，只把圈内透明度降低结果写回。
- `POST /api/jobs/{job_id}/assign`：提交 `component_id` 与 `frame_id`，`frame_id=0` 表示忽略。
- `POST /api/jobs/{job_id}/export`：支持 `output_path`、`artifacts` 与 `ai_tag`。省略路径或内容字段时会按源图片恢复上次设置；该图片尚无记录时默认交付 `aligned` 到项目输出目录。显式传入空路径可把该素材恢复为项目默认输出位置。
- `POST /api/jobs/{job_id}/open-output`：新建并置前 Windows 资源管理器窗口，打开当前任务已经导出的目录；尚未导出时返回提示。
- `POST /api/collections/export`：按动画组导出所有最近任务，支持自定义目标目录与内容类型，并分别返回成功、未分析、待核对跳过和失败项；整组导出设置按动画组单独记忆。
- `GET /api/collections/{collection}/export-profile`：读取该动画组上次使用的整组导出路径与内容类型，供 GUI 或集成端预填。
- `POST /api/collections/open-output`：新建并置前 Windows 资源管理器窗口；`view=result` 打开动画组集中预览目录，`view=root` 打开总输出目录。
- `GET /docs`：FastAPI 自动生成的可交互接口文档。

API 为本机工具设计。分析接口只读取 `F:\game-tool` 项目目录内的图片；`/api/v1/import-folder` 是唯一用于把指定本地文件夹复制进 `input` 的导入边界。

## AI Skill

项目已封装为 Codex Skill：`$ai-sprite-workflow`。它会调用本地服务完成智能分割、核对组件归属与导出；服务未启动时，客户端会自动使用 `start_gui.ps1 -NoBrowser` 启动。

在 AI 对话中可直接描述目标，例如：

```text
$ai-sprite-workflow 处理 F:\game-tool\input\generated_sheet.png，预期 6 帧，完成后告诉我需要人工核对的组件。
```

Skill 源码位于 `skills/ai-sprite-workflow`。也可以直接使用其确定性命令行客户端：

```powershell
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py import-folder F:\generated\hero-attack --collection hero-attack
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py process-folder .\input\hero-attack --expected-frames 6 --output F:\godot-game\assets\hero
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py process .\input\generated_sheet.png --expected-frames 6 --output F:\godot-game\assets\hero\attack
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py refine <job_id> --mode balanced --tolerance 18 --matte-width 3
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py mask-point <job_id> 420 180 background
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py restore-region <job_id> "365,35;495,35;525,150;345,150" --feather 4
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py refine-region <job_id> "365,35;495,35;525,150;345,150" --strength strong --feather 4
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py inspect <job_id>
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py export <job_id> --output F:\godot-game\assets\hero\attack
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py export <job_id> --output F:\archive\hero\attack --all
```

CLI 的 `process`、`process-folder`、`export`、`export-folder` 均支持 `--output`。每次显式指定的 `--output` 与导出内容都会绑定到对应源图片；之后重新处理同一路径的图片时，可以省略 `--output` 和 `--artifact`，服务会自动复用该图片自己的设置。新图片没有历史记录时才默认交付 `frames_aligned` 到项目输出目录。使用 `--artifact sheet` 等参数可选择单项，重复 `--artifact` 可组合，`--all` 导出完整处理包。JSON 输出中的 `delivery_dir` 是可以直接交给后续 AI 或游戏工作流的最终路径，不再需要额外复制脚本。

处理项目目录外的图片时显式加入 `--import-external`，客户端会先复制到 `input`，避免本地 API 越界读取。

## 输出结构

```text
output/<图片名>/
├── frames_trimmed/    # 每帧紧凑透明图
├── frames_aligned/    # 统一画布、底部居中，推荐制作动画
├── masks/             # 每帧全尺寸 Alpha 蒙版
├── sprite_sheet.png   # 由 frames_aligned 生成
└── manifest.json      # 原始框、组件 ID、对齐偏移、底部中心锚点

workspace/jobs/<任务 ID>/
├── cutout_initial.png # 保留的初抠结果，可重新精修
├── cutout.png
├── semantic_mask.png  # 语义前景保护蒙版
├── foreground_mask.png
├── component_map.png  # GUI 像素级点选所用的组件 ID 图
├── overlay.png        # 帧边框、组件颜色和低置信边缘
└── job.json           # 可恢复、可继续修改的任务状态

workspace/export-profiles.json  # 按源图片/动画组保存的导出路径与内容类型
```

## 模型、GPU 与代理

- Python 虚拟环境：`.venv`
- 模型缓存：`.models`
- 默认模型：`isnet-anime`
- 默认推理：`auto`，优先 `CUDAExecutionProvider`，保留 CPU 回退

首次下载其他模型遇到网络问题，可在 GUI 右上角设置代理，或启动时传入：

```powershell
.\start_gui.ps1 -Proxy http://127.0.0.1:7897
```

代理设置只用于当前本地服务进程。视觉大模型配置是后续增强层的预留项，当前核心实例分割不依赖外部 API，也不会把图片上传到网络。

## 回归样例

当前 `test` 中 7 组素材已做自动帧数回归，识别结果依次为：`14、8、6、6、6、8、6`；`input/generated_fox_2x3_test.png` 自动识别为 `2×3 / 6 帧`。不规则死亡动画、横向长条、爆炸碎片和分离道具均包含在样例中。
