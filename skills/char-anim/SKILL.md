---
name: char-anim
description: DP-Game 角色动画生产管线(方案C:AI图生视频→抽帧→抠图→对齐→Godot验收)。仅用于主角团/Boss等重要角色的动作动画(走路、奔跑、跳跃、攻击、受击、倒地等帧序列);小怪等非重要动画资产不走本流程,直接用 art-gen 的精灵表方案。前身方案A(骨骼重定向)/B(部件拆分)已废弃,勿回退。
---

# DP-Game 角色动画管线(方案 C,当前主力)

> **适用范围**:仅主角团、Boss 等重要角色的动作动画(质量优先,H3 视频流程成本高、需人工触发)。
> 小怪、NPC、杂兵等非重要动画资产**不走本流程**,直接用 art-gen skill 的精灵表切割方案
> (一张精灵表出全帧,成本 2.5 积分,量大管饱)。拿不准算不算"重要"时问用户。

> 若项目另有完整设计文档,可把它作为补充背景；本 skill 自身应包含完成流程所需的操作信息。

## 管线总览(2026-09-01 起,本地 ComfyUI 版)

```
角色参考图(whale-maid/ref/) ──► comfy_client.py gen(本地 ComfyUI API,MiniMax H3 图生视频)
  ──► 动作视频 mp4 ──(extract_frames.py,PyAV 均匀采样/全抽)──► PNG 序列
  ──(ai-sprite-workflow process-folder:去底+检测+对齐+导出)──► 透明帧序列
  ──► godot-resource/characters/<角色>/<动作>/ + 同步 game-client/assets/sprites/
  ──► Godot demo 截图验收
```

## 各步操作要点

### ① 参考图/首尾帧(art-gen skill 的 web premium 通道)

- 角色一切衍生以 `godot-resource/characters/<角色>/ref/` 里的参考图为准,保证一致性
- 首尾帧提示词必须写明:严格侧面、全身完整、角色居中、纯白背景、无阴影无地面
- 提示词前缀必须全新(防网站抓历史旧图);需要代理时通过 `ARTGEN_PROXY_URL` 显式配置

### ② H3 视频生成(2026-09-01 起:本地 ComfyUI API,AI 全自动)

本地 ComfyUI(`<COMFYUI_ROOT>`,须常驻 127.0.0.1:8188)部署了 MiniMax H3,
AI 直接通过 API 提交/轮询/下载,**不再需要用户在网页人工操作**:

```bash
# 提示词写进文件再提交(避免引号/换行被 shell 吃掉)
python scripts/comfy_client.py gen \
    --prompt-file <提示词.txt> --ref <角色参考图.png> --seconds 3 --out <输出.mp4>
# --turbo = 8步草稿;默认 20 步质量档。--seed N 固定种子复现;--ratio "9:16 (Portrait)" 跳跃类竖屏
```

- 工作流模板:`templates/h3_i2v.json`(从用户已验证工作流抓取,**只改参数不改结构**)
- 提示词要点(沿用):固定镜头、纯白背景不变、原地动作、角色居中、不变大小/朝向/表情、无特效无新元素
- ComfyUI 不在线时脚本会直接报错并提示启动 `run_nvidia_gpu.bat`
- 旧链路(MiniMax 网页/海螺人工触发)降为**备用**:本地服务不可用或要 15s 长视频多动作时才用

### ③ 抽帧(Windows,PyAV,零安装)

```bash
# 必须用 ComfyUI 自带 python(内置 PyAV):
<COMFYUI_ROOT>/python_embeded/python.exe \
    scripts/extract_frames.py \
    --src <视频.mp4> --dst <帧目录> --frames 8          # 均匀采样 8 帧
# --frames 0 = 逐帧全抽(位移/单次类动作先全抽,导出给用户圈区间);--start/--end 按秒截段
```

- 循环类动作(walk/idle/run):**均匀采样至少 24 帧**(2026-09-01 用户定:重要动画帧数不得低于 24,3s@24fps 视频约 73 帧,采样 24~32 帧保流畅);需要更精细循环区间时仍可全抽后人工圈定
- 位移/单次类动作(jump/attack/fall):**整段全抽,导出全部帧让用户逐帧审片圈定区间**
  (jump 最终就是用户从 107 帧里圈 14-94;自动剔除坏帧会误伤节奏,被否决过)

### ④ 后处理(统一走 ai-sprite-workflow skill,旧脚本已废弃)

```bash
cd <SKILLS_ROOT>/ai-sprite-workflow
python scripts/sprite_workflow_client.py process-folder <帧目录> --skip-existing
# 蒙版局部修复:refine / mask-point / restore-region / refine-region(见其 SKILL.md)
```

- 去底/帧检测/对齐/导出一条龙,交互审查界面 `http://127.0.0.1:7865`
- **位移类动作注意角色大小一致性**:以 idle 站立帧为身高基准,导出后跨动作对比,
  不满意直接对整段帧图等比缩放(PIL resize LANCZOS),demo 按纹理实际高度底部对齐

