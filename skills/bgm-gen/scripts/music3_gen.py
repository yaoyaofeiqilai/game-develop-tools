import json, time, urllib.request, sys, argparse, os

BASE = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")


def post(payload):
    req = urllib.request.Request(BASE + "/prompt", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))["prompt_id"]


def wait(pid, timeout=3600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(5)
        h = json.load(urllib.request.urlopen(f"{BASE}/history/{pid}", timeout=30))
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("completed"):
                for nid, o in h[pid].get("outputs", {}).items():
                    if "audio" in o:
                        for f in o["audio"]:
                            print("OUTPUT", f.get("filename"), flush=True)
                print(f"DONE in {int(time.time()-t0)}s", flush=True)
                return True
            if st.get("status_str") == "error":
                for m in st.get("messages", []):
                    if m[0] == "execution_error":
                        print(f"ERROR {m[1].get('node_type')}: {m[1].get('exception_message')[:400]}", flush=True)
                return False
    print("TIMEOUT", flush=True)
    return False


p = argparse.ArgumentParser(description="MiniMax Music 3 音乐生成(需 ComfyUI 服务运行中)")
p.add_argument("--caption", required=True, help="曲风/内容描述(英文更稳)")
p.add_argument("--lyrics", default="", help="歌词,纯音乐留空")
p.add_argument("--seconds", type=float, default=60.0, help="最长时长秒(模型可能提前结束)")
p.add_argument("--seed", type=int, default=1234)
p.add_argument("--steps", type=int, default=40)
p.add_argument("--prefix", default="audio_test/music3", help="输出文件名前缀")
args = p.parse_args()

wf = {
  "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "minimax_music3_dit_int8_convrot.safetensors", "weight_dtype": "default"}},
  "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "minimax_music3_text_encoder_pruned_int8_convrot.safetensors", "type": "minimax"}},
  "3": {"class_type": "MiniMaxMusic3TextEncode", "inputs": {"clip": ["2", 0], "caption": args.caption, "lyrics": args.lyrics, "seed": args.seed, "max_duration": args.seconds, "cfg_scale": 1.5, "top_k": 50}},
  "4": {"class_type": "EmptyMiniMaxMusic3LatentAudio", "inputs": {"seconds": ["3", 1], "batch_size": 1}},
  "5": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "seed": args.seed, "steps": args.steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "positive": ["3", 0], "negative": ["3", 0], "latent_image": ["4", 0], "denoise": 1.0}},
  "6": {"class_type": "VAELoader", "inputs": {"vae_name": "minimax_music3_dav.safetensors"}},
  "7": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["5", 0], "vae": ["6", 0]}},
  "8": {"class_type": "SaveAudio", "inputs": {"audio": ["7", 0], "filename_prefix": args.prefix}},
}
sys.exit(0 if wait(post({"prompt": wf})) else 1)
