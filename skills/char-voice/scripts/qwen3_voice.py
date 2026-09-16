import json, time, urllib.request, sys, argparse, os
from pathlib import Path

BASE = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")


def model_dir(name):
    comfyui_root = os.environ.get("COMFYUI_ROOT")
    if not comfyui_root:
        sys.exit("请先设置 COMFYUI_ROOT，指向 ComfyUI 便携版根目录")
    return str(Path(comfyui_root) / "ComfyUI" / "models" / "Qwen3-TTS" / name)


def post(payload):
    req = urllib.request.Request(BASE + "/prompt", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))["prompt_id"]


def wait(name, pid, timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(5)
        h = json.load(urllib.request.urlopen(f"{BASE}/history/{pid}", timeout=30))
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("completed"):
                outs = {}
                for nid, o in h[pid].get("outputs", {}).items():
                    if "audio" in o:
                        outs[nid] = [f.get("filename") for f in o["audio"]]
                print(f"[{name}] DONE in {int(time.time()-t0)}s {outs}", flush=True)
                return True
            if st.get("status_str") == "error":
                for m in st.get("messages", []):
                    if m[0] == "execution_error":
                        print(f"[{name}] ERROR {m[1].get('node_type')}: {m[1].get('exception_message')[:400]}", flush=True)
                return False
    print(f"[{name}] TIMEOUT", flush=True)
    return False


def loader(model_dir, repo_id):
    return {"class_type": "Qwen3Loader", "inputs": {"repo_id": repo_id, "source": "ModelScope", "precision": "bf16", "attention": "auto", "local_model_path": model_dir}}


def cmd_design(args):
    """一次生成多版音色试听。--variants 文件每行: tag<TAB>instruct"""
    pairs = []
    with open(args.variants, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            tag, instruct = line.split("\t", 1)
            pairs.append((tag.strip(), instruct.strip()))
    wf = {"1": loader(model_dir("Qwen3-TTS-12Hz-1.7B-VoiceDesign"), "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign")}
    for i, (tag, instruct) in enumerate(pairs):
        n = str(10 + i)
        wf[n] = {"class_type": "Qwen3VoiceDesign", "inputs": {"model": ["1", 0], "text": args.text, "instruct": instruct, "language": args.language, "seed": args.seed}}
        wf[str(20 + i)] = {"class_type": "SaveAudio", "inputs": {"audio": [n, 0], "filename_prefix": f"audio_test/voice_{tag}"}}
    ok = wait("design", post({"prompt": wf}))
    sys.exit(0 if ok else 1)


def cmd_make_prompt(args):
    """从参考音频提取音色嵌入,保存为 prompts/<角色>.safetensors"""
    mk = {
        "1": loader(model_dir("Qwen3-TTS-12Hz-1.7B-Base"), "Qwen/Qwen3-TTS-12Hz-1.7B-Base"),
        "2": {"class_type": "LoadAudio", "inputs": {"audio": args.ref_audio}},
        "3": {"class_type": "Qwen3PromptMaker", "inputs": {"model": ["1", 0], "ref_audio": ["2", 0], "ref_text": args.ref_text}},
        "4": {"class_type": "Qwen3SavePrompt", "inputs": {"prompt": ["3", 0], "filename": args.char}},
    }
    ok = wait("make_prompt", post({"prompt": mk}))
    sys.exit(0 if ok else 1)


def cmd_clone(args):
    """加载音色嵌入,批量克隆台词。--lines 文件每行一句"""
    with open(args.lines, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    clone = {
        "1": loader(model_dir("Qwen3-TTS-12Hz-1.7B-Base"), "Qwen/Qwen3-TTS-12Hz-1.7B-Base"),
        "2": {"class_type": "Qwen3LoadPrompt", "inputs": {"prompt_file": args.char + ".safetensors"}},
    }
    for i, text in enumerate(lines):
        n = str(10 + i)
        clone[n] = {"class_type": "Qwen3VoiceClone", "inputs": {"model": ["1", 0], "text": text, "prompt": ["2", 0], "language": args.language, "seed": args.seed + i}}
        clone[str(20 + i)] = {"class_type": "SaveAudio", "inputs": {"audio": [n, 0], "filename_prefix": f"audio_test/{args.char}_line{i+1}"}}
    ok = wait("clone", post({"prompt": clone}))
    sys.exit(0 if ok else 1)


p = argparse.ArgumentParser(description="Qwen3-TTS 角色音色生产(需 ComfyUI 服务运行中)")
sub = p.add_subparsers(dest="cmd", required=True)

d = sub.add_parser("design", help="生成多版音色试听")
d.add_argument("--variants", required=True, help="txt 文件,每行: tag<TAB>音色描述")
d.add_argument("--text", required=True, help="试听台词(各版保持一致便于对比)")
d.add_argument("--language", default="Chinese")
d.add_argument("--seed", type=int, default=42)
d.set_defaults(fn=cmd_design)

m = sub.add_parser("make-prompt", help="提取音色嵌入并保存")
m.add_argument("--char", required=True, help="角色名(即保存的文件名)")
m.add_argument("--ref-audio", required=True, help="参考音频文件名(须先放入 ComfyUI/input/)")
m.add_argument("--ref-text", required=True, help="参考音频的逐字文本")
m.set_defaults(fn=cmd_make_prompt)

c = sub.add_parser("clone", help="加载音色嵌入批量生成台词")
c.add_argument("--char", required=True, help="角色名(对应 prompts/<角色>.safetensors)")
c.add_argument("--lines", required=True, help="txt 文件,每行一句台词")
c.add_argument("--language", default="Chinese")
c.add_argument("--seed", type=int, default=100)
c.set_defaults(fn=cmd_clone)

args = p.parse_args()
args.fn(args)
