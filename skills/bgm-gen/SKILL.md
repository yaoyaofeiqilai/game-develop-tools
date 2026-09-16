---
name: bgm-gen
description: DP-Game 音乐/BGM 生产(MiniMax Music 3 本地部署于 ComfyUI,文生音乐,支持纯音乐 BGM 和带歌词歌曲)。当任务涉及生成背景音乐、主题曲、角色歌时使用。模型已装好,直接按本手册操作,勿重装。
---

# DP-Game 音乐生产(MiniMax Music 3)

> 环境(2026-09 已装妥,勿重装):
> - ComfyUI 便携版:`<COMFYUI_ROOT>`(0.34.0+,Music 3 为原生节点)
> - 权重(int8 低显存版):`models\diffusion_models\minimax_music3_dit_int8_convrot.safetensors`、`models\text_encoders\minimax_music3_text_encoder_pruned_int8_convrot.safetensors`、`models\vae\minimax_music3_dav.safetensors`
> - **必须先启动 ComfyUI 服务**,API 在 `http://127.0.0.1:8188`
> - 8GB 显存环境参考:30 秒 BGM 通常需要数分钟;越长越慢,批量任务串行提交

## 主流程:生成 BGM / 歌曲

```bash
python scripts/music3_gen.py --caption "<曲风描述>" [--lyrics "<歌词>"] [--seconds 60] [--seed 1234] [--prefix audio_test/music3]
```

- 使用可访问本机 ComfyUI 的 Python 3.12
- 产出在 `ComfyUI\output\` 下按 prefix 命名(flac)

## 提示词要点

- `caption` 用英文更稳:写清 风格+情绪+乐器+速度(BPM)+用途,例如
  `Cheerful 2D JRPG village theme, chiptune-inspired orchestral pop, warm piano melody, light strings, playful flute, gentle percussion, cozy and adventurous mood, seamless loop feel, instrumental only`
- 纯音乐 BGM:caption 末尾加 `instrumental only`,`--lyrics` 留空
- 带歌词歌曲:`--lyrics` 传完整歌词(可用 [Verse]/[Chorus] 等结构标记)
- `--seconds` 是最长时长,模型可能提前自然结束(要 30s 实际可能出 26s,正常)
- 游戏循环 BGM 在 caption 里写 `seamless loop feel`,生成后用音频工具首尾淡入淡出接循环点

## 验收与归档

- 产出必须让用户亲耳验收;不满意先改 caption(风格词、乐器、BPM),再换 seed
- 验收通过归档:`<ASSET_REPOSITORY_ROOT>/audio/bgm/<场景>.flac`,并按项目约定同步到游戏工程

## 关键坑

1. 解码节点必须 `VAEDecodeAudio`(脚本已内置);`cfg_scale=1.5、top_k=50` 是文本编码节点必填项(脚本已内置)
2. 音乐/语音/音效/视频共用 8G 显存,**同一时间只跑一个生成任务**
3. 显存受限时优先使用 int8 权重;选择 fp16/fp32 前先确认本机显存容量
4. 显存不足(CUDA OOM)时先缩短 `--seconds` 再试
