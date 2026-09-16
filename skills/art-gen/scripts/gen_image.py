#!/usr/bin/env python3
"""DP-Game 美术生成脚本 v0.3 —— 多模型分级路由。

分级(定义在 models.json,换模型只改配置不改代码):
    draft   (默认)= plan 通道 seedream-5.0-lite,套餐额度 ≈ 免费
                    用于:小物件、普通小怪、道具、实验图
    premium       = standard 通道 seedream-5.0-pro,约 ¥1.8/张
                    仅用于:主角、Boss、重要场景、需参考图一致性的角色衍生
    ui            = jimeng 通道 即梦4.0,免费试用额度/¥0.2 每张,限 1 并发
                    用于:UI 图标、按钮、装饰元素

用法:
    python gen_image.py --prompt "..." --out <路径>                    # draft
    python gen_image.py --tier premium --ref ref.png --prompt "..." --out <路径>
    python gen_image.py --tier ui --prompt "..." --out <路径>
    # 调试可用 --channel/--model 强制覆盖 tier

注意:
- 最小尺寸约 3686400 像素(如 2048x2048)
- 返回图片 URL 有效期 24h,本脚本立即下载到本地
- 密钥读取脚本同目录的 artgen.local.json(从 .example.json 创建,勿外泄)
"""
import argparse
import base64
import json
import mimetypes
import sys
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "artgen.local.json"

