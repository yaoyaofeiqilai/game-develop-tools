# Sprite Intelligence Workbench

面向 AI 生成游戏角色、精灵图和动画素材的本地智能处理工作台。

它不依赖固定网格切割，而是先处理整张图片的背景与 Alpha 蒙版，再识别角色主体、武器、飞散物、文字和特效等组件，推断帧锚点并生成可直接用于游戏动画的统一尺寸透明帧。对于模型判断不确定的结果，GUI 会保留人工核对和局部修正入口。

> 当前主要面向 Windows。已在 Windows 11、Python 3.12、NVIDIA RTX 4060、CUDA 13、cuDNN 9 和 ONNX Runtime GPU 环境验证。

详细操作、参数选择、局部修复和故障排查请阅读：[中文使用手册](docs/USER_GUIDE.zh-CN.md)。

## AI Skills

仓库的 `skills/` 目录收录了可交给 Codex、Kimi Code 等 AI Agent 使用的工作流说明。每个 Skill 都以 `SKILL.md` 为入口，描述适用场景、操作步骤、工具调用方式、验收标准和常见问题。

| Skill | 用途 | 适用场景 |
| --- | --- | --- |
| [`art-gen`](skills/art-gen/SKILL.md) | 游戏美术资产生成与修改 | 角色、怪物、场景、UI、特效、道具贴图和动画精灵表；包含生成通道路由、提示词规范、落盘规则与验收流程 |
| [`char-anim`](skills/char-anim/SKILL.md) | 重要角色动画生产 | 主角、Boss 等角色的走路、奔跑、跳跃、攻击、受击和倒地动画；覆盖图生视频、抽帧、抠图、对齐与 Godot 验收 |
| [`ai-sprite-workflow`](skills/ai-sprite-workflow/SKILL.md) | AI 精灵图后处理 | 对生成好的精灵表或动画帧执行背景移除、智能分帧、蒙版修复、锚点对齐、批量导出和历史清理，不负责生成原始美术 |
| [`bgm-gen`](skills/bgm-gen/SKILL.md) | BGM 与歌曲生成 | 使用本地音乐模型生成纯音乐、主题曲或角色歌，并完成试听验收与资产归档 |
| [`sfx-gen`](skills/sfx-gen/SKILL.md) | 游戏音效生成 | 生成打击、脚步、环境、技能和 UI 等音效，并按游戏资产要求验收、整理与归档 |
| [`char-voice`](skills/char-voice/SKILL.md) | 角色台词配音 | 复用已经建档的角色音色批量生成台词语音，也包含新角色首次建立音色的流程 |
| [`dp-game-server-ops`](skills/dp-game-server-ops/SKILL.md) | 游戏服务器部署与维护 | 服务器健康检查、版本化部署、日志诊断、systemd、MySQL、WebSocket 排障与回滚；实际连接参数通过本地配置提供 |

使用时，把需要的 Skill 目录放入 Agent 支持的 skills 目录，或让 Agent 直接读取对应的 `SKILL.md`。首次运行前应先查看 Skill 中的环境要求和本地配置说明；仓库内只保留可复用流程及配置示例，不包含实际账号、密钥或服务器连接信息。

这些 Skill 可以串联使用。例如，重要角色动画可以先由 `art-gen` 生成参考图，再由 `char-anim` 生成动作视频，最后交给 `ai-sprite-workflow` 完成抽帧后的透明背景处理、对齐和导出。

## 主要能力

