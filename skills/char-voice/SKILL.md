---
name: char-voice
description: DP-Game 角色台词配音生产(复用已建档音色,批量生成台词语音)。当任务涉及给已有角色配音、生成台词语音时使用。新角色音色设计见附录,日常配音勿走设计流程。
---

# DP-Game 角色台词配音(音色复用)

> 环境(2026-09 已装妥,勿重装):
> - ComfyUI 便携版:`<COMFYUI_ROOT>`,节点 ComfyUI-Qwen3-TTS,权重在 `ComfyUI/models/Qwen3-TTS/`
> - 音色嵌入库:`ComfyUI\models\Qwen3-TTS\prompts\<角色>.safetensors`
> - **必须先启动 ComfyUI 服务**(run_nvidia_gpu.bat),API 在 `http://127.0.0.1:8188`
> - 8GB 显存环境参考:单句通常在十秒量级,实际速度取决于硬件和模型配置

## 主流程:给角色配台词(3 步)

**前置:确认角色音色已建档**——查 `ComfyUI\models\Qwen3-TTS\prompts\` 里有没有 `<角色>.safetensors`。已建档音色清单见文末。没有才走附录的建档流程。

**① 准备台词文件**:lines.txt,每行一句(UTF-8)

**② 批量生成**(Windows python 3.12):

```bash
python scripts/qwen3_voice.py clone --char <角色> --lines <lines.txt>
```

产出在 `ComfyUI\output\audio_test\<角色>_line<N>_00001.flac`

**③ 归档入库**:复制到资产库 `<ASSET_REPOSITORY_ROOT>/characters/<角色>/voice/lines/`,命名与台词/场景对应;让用户抽听验收

可选参数:`--language`(默认 Chinese)、`--seed`(默认 100,同 seed 结果可复现)

## 关键坑

1. 服务没启动 → 先启动 ComfyUI,再跑脚本
2. 换过音色嵌入或节点文件后**必须重启 ComfyUI**
3. 语音/音乐/音效/视频共用 8G 显存,同一时间只跑一个生成任务
4. 台词里生僻字、谐音字先念一遍验证;数字、英文必要时改写成中文读法

## 已建档音色

| 角色 | 音色 | 定稿日期 |
|---|---|---|
| whale-maid | 软萌微懒、轻快俏皮(v6) | 2026-09-03 |

## 附录:新角色音色建档(仅首次)

1. variants.txt 每行 `tag<TAB>音色描述`(音高/性格质感/语速/尾音/情绪),同一台词同 seed 跑多版:
   `qwen3_voice.py design --variants <v.txt> --text "<试听台词>"`
2. 用户亲耳选定后,选中音频复制为 `ComfyUI\input\<角色>_voice_ref.flac`,然后:
   `qwen3_voice.py make-prompt --char <角色> --ref-audio <角色>_voice_ref.flac --ref-text "<参考音频逐字文本>"`
   (ref-text 错字会拉低克隆相似度)
3. 归档三件到 `godot-resource\characters\<角色>\voice\`:`<角色>.safetensors`、`<角色>_voice_ref.flac`、`ref_text.txt`,并在上文"已建档音色"表登记
4. 音色要更换时:重新走 1-3,三件一起替换,不留旧版
