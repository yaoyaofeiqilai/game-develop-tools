---
name: sfx-gen
description: DP-Game 音效生产(Sony Woosh 本地部署于 ComfyUI,文生音效 DFlow)。当任务涉及生成游戏音效(打击/脚步/环境声/UI 音等)时使用。模型已装好,直接按本手册操作,勿重装。
---

# DP-Game 音效生产(Sony Woosh)

> 环境(2026-09 已装妥,勿重装):
> - ComfyUI 便携版:`<COMFYUI_ROOT>`,节点 `ComfyUI-Woosh`
> - 权重:`ComfyUI\models\woosh\` 下 5 个文件夹(Woosh-AE、TextConditionerA/V、Woosh-DFlow、Woosh-DVFlow-8s),每个含 config.yaml + weights.safetensors
> - roberta-large 分词器缓存:`models\woosh\hf_cache\`(删了要重新下载并重启服务)
> - **必须先启动 ComfyUI 服务**,API 在 `http://127.0.0.1:8188`
> - 8GB 显存环境参考(蒸馏版 4 步):一条短音效通常在几十秒量级

## 主流程:文生音效(DFlow)

```bash
python scripts/woosh_gen.py --prompt "<音效描述>" [--seconds 5] [--seed 42] [--prefix audio_test/woosh_xxx]
```

- 用 **Windows python 3.12**;产出 flac + 频谱图 png,在 `ComfyUI\output\` 下
- 描述用英文更稳,写清 主体+动作+材质+质感,如
  `A sword slash swoosh followed by a metallic clang hitting armor, game sound effect, punchy and clean`
- `--seconds` 按时长换算(约 100 latent 帧=1 秒,脚本已处理)
- 默认蒸馏 4 步出片;要更高质量可 `--steps 50`(慢很多,一般不需要)

## 备用:视频自动配音效(DVFlow,基本不用)

用户确认日常只做文生音效,此路径仅备用(已实测跑通):
`woosh_gen.py --video "<视频.mp4>" [--prompt "<补充描述,可省略>"]`
注意:DVFlow 不接 WooshTextEncode(权重无文本条件槽),描述写在 prompt 参数里;in-process 推理;需 synchformer 权重(已预置 `ComfyUI\models\mmaudio\ext_weights\`)。

## 验收与归档

- 音效必须让用户亲耳验收(频谱图只能看大致能量分布,不能代替耳朵)
- 归档:`<ASSET_REPOSITORY_ROOT>/audio/sfx/<类别>/<名称>.flac`,并按项目约定同步到游戏工程
- 常用类别:attack(攻击)/move(脚步移动)/hit(受击)/ui/ambient(环境)

## 关键坑

1. **改过 `models\woosh\hf_cache` 或节点文件后必须重启 ComfyUI**(缓存目录启动时绑定,踩过)
2. 文生音效选 `Woosh-DFlow`(model_name/model_type 配套,脚本已内置)
3. 音乐/语音/音效/视频共用 8G 显存,**同一时间只跑一个生成任务**
4. 节点 loader 对 config.yaml 里的路径有临时改写机制,**不要手动改 woosh 模型文件夹里的 config.yaml**
5. **WooshSample 的 force_offload 必须保持 False**(脚本已内置):节点有 bug,force_offload 卸载模型后 loader 仍缓存旧 patcher(`.model=None`),下一次运行 sampler 在 `load_model_gpu` 重载之前就抓取了 None,报 `'NoneType' object has no attribute 'get_cond'`;subprocess 模式则报 `Cannot determine model directory`。万一遇到,重启 ComfyUI 即可