CHANNELS = {
    "plan": {
        "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "key_field": "ark_plan_api_key",
        "default_model": "doubao-seedream-5-0-lite",
    },
    "standard": {
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "key_field": "ark_api_key",
        "default_model": "doubao-seedream-5-0-pro-260628",
    },
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"缺少密钥文件: {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def generate(prompt: str, size: str, model: str, base_url: str, api_key: str,
             refs: list[Path] | None = None) -> str:
    """调用 images/generations,返回图片 URL。"""
    payload: dict = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": "url",
    }
    if refs:
        images = [to_data_url(p) for p in refs]
        payload["image"] = images[0] if len(images) == 1 else images
    req = urllib.request.Request(
        f"{base_url}/images/generations",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"API 错误 {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
    return data["data"][0]["url"]


def parse_size(size: str) -> tuple[int, int]:
    w, h = size.lower().split("x")
    return int(w), int(h)


def download(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as resp, open(out_path, "wb") as f:
        f.write(resp.read())


def downscale_to(path: Path, width: int, height: int) -> None:
    """本地降采样到目标尺寸(ark 系 API 最小出图 2048x2048,小图靠本地缩放)。"""
    from PIL import Image
    with Image.open(path) as img:
        if img.size == (width, height):
            return
        img.resize((width, height), Image.LANCZOS).save(path)


MODELS_PATH = Path(__file__).parent / "models.json"


def load_tiers() -> dict:
    """加载模型注册表(逻辑分级 → 通道/模型)。"""
    if not MODELS_PATH.exists():
        return {}
    return json.loads(MODELS_PATH.read_text(encoding="utf-8")).get("tiers", {})


def main() -> None:
    ap = argparse.ArgumentParser(description="DP-Game 美术生成(多模型分级路由)")
    ap.add_argument("--prompt", default=None, help="单图模式的提示词(--batch 模式不需要)")
    ap.add_argument("--out", default=None, help="输出文件路径(--batch 模式不需要)")
    ap.add_argument("--ref", default=None, help="参考图路径,多张用逗号分隔(jimeng 通道不支持)")
    ap.add_argument("--size", default="1024x1024",
                    help="最终图像尺寸,默认 1K。ark 通道 API 最小出图 2048x2048,小尺寸会自动本地降采样")
    ap.add_argument("--tier", default="draft",
                    help="资产分级(见 models.json):draft=普通资产(默认) | premium=重要资产 | ui=UI图标")
    ap.add_argument("--channel", choices=["web", "plan", "standard", "jimeng"], default=None,
                    help="覆盖 tier 的通道(调试用)")
    ap.add_argument("--model", default=None, help="覆盖 tier 的模型(调试用)")
    ap.add_argument("--line", default=None, help="web 通道线路(覆盖 tier),如 Image2稳定线路八")
    ap.add_argument("--res", default=None, help="web 通道分辨率: 1K / 2K / 4K(覆盖 tier)")
    ap.add_argument("--ratio", default=None, help="web 通道宽高比: 1:1 / 16:9 等(覆盖 tier;精灵表建议 16:9)")
    ap.add_argument("--batch", default=None,
                    help="批量任务 JSON(web 通道):[{prompt, out, tier?, ref?, line?, res?, ratio?}, ...];"
                         "一次浏览器会话跑完,比逐张调用快得多")
    args = ap.parse_args()

    out = Path(args.out) if args.out else None
    req_w = req_h = None
    if args.size:
        req_w, req_h = parse_size(args.size)

    # 路由解析:tier 注册表 → 显式 --channel/--model 覆盖
    tiers = load_tiers()

    # ---- 批量模式(web 通道) ----
    if args.batch:
        import webgen
        raw = json.loads(Path(args.batch).read_text(encoding="utf-8"))
        jobs = []
        for j in raw:
            t = tiers.get(j.get("tier", "draft"), {})
            if t.get("channel", "web") != "web":
                sys.exit(f"批量模式仅支持 web 通道,任务 {j.get('out')} 的 tier 不是 web")
            refs = None
            if j.get("ref"):
                refs = [Path(s.strip()) for s in j["ref"].split(",")]
                for p in refs:
                    if not p.exists():
                        sys.exit(f"参考图不存在: {p}")
            jobs.append({
                "prompt": j["prompt"], "out": j["out"], "refs": refs,
                "line": j.get("line") or t.get("line"),
                "res": j.get("res") or t.get("res"),
                "ratio": j.get("ratio") or t.get("ratio"),
            })
        print(f"[artgen] 批量 {len(jobs)} 张(web 通道,需Edge已退出)")
        webgen.generate_batch(jobs)
        return

    if not out:
        sys.exit("需要 --out(单图)或 --batch(批量)")
    tier = tiers.get(args.tier)
    if args.tier != "draft" and not tier:
        sys.exit(f"未知 tier: {args.tier}(可选: {', '.join(tiers) or '见 models.json'})")
    channel = args.channel or (tier or tiers.get("draft", {})).get("channel", "web")
    model = args.model or (tier or {}).get("model")

    if channel == "web":
        import webgen
        refs = None
        if args.ref:
            refs = [Path(p.strip()) for p in args.ref.split(",")]
            for p in refs:
                if not p.exists():
                    sys.exit(f"参考图不存在: {p}")
        line = args.line or (tier or {}).get("line") or webgen.DEFAULT_LINE
        res = args.res or (tier or {}).get("res")
        ratio = args.ratio or (tier or {}).get("ratio")
        print(f"[artgen] tier={args.tier} channel=web line={line} res={res or '自动'} ratio={ratio or '自动'}(需Edge已退出)")
        webgen.generate(args.prompt, out, refs, line=line, res=res, ratio=ratio)
        print(f"[artgen] 已保存: {out} ({out.stat().st_size} bytes)")
        return

    if channel == "jimeng":
        import jimeng
        cfg = load_config()
        ak, sk = cfg.get("jimeng_access_key"), cfg.get("jimeng_secret_key")
        if not ak or not sk:
            sys.exit("artgen.local.json 中 jimeng_access_key / jimeng_secret_key 为空")
        if args.ref:
            sys.exit("jimeng 通道暂不支持本地参考图(仅接受公网 URL)")
        area = max(req_w * req_h, 1024 * 1024)  # 即梦面积下限 1024*1024
        print(f"[artgen] tier={args.tier} channel=jimeng model={model or 'jimeng_t2i_v40'} area={area}")
        urls = jimeng.generate(args.prompt, ak, sk, size=area)
        download(urls[0], out)
        downscale_to(out, req_w, req_h)
        print(f"[artgen] 已保存: {out} ({out.stat().st_size} bytes)")
        return

    ch = CHANNELS[channel]
    cfg = load_config()
    key = cfg.get(ch["key_field"])
    if not key:
        sys.exit(f"artgen.local.json 中 {ch['key_field']} 为空")
    model = model or ch["default_model"]

    refs = None
    if args.ref:
        refs = [Path(p.strip()) for p in args.ref.split(",")]
        for p in refs:
            if not p.exists():
                sys.exit(f"参考图不存在: {p}")

    # ark 系 API 最小出图约 2048x2048;请求尺寸更小则 API 出 2K、本地降采样
    api_size = args.size if req_w * req_h >= 3686400 else "2048x2048"
    cost = (tier or {}).get("cost_cny")
    cost_msg = f" 约¥{cost}/张" if cost else " 套餐额度"
    print(f"[artgen] tier={args.tier} channel={channel} model={model} api_size={api_size} -> {args.size} refs={len(refs or [])}{cost_msg}")
    url = generate(args.prompt, api_size, model, ch["base_url"], key, refs)
    download(url, out)
    downscale_to(out, req_w, req_h)
    print(f"[artgen] 已保存: {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
