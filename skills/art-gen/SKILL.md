---
name: art-gen
description: DP-Game 美术资产生成管线。当任务涉及生成/修改任何游戏美术资产(角色、怪物、场景、UI、特效、道具贴图、动画精灵表)时使用。内含模型分级路由规则、资产目录规范、验收闭环。主力通道是绿叶AI网站自动化(web),API 通道为备用。
---

# DP-Game 美术生成管线(AI 使用指南)

## 通道路由(2026-08-22 起:网站为主力,API 为备用)

| 分级 | 通道 | 成本 | 用途 |
|---|---|---|---|
| `draft`(默认) | **web**(绿叶AI lyaiapp.com) | 2.5 积分/张 | 普通资产:小物件、普通小怪、道具、**动画精灵表** |
| `premium` | **web** + `--ref` 参考图 | 2.5 积分/张 | 重要资产:主角团、Boss、重要场景、角色一致性衍生 |
| `ui` | jimeng(即梦4.0) | 免费试用→¥0.2/张 | UI 图标、按钮、装饰(限 1 并发,禁并行) |
| `backup_lite` | plan(Seedream 5.0 Lite API) | 套餐额度≈免费 | **仅当 web 通道失效时**的普通资产备用 |
| `backup_pro` | standard(Seedream 5.0 Pro API) | **¥1.8/张** | **仅当 web 通道失效时**的重要资产备用 |

- 网站通道实测质量:角色一致性优秀(参考图衍生)、精灵表直接可用,已取代 API 成为主力
- 备用通道启用条件:webgen 连续失败(网站改版/封号/积分耗尽),并在任务记录中说明
- jimeng 通道限 1 并发:多 agent 并行时,jimeng 任务串行;web 通道同时只能跑一个(共享 Edge profile)

## web 通道使用前提(重要)

- **Edge 浏览器必须完全退出**(含后台进程),否则 profile 锁报错。检查:`tasklist //FI "IMAGENAME eq msedge.exe"`
- 登录态复用用户 Edge profile;若网站要求重新登录,停下来让用户处理
- 页面改版会导致失败:选择器集中在 `scripts/webgen.py` 顶部的 `SITE` 字典,按新页面结构修

## 调用方式

**所有脚本已内嵌在本 skill 的 `scripts/` 目录(正本)**:`gen_image.py`、`webgen.py`、`jimeng.py`、
`models.json`、`sprite_pipeline.py`、`extract_parts.py`。复制
`scripts/artgen.local.example.json` 为 `scripts/artgen.local.json` 后再填写本机凭据；本地配置已被
`.gitignore` 排除,不得提交或分享。配置与模型均按 `__file__` 相对定位,可从任意工作目录运行:

```bash
SKILL_SCRIPTS="<SKILLS_ROOT>/art-gen/scripts"
ASSET_ROOT="<ASSET_REPOSITORY_ROOT>"
# 普通资产(默认 draft → web 低价线路,自动/1K)
python "$SKILL_SCRIPTS/gen_image.py" --prompt "<描述>" --out "$ASSET_ROOT/<分类>/<名称>.png"
# 重要资产/角色衍生/精灵表(premium → 稳定线路十七 2K 16:9,10积分)
python "$SKILL_SCRIPTS/gen_image.py" --tier premium --ref "<参考图路径>" --prompt "<描述>" --out "<路径>"
# 更高分辨率:--res 4K;换宽高比:--ratio 16:9(精灵表务必宽幅,1:1 会裁掉帧)
# UI 图标(即梦)
python "$SKILL_SCRIPTS/gen_image.py" --tier ui --prompt "<描述>" --out "$ASSET_ROOT/ui/<名称>.png"
# 备用通道示例
python "$SKILL_SCRIPTS/gen_image.py" --tier backup_lite --prompt "..." --out "..."

# 批量模式(多张图首选!一次浏览器会话跑完,省时约 35%+)
python "$SKILL_SCRIPTS/gen_image.py" --batch jobs.json
# jobs.json: [{"prompt":"...","out":"...","tier?":"draft","ref?":"...","res?":"2K","ratio?":"16:9"}, ...]
```

