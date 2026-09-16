import json, time, urllib.request, sys, argparse, os

BASE = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")


def post(payload):
    req = urllib.request.Request(BASE + "/prompt", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))["prompt_id"]


def wait(pid, timeout=1800):
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


p = argparse.ArgumentParser(description="Sony Woosh 音效生成(需 ComfyUI 服务运行中)")
p.add_argument("--prompt", required=True, help="音效描述(英文更稳);视频模式可留空或写补充描述", nargs="?", default="")
p.add_argument("--video", default="", help="可选:视频文件路径(填了就是视频配音效 DVFlow 模式)")
p.add_argument("--seconds", type=float, default=5.0, help="音效时长,约 100 latent 帧=1 秒")
p.add_argument("--seed", type=int, default=42)
p.add_argument("--steps", type=int, default=4, help="蒸馏模型 4 步即可")
p.add_argument("--prefix", default="audio_test/woosh", help="输出文件名前缀")
args = p.parse_args()

latent_frames = max(50, round(args.seconds * 100))


def woosh_mode(kind):
    opts = json.load(urllib.request.urlopen(BASE + "/object_info/WooshTextEncode", timeout=30))["WooshTextEncode"]["input"]["required"]["mode"][0]
    return next(o for o in opts if kind in o)


if args.video:
    wf = {
      "1": {"class_type": "WooshLoadFlow", "inputs": {"model_name": "Woosh-DVFlow-8s", "model_type": "DVFlow"}},
      "2": {"class_type": "WooshLoadVideo", "inputs": {"video_path": args.video}},
      # DVFlow checkpoint 没有 text conditioner 槽,不能接 text_conditioning;
      # 文本描述直接走 WooshSample 的 prompt 参数。
      "3": {"class_type": "WooshSample", "inputs": {"gen_model": ["1", 0], "prompt": args.prompt, "steps": args.steps, "cfg": 4.5, "seed": args.seed, "latent_frames": latent_frames, "subprocess": False, "force_offload": False, "video": ["2", 0]}},
      "4": {"class_type": "SaveAudio", "inputs": {"audio": ["3", 1], "filename_prefix": args.prefix}},
    }
else:
    wf = {
      "1": {"class_type": "WooshLoadFlow", "inputs": {"model_name": "Woosh-DFlow", "model_type": "DFlow"}},
      "5": {"class_type": "WooshTextEncode", "inputs": {"mode": woosh_mode("T2A")}},
      "3": {"class_type": "WooshSample", "inputs": {"gen_model": ["1", 0], "prompt": args.prompt, "steps": args.steps, "cfg": 4.5, "seed": args.seed, "latent_frames": latent_frames, "subprocess": True, "force_offload": False, "text_conditioning": ["5", 0]}},
      "4": {"class_type": "SaveAudio", "inputs": {"audio": ["3", 1], "filename_prefix": args.prefix}},
      "6": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": args.prefix + "_spec"}},
    }
sys.exit(0 if wait(post({"prompt": wf})) else 1)