### ⑤ 入库 + Godot 验收

- 资产库:`<ASSET_REPOSITORY_ROOT>/characters/<角色>/<动作>/0.png…N.png + preview.gif`
- 同步副本:`<GAME_PROJECT_ROOT>/assets/sprites/<角色>/<动作>/`
- 验收:启动编辑器 → `filesystem_manage scan` → `project_run` → `editor_screenshot source=game`
  (demo:`scenes/demo_character.tscn` 移动组 / `scenes/demo_battle.tscn` 战斗组)
- 向左移动 = `flip_h` 水平翻转,不重复生成

## 关键坑(都踩过,勿再犯)

1. **帧图复制/重编号严禁 `*.png` glob 顺序**(bash/PIL 都是字典序 `0,1,10,11,…,2,20`,
   表现为"每隔 11 帧跳回开头姿势")。sprite_pipeline.py 已内置数值排序;手写脚本仍要用
   `for i in $(seq A B)` 数值循环或 `key=int` 排序
2. **bbox 必须用阈值化 alpha(>32)**:rembg 残留的半透明噪点会撑大 getbbox() 导致角色被缩小
3. **H3 会生成逆行坏帧**(上升段插入深蹲帧,表现为上下抖动):逐帧算 alpha>200 重心 y,
   与邻帧均值偏差>40px 且逆趋势即坏帧;但优先整段重提+人工圈区间,不轻易自动剔帧
4. **区分 Git Bash 与 WSL 路径语义**:Windows Python 应使用 `C:/...` 形式的绝对路径；
   WSL 工具通过 `wsl bash <脚本>` 调用,不要把 `/c/...` 一类路径传给 Windows Python
5. 生成/处理完图片必须 ReadMediaFile 亲眼验收(大图先拼图降采样),preview.gif 太大读不了
   就抽帧拼九宫格 sheet
6. 多步图像处理写成 .py 脚本文件再跑,避免 shell 引号吃掉 `$变量`
7. **泛洪 tol=24 会吃浅肉色肢体**(手部高光 sum≈695 ≥ 阈值 693,attack_up/4、attack_down/13
   断手事故;断面平直、画布边缘无残留像素是特征):手前伸/上举类动作用 `--tol 12` 收紧判定。
   修复时须整段重跑,并用"同帧新旧内容高对比"反推 --height 保持角色等大
8. **清帧内枪口闪光严禁矩形框选**(attack_down/12 框选误删整只手):先用连通域分析定位——
   独立特效块直接删(注意 3% 去孤岛阈值会漏放大块);与手粘连的光球用"核心亮区→膨胀出
   ROI→ROI 内颜色分离(B-R 差)";外围灰雾/白芯要按实际像素值(可能 al=255 纯白)补刀,
   每步清除后 ReadMediaFile 验收手部完好

## 环境备忘

**脚本已全部内嵌在本 skill 的 `scripts/` 目录(正本,改脚本就改这里)**:

| 脚本 | 运行环境 |
|---|---|
| `scripts/comfy_client.py` | 任意 Windows python(纯标准库);ComfyUI 须在 127.0.0.1:8188 常驻 |
| `scripts/extract_frames.py` | **必须用 ComfyUI 自带 python**(内置 PyAV):`<COMFYUI_ROOT>/python_embeded/python.exe` |
| `templates/h3_i2v.json` | H3 图生视频工作流模板,只改参数不改结构 |
| ~~`scripts/sprite_pipeline.py`~~ / ~~`batch_rembg.py`~~ | **已废弃(2026-09-01)**:后处理统一走 ai-sprite-workflow skill |
| `scripts/loop_detect.py` | 备用,精细循环区间分析;WSL conda env `animated_drawings` |
| 生图(gen_image.py 等) | 在 art-gen skill 的 `scripts/` 目录,见 art-gen skill |
| Godot 编辑器 | `"<GODOT_EXECUTABLE>" --editor --path "<GAME_PROJECT_ROOT>"` |

> 若其他项目目录仍保留历史副本,以本 skill 目录为准；修改脚本后避免维护多个不一致的正本。

## 已验收产物(whale-maid 八动作,可作格式参照)

walk 31 / idle 47 / run 18 / jump 81(418×644) / attack 25 / hit 15 / fall 26 / lying 11,
均在 `godot-resource/characters/whale-maid/<动作>/`,循环类 fps≈15、位移类 fps≈20。

## 协作纪律

- 视频生成已由本地 ComfyUI 全自动接管;MiniMax 网页/海螺人工触发仅作备用(本地不可用或 15s 长视频)
- 发现更成熟方案或不合理处,主动提出给用户选择,不闷头执行(用户明确要求)
- 废弃中间产物(raw 抽帧/cutout/分段目录)在用户确认后清理;源视频和 ref 参考图永远保留