> 速度实测:单张含浏览器启动约 62s;批量 3 张共 131s(约 44s/张),图越多越省。
> 批量任务单个失败不会中断整批,失败列表最后汇总。

### web 通道线路实测(2026-08-22)

| 线路 | 积分 | 分辨率 | 用途 |
|---|---|---|---|
| Image2低价一(draft) | 2.5 | 自动/1K | 普通资产 |
| Image2稳定线路十七(premium) | 10(2K)/ 16(4K) | 2K/4K | 重要资产/精灵表 |
| Image2稳定线路十四 | 14 | 2K/4K | 备选 |
| ~~Image2稳定线路八~~ | 8 | 标称124k但 2K/4K 实际禁用 | 勿用 |

- 4K 实测:16:9·4K 收费 16 积分,出图 3840×2160;**站点会先出半分辨率预览再换全分辨率图**,webgen 已内置"等 naturalWidth 稳定再下载"逻辑,勿简化这段代码
- 复杂动作序列帧提示词写法已验证:"从X到Y的N个连续动作帧,动作夸张可爱,表情从A到B"——参考 `characters/whale-maid-fall-4k.png`(跑步→被绊→扑倒→摔懵星星眼,5帧)
- **结果检测以"生成记录区顶部记录 + 提示词前缀匹配 + 耗时完成"为准**(预览区 img 不会更新、完成计数受懒加载干扰,两种写法都踩过坑,勿回退 `webgen.py` 的 `_wait_result_url`)
- 多张图必须用批量模式(`--batch jobs.json`):共享一次浏览器会话、缓存线路选择、单任务失败不中断;实测单张 62s vs 批量 44s/张
- 分辨率/线路表更新只改 `models.json` 和 `webgen.py` 顶部 `LINES` 字典

- 密钥:`scripts/artgen.local.json`(从 `.example.json` 创建;勿外泄、勿提交)
- 模型注册表:`scripts/models.json`——**更换/新增模型只改这个文件**;生成前读一次以最新为准
- 出图尺寸:网站默认 1K;API 通道脚本自动处理(API 出 2K → 本地降采样)

## 动画精灵表工作流(已验证)

1. 用 `--ref <角色设定图>` 生成精灵表,提示词要点:`N个侧面行走动作帧、角色面向右侧、帧间距均匀`
   - **排布规范(2026-08-26 用户定)**:多帧精灵表一律要求**两行排布**(如 6帧=3列×2行,8帧=4列×2行,按从左到右、从上到下顺序),不要单行长条
   - **背景规范(2026-09-03 用户改定)**:一律要求**纯绿色背景**(提示词写"纯绿色背景RGB(0,255,0),均匀无渐变无阴影,角色/物体不沾绿色")——绿底色键抠除最稳,不依赖识别模型、不会半透化。后处理走 ai-sprite-workflow 时用 `--background-mode chroma`。**废弃"透明背景"写法**(站点实测是把棋盘格画上去的 RGB 假透明)和纯白底(浅色主体被 remover 半透化)
2. 切割/抠图/对齐:**2026-09-01 起统一走 ai-sprite-workflow skill**(`process`/`process-folder`),旧的手工分割与 rembg 脚本已废弃
3. 向左移动动画 = 帧水平翻转,不要重复生成

## 资产落盘规范

一律生成到 `<ASSET_REPOSITORY_ROOT>` 的分类目录(`characters/ enemies/ bosses/ backgrounds/ terrain/ props/ effects/ ui/ _style_ref/`),使用时才导入 `<GAME_PROJECT_ROOT>/assets/`。命名:小写英文+连字符。

## 验收闭环(必须执行)

1. 生成后立即用 ReadMediaFile **亲眼检查**;**下载后要确认拿到的是新图**（历史上踩过坑：误抓历史记录旧图)
2. 检查项:符合描述、风格与 `_style_ref/` 锚点一致、无明显崩坏(手/肢体/乱码)
3. 不合格 → 改提示词重新生成(网站通道重试成本低;premium 级别的 backup_pro 重试前先想清楚)
4. 合格 → 标记需求单完成

