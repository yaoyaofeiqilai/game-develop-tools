#!/usr/bin/env python3
"""绿叶 AI(lyaiapp.com)网页自动化生图通道。

通过 Playwright 以用户 Edge 登录态(persistent profile)驱动网页生图:
上传参考图(可选)→ 填提示词 → 点生成 → 轮询新结果 → 下载落盘。

单图用法(main)与批量用法(generate_batch)均可。批量模式共享一次浏览器
会话,并缓存线路/分辨率/宽高比选择,多张图时速度提升显著。

限制与约定:
- 运行时 Edge 必须完全退出(profile 锁);脚本检测到会给出明确报错
- 页面改版只需改下面 SITE / LINES 配置
- 结果为 CDN URL,立即下载;作品在服务器仅保留 1-3 天
- 站点先出半分辨率预览再换全分辨率图,下载前等 naturalWidth 稳定(勿简化)
"""
import os
import sys
import time
import urllib.request
from pathlib import Path

# ---- 站点配置(页面改版改这里) ----
SITE = {
    "url": "https://lyaiapp.com/workspace",
    "file_input": "input[type=file]",
    "prompt_box": "textarea",
    "generate_btn_text": "生成图片",
    "cdn_marker": "rains3.com/images/",   # 结果图 URL 特征
    "timeout_s": 300,
}

# 线路表:线路名 → 所属分组(None = 默认分组,无需点开)
# 实测(2026-08-22):稳定线路八标称 124k 但 2K/4K 按钮实为禁用;
# 高分辨率用稳定线路十七(2K/4K 均可用,10 积分;4K 16 积分)。
# 2026-09-13 站点线路全面改名,实测新表:
#   低价线路 = 2.5Flare渠道1(2.5 积分,自动/1K,支持 1:1/4:3/16:9);
#   高分辨率 = GPT2.5·4K 分组的 Image4K-高-特价(10 积分,2K/4K);
#   分组按钮名:GPT2.5·1K / GPT2.5·4K / GPT2.5 / GPT2 / Gemini / Midjourney / Seedream / Qwen。
LINES = {
    "2.5Flare渠道1": None,               # 2.5 积分,自动/1K(原 Image2低价一)
    "Image4K-高-特价": "GPT2.5·4K",      # 10 积分,2K/4K(原 稳定线路十七)
    "2.5Sunburst-超分": "GPT2.5·4K",     # 8 积分,高质(备用)
}
DEFAULT_LINE = "2.5Flare渠道1"