- **整图智能抠图**：在连通色背景与 `rembg` 语义模型之间自动选择，默认模型为 `isnet-anime`。
- **绿幕/蓝幕净化**：默认开启；识别均匀高饱和背景，清除角色内部封闭孔洞，并对半透明边缘消绿或消蓝。
- **蒙版精修**：结合语义前景、背景颜色、局部平坦度和边缘羽化，处理头发、尾巴、文字孔洞及浅色边缘残留。
- **非规则帧识别**：不要求等宽、等距或规则网格，支持交错排列、分离道具和飞散特效。
- **人工修正**：支持点选清除/恢复、圈选恢复初抠结果、圈选局部重新抠图和组件归属调整。
- **动画组工作流**：文件夹递归导入、整组分析、前后帧切换、整组导出和 `result` 集中预览。
- **统一动画输出**：导出紧凑透明帧、统一画布帧、Alpha 蒙版、Sprite Sheet 和 manifest。
- **AI 协同接口**：提供确定性的 CLI、HTTP API 和 Codex Skill；任务可标记为 AI 处理并进入独立审核列表。
- **本地优先**：核心处理无需上传图片；输入、模型、任务历史、设置和导出结果都保存在本地。

## 界面概览

GUI 由素材库、中央预览和处理控制三个可调宽度区域组成，支持三套主题：

- 文艺纸刊（默认）
- 工业工作台
- 多巴胺乐园

素材、AI 处理记录和处理历史都会按动画组聚合并支持折叠。分析过的素材再次打开时会恢复最近结果，方便继续局部修正，而不是回到未分析状态。

## 安装

### 1. 环境要求

- Windows 10/11
- Python 3.12（推荐）
- Git
- NVIDIA GPU 为可选项；没有可用 CUDA Provider 时可以使用 CPU，但处理速度会明显降低
- 首次使用语义模型时需要下载模型文件

### 2. 克隆并安装依赖

```powershell
git clone <your-repository-url>
cd game-tool

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

当前 `requirements.txt` 使用 GPU 版 ONNX Runtime，并锁定已验证的版本。若使用纯 CPU 环境，请根据 ONNX Runtime 官方支持矩阵替换 GPU 包后再安装。

### 3. 启动 GUI

最简单的方式是双击：

```text
start_gui.bat
```

也可以在 PowerShell 中运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_gui.ps1
```

启动后访问：<http://127.0.0.1:7865>

如果首次下载模型遇到网络问题：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\start_gui.ps1 `
  -Proxy http://127.0.0.1:7897
```

## GUI 使用流程

1. 点击左侧“导入素材”，或把图片/文件夹拖入左侧导入区或中央画布。
2. 多张图片和文件夹会自动组成动画组，并保留子目录结构。
3. 已知帧数时填写“预期帧数”；未知时保持 `0` 让系统推断。
4. 保持蒙版精修为“保守”、智能绿幕为“标准”，运行智能分析。
5. 对比“原图 / 初抠 / 精修 / 蒙版 / 实例归属”。
6. 使用点选或圈选工具修复残留背景和误删前景。
7. 核对红色低置信组件，把它们分配给正确帧或忽略。
8. 导出单张结果，或在动画组模式下执行整组导出。

导出路径和内容类型按**源图片**分别记忆；同一图片重新分析产生新任务 ID 后仍会恢复自己的导出设置。动画组拥有独立的整组导出设置。

## CLI 与 AI 工作流

推荐使用 Skill 自带的客户端，它会在需要时自动启动本地服务，并输出适合 AI 继续处理的 JSON。

### 处理单张图片

```powershell
.\.venv\Scripts\python.exe `
  .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py `
  process .\input\hero.png `
  --expected-frames 6 `
  --output F:\game-assets\hero\attack
```

默认只交付 `frames_aligned`。可使用 `--artifact trimmed`、`--artifact sheet`、`--artifact masks`，或使用 `--all` 导出完整处理包。

### 导入并处理动画文件夹

```powershell
# 只导入并注册动画组
.\.venv\Scripts\python.exe `
  .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py `
  import-folder F:\generated\hero-walk `
  --collection hero-walk

# 批量分析并导出
.\.venv\Scripts\python.exe `
  .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py `
  process-folder .\input\hero-walk `
  --skip-existing `
  --output F:\game-assets\hero