> **超时 ≠ 没生成(2026-09-01 教训)**:脚本报"生成超时"时,先到站点历史记录确认该提示词是否真的没出图——假超时(页面改版致检测失效)下盲目重试会重复扣积分(当天同一提示词被生成 4 次,浪费 25 积分)。webgen 已有超时兜底自动抢救,但若兜底也失败,先人工查历史记录捞图,再决定是否重试。
>
> **报"站点生成失败"也可能是误报(2026-09-02 教训)**:旧版检测用 800 字符大窗口找"失败/耗时",会混入下方旧记录的状态字样,把正在"生成中"的本卡误判为失败(头像因此假失败 4 次,白扣积分)。已修复为按本卡区间(完整提示词起 +250 字符)判定;若再遇失败报错,先去站点历史记录核实该卡真实状态再决定重试。

## 提示词要点

- 中文描述;写清:主体、风格(二次元/Q版/赛璐璐)、配色、构图、背景(纯白)、用途
- 角色一致性:先出"标准设定图"存 `_style_ref/`,后续该角色一切衍生都 `--ref` 它
- 横版游戏角色:指定"侧面站姿、面向右边"

## 帧图 → 游戏资产(2026-09-01 起统一走 ai-sprite-workflow)

生成只是第一步,后处理(抠图/切割/对齐/导出)**统一走 ai-sprite-workflow skill,禁止再用本目录的旧脚本**(sprite_pipeline.py、泛洪去白底、手工 rembg 均已废弃,仅留档):

```bash
cd <SKILLS_ROOT>/ai-sprite-workflow
python scripts/sprite_workflow_client.py process <图> --expected-frames N --import-external
# 整套动画文件夹:process-folder <目录> --expected-frames N [--skip-existing]
```

用法与实测结论见 `<SKILLS_ROOT>/ai-sprite-workflow/SKILL.md`。

- 输出帧复制进工程 `<GAME_PROJECT_ROOT>/assets/sprites/<角色>/<动作>/0~N.png`
- Godot 侧用 AnimatedSprite2D + 运行时从目录加载帧(参考 `scripts/demo_walk.gd`),向左走用 `flip_h`
- 验收样例:`scenes/demo_walk.tscn`(左右往返行走 demo),运行:
  `"<GODOT_EXECUTABLE>" --path "<GAME_PROJECT_ROOT>" scenes/demo_walk.tscn`

## 帧率提升:RIFE 本地补帧(已验证)

网站只需出**稀疏关键帧**(6-8 帧),流畅度靠本地 RIFE 补帧,零积分成本:

```bash
# 1. 帧图统一尺寸(白底、底部对齐、同宽高,RIFE 要求)
#    参考脚本逻辑见 characters/walk8_uniform 的生成方式
# 2. RIFE 补帧(默认翻倍;自定义帧数需 -m rife 目录下的 v4 模型)
"<RIFE_ROOT>/rife-ncnn-vulkan.exe" \
  -i <统一尺寸帧目录> -o <输出目录> -f "%02d.png"
# 3. 再过 sprite_pipeline.py(去底/对齐)
```

- 行走动画提示词要写**行走循环四相关键帧**(脚跟着地/重心下压/支撑后摆/交错经过),8 帧效果好于 6 帧
- 补帧后引擎内按帧数等比调 fps(16 帧 → 16fps)
- RIFE 安装目录中的 `rife-anime` 模型(二次元专项)可按需测试
- 已验证资产:鲸鱼女仆行走 8 关键帧 → RIFE 16 帧 → demo 实跑

## 多 agent 并行注意

- web 通道全局限一(共享 Edge profile),冲突时排队或改用 backup/ui 通道
- 不要覆盖 `_style_ref/` 已确认的锚点图;新资产命名带资产名前缀

## 动画资产路由(2026-08-23 用户拍板)

| 资产类型 | 走哪套 |
|---|---|
| 主角团 / Boss 等重要角色的动作动画 | **char-anim skill**(方案 C:H3 图生视频→抽帧,质量优先) |
| 小怪、NPC、杂兵等非重要动画 | **本 skill 的精灵表工作流**(一张精灵表出全帧 + 切割 + 可选 RIFE 补帧,2.5 积分成本低) |
| 静态美术(场景/道具/UI/立绘) | 本 skill 常规模型分级路由 |

拿不准资产算不算"重要"时问用户,不要自己降级。
