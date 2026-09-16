# -*- coding: utf-8 -*-
"""ComfyUI API 客户端:MiniMax H3 图生视频,供 char-anim 管线调用。

用法:
  python comfy_client.py status                          # 服务/队列状态
  python comfy_client.py upload <图片>                    # 上传参考图到 ComfyUI input
  python comfy_client.py gen --prompt-file p.txt --ref ref.png --seconds 3 --out out.mp4

依赖:仅标准库,任意 python3 可跑。ComfyUI 须在 127.0.0.1:8188 常驻。
模板:templates/h3_i2v.json(从用户已验证的工作流抓取,勿改结构,只改参数)。
"""
import argparse, json, os, random, sys, time, urllib.request, urllib.parse, uuid
from pathlib import Path

BASE = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
TEMPLATE = Path(__file__).parent.parent / "templates" / "h3_i2v.json"

# 模板内可参数化节点(2026-09-01 从运行中队列抓取确认)
N_LOAD_IMAGE = "114"        # LoadImage 参考图
N_PROMPT = "105:104"        # MiniMaxH3ImageToVideo.prompt
N_SECONDS = "105:111"       # PrimitiveFloat 时长(秒),帧数由 105:107 公式换算
N_SEED = "105:15"           # RandomNoise.noise_seed
N_TURBO = "105:126"         # PrimitiveBoolean: false=20步(默认,质量), true=8步turbo(草稿)
N_SAVE = "92"               # SaveVideo.filename_prefix
N_RES = "115"               # ResolutionSelector(宽高比/总像素)


def _req(method: str, path: str, data=None, files=None, timeout=30):
    url = BASE + path
    if files:  # multipart
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in (data or {}).items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, (fname, blob) in files.items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + blob + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    elif data is not None:
        req = urllib.request.Request(url, data=json.dumps(data).encode(), method=method)
        req.add_header("Content-Type", "application/json")
    else:
        req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw[:1] in (b"{", b"[") else raw


def check_server() -> None:
    try:
        _req("GET", "/system_stats", timeout=5)
    except Exception:
        comfy_root = os.environ.get("COMFYUI_ROOT")
        launch_hint = (
            str(Path(comfy_root) / "run_nvidia_gpu.bat")
            if comfy_root else "请设置 COMFYUI_ROOT 后运行其中的 run_nvidia_gpu.bat"
        )
        sys.exit(
            "[comfy] 连不上 ComfyUI(127.0.0.1:8188)。请先启动: " + launch_hint
        )


def upload_image(path: Path) -> str:
    """上传参考图到 ComfyUI input 目录,返回 LoadImage 可用的文件名。"""
    resp = _req("POST", "/upload/image",
                data={"type": "input", "overwrite": "true"},
                files={"image": (path.name, path.read_bytes())})
    name = resp["name"] if not resp.get("subfolder") else f"{resp['subfolder']}/{resp['name']}"
    print(f"[comfy] 参考图已上传: {name}")
    return name


def build_workflow(prompt: str, ref_name: str | None, seconds: float,
                   seed: int, turbo: bool, prefix: str,
                   ratio: str | None, megapixels: float | None) -> dict:
    wf = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    wf[N_PROMPT]["inputs"]["prompt"] = prompt
    wf[N_SECONDS]["inputs"]["value"] = seconds
    wf[N_SEED]["inputs"]["noise_seed"] = seed if seed >= 0 else random.getrandbits(63)
    wf[N_TURBO]["inputs"]["value"] = turbo
    wf[N_SAVE]["inputs"]["filename_prefix"] = prefix
    if ref_name:
        wf[N_LOAD_IMAGE]["inputs"]["image"] = ref_name
    if ratio:
        wf[N_RES]["inputs"]["aspect_ratio"] = ratio
    if megapixels:
        wf[N_RES]["inputs"]["megapixels"] = megapixels
    return wf


def submit_and_wait(wf: dict, timeout_s: int) -> tuple[str, str, str]:
    """提交并轮询,返回 (filename, subfolder, prompt_id)。失败抛错。"""
    resp = _req("POST", "/prompt", data={"prompt": wf})
    pid = resp["prompt_id"]
    print(f"[comfy] 已入队: {pid}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(5)
        hist = _req("GET", f"/history/{pid}", timeout=15)
        if pid not in hist:
            continue
        rec = hist[pid]
        st = rec.get("status", {})
        if st.get("status_str") == "error":
            msgs = json.dumps(st.get("messages", []), ensure_ascii=False)[:400]
            raise RuntimeError(f"[comfy] 任务失败: {msgs}")
        if st.get("completed"):
            img = rec["outputs"][N_SAVE]["images"][0]
            print(f"[comfy] 完成: {img['filename']}")
            return img["filename"], img.get("subfolder", ""), pid
    raise RuntimeError(f"[comfy] 超时({timeout_s}s)未完成,任务可能仍在跑,prompt_id={pid},"
                       "可用 /history 查询后手动下载")


def download(filename: str, subfolder: str, out: Path) -> None:
    qs = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": "output"})
    raw = _req("GET", f"/view?{qs}", timeout=300)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    print(f"[comfy] 已保存: {out} ({out.stat().st_size} bytes)")


def main() -> None:
    ap = argparse.ArgumentParser(description="ComfyUI MiniMax H3 图生视频客户端")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    up = sub.add_parser("upload"); up.add_argument("image")
    g = sub.add_parser("gen")
    g.add_argument("--prompt", default=None)
    g.add_argument("--prompt-file", default=None, help="提示词文件(推荐,避免引号/换行问题)")
    g.add_argument("--ref", default=None, help="参考图本地路径(自动上传)")
    g.add_argument("--seconds", type=float, default=3.0)
    g.add_argument("--seed", type=int, default=-1, help="-1=随机")
    g.add_argument("--turbo", action="store_true", help="8步 turbo(草稿);默认 20 步(质量)")
    g.add_argument("--prefix", default="video/charanim")
    g.add_argument("--ratio", default=None, help='如 "9:16 (Portrait)"(跳跃类用竖屏)')
    g.add_argument("--megapixels", type=float, default=None)
    g.add_argument("--out", required=True)
    g.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    check_server()
    if args.cmd == "status":
        q = _req("GET", "/queue")
        print(f"[comfy] 在线。队列: 运行中 {len(q['queue_running'])} / 等待 {len(q['queue_pending'])}")
    elif args.cmd == "upload":
        upload_image(Path(args.image))
    elif args.cmd == "gen":
        prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else args.prompt
        if not prompt:
            ap.error("gen 需要 --prompt 或 --prompt-file")
        ref_name = upload_image(Path(args.ref)) if args.ref else None
        wf = build_workflow(prompt, ref_name, args.seconds, args.seed,
                            args.turbo, args.prefix, args.ratio, args.megapixels)
        fname, subfolder, _ = submit_and_wait(wf, args.timeout)
        download(fname, subfolder, Path(args.out))


if __name__ == "__main__":
    main()