```

通过 CLI 创建或导出的任务会带上 `ai` 标签，在 GUI 的“AI处理记录”中按照动画组归类。处理历史也使用相同的分组逻辑。

### 常用修正命令

```powershell
# 检查任务状态和待核对组件
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py inspect <job-id>

# 重新执行蒙版精修
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py refine <job-id> --mode balanced

# 清除一个颜色连通背景区域
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py mask-point <job-id> 420 180 background

# 恢复圈选范围内被误删的初抠结果
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py restore-region <job-id> "365,35;495,35;525,150;345,150"

# 对圈选范围重新进行局部背景移除
.\.venv\Scripts\python.exe .\skills\ai-sprite-workflow\scripts\sprite_workflow_client.py refine-region <job-id> "365,35;495,35;525,150;345,150" --strength strong
```

智能绿幕默认开启。需要加强时使用 `--chroma-strength strong`；角色本身包含与背景无法区分的绿色或蓝色时使用 `--no-smart-chroma`。

完整的 AI 操作规范见 [skills/ai-sprite-workflow/SKILL.md](skills/ai-sprite-workflow/SKILL.md)，HTTP 接口说明见 [skills/ai-sprite-workflow/references/api.md](skills/ai-sprite-workflow/references/api.md)。服务运行后也可以打开 <http://127.0.0.1:7865/docs> 查看交互式 API 文档。

## 处理流程

```text
源图
  → 初步背景移除 / Alpha 蒙版
  → 自适应绿幕或蓝幕净化
  → 语义前景保护 + 背景颜色建模
  → 封闭背景孔洞清理 + 边缘羽化
  → 前景连通组件识别
  → 帧主体候选与空间排序
  → 分离组件归属与置信度评估
  → 人工核对 / 局部蒙版修正
  → 统一锚点导出
```

相比固定网格，这套流程更适合不等距、不等宽、两行交错以及带飞散特效的 AI 图片。但如果相邻角色在源图中已经大面积粘连或互相遮挡，单张图片本身没有足够信息保证完全自动恢复，此时仍需要人工核对或重新生成素材。

## 输出结构

```text
output/<素材或动画组>/
├── frames_trimmed/      # 每帧紧凑透明图
├── frames_aligned/      # 统一画布、底部居中，推荐制作动画
├── masks/               # Alpha 蒙版
├── sprite_sheet.png
├── manifest.json
└── result/               # 动画组集中预览

workspace/jobs/<任务 ID>/
├── cutout_initial.png   # 保留的初抠结果
├── cutout.png           # 当前精修结果
├── semantic_mask.png
├── foreground_mask.png
├── component_map.png
├── overlay.png
└── job.json
```

`frames_trimmed` 保留每帧最紧凑的透明边界，尺寸可能不同；`frames_aligned` 使用统一画布和底部中心锚点，更适合直接制作角色动画。

## 项目结构

```text
game-tool/
├── gui_server.py                 # FastAPI 服务和本地素材管理
├── smart_engine.py               # 智能抠图、精修、实例归属和导出
├── sprite_workflow.py            # 基础 CLI 与文件夹监控流程
├── web/                           # 原生 HTML/CSS/JavaScript GUI
├── skills/                       # AI 美术、音频、动画和服务器运维 Skills
│   └── ai-sprite-workflow/       # 精灵图工作流及自动化客户端
├── tests/                         # 单元与回归测试
├── test/                          # 小型人工回归素材
├── start_gui.bat / .ps1          # GUI 启动入口
├── process.ps1                   # 传统单次处理入口
└── watch.ps1                     # input 文件夹监控入口
```

以下目录属于本地运行数据，并已由 `.gitignore` 排除：

- `.venv/`：Python 虚拟环境
- `.models/`：下载的 AI 模型
- `input/`：用户导入素材
- `output/`：导出结果
- `review/`：待审核结果
- `workspace/`：任务历史、设置、回收站和导出路径记忆

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试覆盖文件夹导入与导出、动画组路径、历史记录、局部精修、智能绿幕、CLI 参数和可恢复回收站等关键流程。