EDGE_PROFILE = os.environ.get(
    "ARTGEN_EDGE_PROFILE",
    str(Path.home() / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data"),
)

# 可选网络代理。不要在可共享脚本里固化本机端口或凭据。
PROXY = os.environ.get("ARTGEN_PROXY_URL", "")


def _scroll_line_list(page) -> None:
    """把页面里能滚动的容器(线路列表)滚到底再回顶,触发虚拟列表渲染未挂载的行。"""
    page.evaluate("""() => {
      const els=[...document.querySelectorAll('*')].filter(e=>e.scrollHeight>e.clientHeight+20);
      els.sort((a,b)=>(b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight));
      if(els.length){ const el=els[0]; el.scrollTop=el.scrollHeight; el.dispatchEvent(new Event('scroll')); }
    }""")
    page.wait_for_timeout(700)
    page.evaluate("""() => {
      const els=[...document.querySelectorAll('*')].filter(e=>e.scrollHeight>e.clientHeight+20);
      els.sort((a,b)=>(b.scrollHeight-b.clientHeight)-(a.scrollHeight-a.clientHeight));
      if(els.length){ const el=els[0]; el.scrollTop=0; el.dispatchEvent(new Event('scroll')); }
    }""")
    page.wait_for_timeout(700)


def _select_line(page, line: str, res: str | None, ratio: str | None = None) -> None:
    """打开模型选择器,选择线路(必要时先点分组),再选分辨率与宽高比。

    实测要点(2026-08-22,2026-08-24 更新):
    - 线路必须按 button 过滤点击(页面历史记录里有同名文本,会误点)
    - 站点改版后:弹层标题由"全部模型"改为"模型选择";分组要用 button 过滤点击
      (get_by_text exact 会命中分组内 span 且把弹层关掉)
    - 线路列表是虚拟滚动:目标线路可能要滚动后才渲染,故先 scroll 再点
    - 分辨率按钮在可用时用常规 click;禁用(灰色)时说明线路不支持该分辨率
    - 宽高比在"自动"下拉里(1:1/2:3/3:2/3:4/4:3/9:16/16:9),精灵表建议 16:9
    """
    import re

    # 当前线路按钮:文本含线路名前缀的按钮
    # (2026-09-13 站点改名后按钮形如 "2.5Flare渠道1 热门 2.5 积分",正则需覆盖新命名)
    page.locator("button").filter(
        has_text=re.compile(r"Image\d|ChatGPT|Gemini|即梦|Flare|Sunburst|Nano|Seedream")).first.click()
    page.wait_for_selector("text=模型选择", timeout=10000)
    group = LINES.get(line)
    if group:
        page.locator("button").filter(
            has_text=re.compile(re.escape(group))).first.click()
        page.wait_for_timeout(900)
    # 目标线路按钮(滚动渲染后再点)
    for _ in range(3):
        btn = page.locator("button").filter(has_text=re.compile(re.escape(line))).first
        if btn.count() > 0:
            try:
                btn.scroll_into_view_if_needed(timeout=4000)
                btn.click(timeout=5000)
                break
            except Exception:
                _scroll_line_list(page)
                continue
        _scroll_line_list(page)
    page.wait_for_timeout(800)
    if res:
        btn = page.locator("button").filter(
            has_text=re.compile(rf"^\s*{res}\s*$")).first
        if btn.is_disabled():
            raise RuntimeError(f"线路 {line} 不支持分辨率 {res}(按钮禁用)")
        btn.click()
        page.wait_for_timeout(400)
    if ratio:
        # 宽高比下拉:当前值按钮(显示"自动"或"1:1"等)
        page.locator("button").filter(
            has_text=re.compile(r"^\s*(自动|\d+:\d+)\s*$")).first.click()
        page.wait_for_selector(
            f"button:has-text('{ratio}')", timeout=8000)
        page.locator("button").filter(
            has_text=re.compile(rf"^\s*{re.escape(ratio)}\s*$")).first.click()
        page.wait_for_timeout(400)


def _history_top_img(page):
    """历史记录区最新一条(顶部)记录的 CDN 图 [src, naturalWidth];无则 None。

    实测:预览区的 img 不会随新任务更新,只有历史记录区顶部会插入新记录,
    因此结果图必须取"生成记录"锚点之下、Y 坐标最近的 CDN img。
    """
    return page.evaluate(
        "() => { const all=[...document.querySelectorAll('*')];"
        " const anchor=all.find(e=>e.children.length===0 && (e.innerText||'').trim()==='生成记录');"
        " if(!anchor) return null;"
        " const ay=anchor.getBoundingClientRect().y;"
        " const imgs=[...document.querySelectorAll('img')]"
        "   .filter(i=>i.src.includes('" + SITE["cdn_marker"] + "'))"
        "   .map(i=>({src:i.src, w:i.naturalWidth, y:i.getBoundingClientRect().y}))"
        "   .filter(o=>o.y>ay).sort((a,b)=>a.y-b.y);"
        " if(!imgs.length) return null;"
        " return [imgs[0].src, imgs[0].w]; }")


def _history_top_text(page) -> str:
    """"生成记录"之后首个记录块的文本(用于完成判定)。

    2026-09-01 站点改版:记录卡改为显示完整提示词(不再截断),
    300 字符窗口会漏掉末尾的"耗时"标志导致假超时,窗口加大到 800。
    """
    return page.evaluate(
        "() => { const m = document.body.innerText.match(/生成记录([\\s\\S]{0,800})/);"
        " return m ? m[1] : ''; }")


def _wait_result_url(page, prompt: str) -> str:
    """等最新历史记录(匹配本任务提示词)完成,取其全分辨率图 URL。

    实测要点(2026-08-22,勿回退):
    - 预览区 img 不更新,完成判定要看"生成记录"区顶部记录
    - 完成标志:该记录的"耗时"从"生成中..."变为具体秒数
    - 站点先出半分辨率图再换全分辨率,等 naturalWidth 稳定
    - 防参考图误抓:记录生成开始时的顶部图,返回时必须是与之不同的新图
      (否则会用 --ref 上传的历史记录/上一张图冒充本次结果)
    """
    needle = prompt[:12]
    # 状态判定只看"本任务记录卡"区间(完整提示词起 + 其后 250 字符,卡内依次
    # 为 提示词/线路名/徽标/耗时/时间戳)。勿用整个 800 字符窗口判"失败/耗时":
    # 窗口会混入下方旧记录的状态字样,把"生成中"的本卡误判为失败
    # (2026-09-02 实测:头像 4 次假失败,站点历史记录里其实全部"完成",白扣积分)。
    zlen = len(prompt) + 250
    deadline = time.time() + SITE["timeout_s"]
    baseline = _history_top_img(page)  # 开始时的顶部图(可能是参考图/上一条结果)
    while time.time() < deadline:
        time.sleep(3)
        seg = _history_top_text(page)
        i = seg.find(prompt) if prompt else -1
        if i < 0:
            i = seg.find(needle)
        if i < 0:
            continue
        zone = seg[i:i + zlen]
        if "失败" in zone:
            raise RuntimeError(f"站点生成失败: {zone[:120]}")
        if "耗时" not in zone or "生成中" in zone:
            continue
        # 完成:等图片 URL/naturalWidth 稳定
        last = (None, -1)
        saw_new = False
        for _ in range(10):
            page.wait_for_timeout(2500)
            cur = _history_top_img(page)
            if not cur:
                continue
            if baseline and cur[0] == baseline[0]:
                # 顶部仍是开始时就存在的图(参考图/上一条),继续等真正的新结果
                continue
            saw_new = True
            if cur[0] != last[0] and last[0] is not None:
                last = (cur[0], -1)  # src 发生替换,重新等稳定
                continue
            if cur[1] >= 2500 or cur[1] == last[1]:
                return cur[0]
            last = tuple(cur)
        if saw_new and last[0]:
            return last[0]
    # ---- 超时兜底(2026-09-01 站点改版假超时教训,勿删)----
    # 假超时 = 站点其实已生成完成,只是检测条件没命中(如文本窗口漏掉"耗时")。
    # 直接抛错会诱使操作者重试 → 同提示词重复扣积分(当天因此浪费 25 积分)。
    # 兜底:最后再做一次宽松判定——顶部记录文本匹配本提示词且已完成(有耗时、
    # 非生成中、非失败),且图片与 baseline 不同,则抢救返回,不抛错。
    seg = _history_top_text(page)
    i = seg.find(prompt) if prompt else -1
    if i < 0:
        i = seg.find(needle)
    zone = seg[i:i + zlen] if i >= 0 else ""
    if zone and "耗时" in zone and "生成中" not in zone and "失败" not in zone:
        cur = _history_top_img(page)
        if cur and (not baseline or cur[0] != baseline[0]):
            print("[webgen] 警告:主检测超时,已从历史记录兜底抢救结果图(请检查检测逻辑是否又失效)")
            return cur[0]
    raise RuntimeError(
        "生成超时或失败(未检测到新结果图)。注意:重试前先到站点历史记录确认"
        "该提示词是否真的没生成——假超时会重复扣积分!")


def _download(url: str, out_path: Path) -> None:
    """CDN 下载:真直连(绕过系统代理)→ 默认 → 显式代理,三路依次尝试。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    attempts = []
    # 1) 真直连:空 ProxyHandler 强制不走任何代理(系统代理开着时 urllib 会被劫,TLS 断流)
    attempts.append(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    # 2) 显式代理
    if PROXY:
        attempts.append(urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": PROXY, "https": PROXY})))
    last_err = None
    for opener in attempts:
        try:
            with opener.open(url, timeout=120) as resp, open(out_path, "wb") as f:
                f.write(resp.read())
            return
        except Exception as e:
            last_err = e
    raise last_err


def _run_job(page, job: dict, applied: list) -> Path:
    """在已打开的页面上执行一个生图任务。

    job: {prompt, out, refs?, line?, res?, ratio?}
    applied: 单元素列表 [(line, res, ratio)],用于跨任务缓存参数选择。
    """
    line = job.get("line") or DEFAULT_LINE
    res = job.get("res")
    ratio = job.get("ratio")
    want = (line, res, ratio)
    if want != applied[0] and (line != DEFAULT_LINE or res or ratio):
        _select_line(page, line, res, ratio)
    applied[0] = want

    refs = job.get("refs")
    if refs:
        page.set_input_files(SITE["file_input"], [str(r) for r in refs])
        # 等上传计数刷新(如 "上传 1/5")而非定长 sleep
        page.wait_for_function(
            "() => /上传 [1-9]/.test(document.body.innerText)", timeout=30000)

    page.fill(SITE["prompt_box"], job["prompt"])
    page.wait_for_timeout(200)

    page.get_by_role("button", name=SITE["generate_btn_text"]).first.click()

    url = _wait_result_url(page, job["prompt"])
    out_path = Path(job["out"])
    _download(url, out_path)
    return out_path


def generate_batch(jobs: list[dict], headless: bool = True) -> list[Path]:
    """批量生图:一次浏览器会话跑多个任务,缓存线路/分辨率选择。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(
                EDGE_PROFILE, channel="msedge", headless=headless,
                viewport={"width": 1600, "height": 1000},
                proxy={"server": PROXY} if PROXY else None,
            )
        except Exception as e:
            if "already in use" in str(e) or "lock" in str(e).lower():
                sys.exit("[webgen] Edge 正在运行,请先完全退出 Edge(含后台进程)再试")
            raise
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(SITE["url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector(SITE["prompt_box"], timeout=30000)

            results = []
            applied = [None]  # 页面表单参数缓存
            failures = []
            for i, job in enumerate(jobs, 1):
                t0 = time.time()
                try:
                    out = _run_job(page, job, applied)
                    results.append(out)
                    print(f"[webgen] ({i}/{len(jobs)}) {out.name} "
                          f"{out.stat().st_size} bytes, {time.time()-t0:.0f}s")
                except Exception as e:
                    failures.append((job.get("out"), str(e)[:200]))
                    print(f"[webgen] ({i}/{len(jobs)}) 失败: {str(e)[:150]}")
            if failures:
                print(f"[webgen] {len(failures)} 个任务失败:")
                for out, err in failures:
                    print(f"  - {out}: {err}")
            return results
        finally:
            ctx.close()


def generate(prompt: str, out_path: Path, refs: list[Path] | None = None,
             headless: bool = True, line: str = DEFAULT_LINE,
             res: str | None = None, ratio: str | None = None) -> Path:
    """单图生成(generate_batch 的单任务封装)。"""
    job = {"prompt": prompt, "out": out_path, "refs": refs,
           "line": line, "res": res, "ratio": ratio}
    results = generate_batch([job], headless=headless)
    if not results:
        raise RuntimeError(f"任务失败: {out_path}")
    return results[0]


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="绿叶 AI 网页生图通道")
    ap.add_argument("--prompt", help="单图模式的提示词")
    ap.add_argument("--out", help="单图模式的输出路径")
    ap.add_argument("--ref", default=None, help="参考图,多张逗号分隔")
    ap.add_argument("--line", default=DEFAULT_LINE,
                    help=f"线路,可选: {', '.join(LINES)}")
    ap.add_argument("--res", default=None, help="分辨率: 1K / 2K / 4K(不填=自动)")
    ap.add_argument("--ratio", default=None, help="宽高比: 1:1 / 3:2 / 16:9 等(不填=自动)")
    ap.add_argument("--batch", default=None,
                    help="批量任务 JSON:[{prompt, out, ref?, line?, res?, ratio?}, ...]")
    ap.add_argument("--headed", action="store_true", help="显示浏览器窗口(调试用)")
    args = ap.parse_args()

    if args.batch:
        import json
        raw = json.loads(Path(args.batch).read_text(encoding="utf-8"))
        jobs = []
        for j in raw:
            refs = None
            if j.get("ref"):
                refs = [Path(s.strip()) for s in j["ref"].split(",")]
            jobs.append({**j, "refs": refs})
        results = generate_batch(jobs, headless=not args.headed)
        print(f"[webgen] 批量完成 {len(results)} 张")
        return

    if not args.prompt or not args.out:
        ap.error("单图模式需要 --prompt 和 --out;批量模式用 --batch")
    refs = [Path(s.strip()) for s in args.ref.split(",")] if args.ref else None
    result = generate(args.prompt, Path(args.out), refs,
                      headless=not args.headed, line=args.line,
                      res=args.res, ratio=args.ratio)
    print(f"[webgen] 已保存: {result} ({result.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
