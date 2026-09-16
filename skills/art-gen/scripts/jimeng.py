#!/usr/bin/env python3
"""即梦 4.0(火山引擎智能视觉 CV API)调用模块。

接口形态(见 https://www.volcengine.com/docs/85621/1817045):
- POST https://visual.volcengineapi.com?Action=CVSync2AsyncSubmitTask&Version=2022-08-31
- 火山引擎 V4 签名(Region=cn-north-1, Service=cv)
- 异步任务:提交拿 task_id → 轮询 CVSync2AsyncGetResult 拿 image_urls
- req_key 固定 jimeng_t2i_v40

注意:参考图仅接受公网 URL(image_urls),本地图需先传图床,暂不支持。
"""
import datetime
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request

HOST = "visual.volcengineapi.com"
REGION = "cn-north-1"
SERVICE = "cv"
VERSION = "2022-08-31"
REQ_KEY = "jimeng_t2i_v40"


def _sign(method: str, query: dict, body: bytes, ak: str, sk: str) -> dict:
    """火山引擎 V4 签名,返回应附加的请求头。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")

    payload_hash = hashlib.sha256(body).hexdigest()
    content_type = "application/json"

    canonical_query = "&".join(
        f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(query.items())
    )
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{HOST}\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{x_date}\n"
    )
    canonical_request = "\n".join([
        method, "/", canonical_query, canonical_headers, signed_headers, payload_hash,
    ])

    credential_scope = f"{short_date}/{REGION}/{SERVICE}/request"
    string_to_sign = "\n".join([
        "HMAC-SHA256", x_date, credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    def _hmac(key: bytes, msg: str) -> bytes:
        return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

    k_signing = _hmac(_hmac(_hmac(_hmac(sk.encode("utf-8"), short_date), REGION), SERVICE), "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    return {
        "Content-Type": content_type,
        "Host": HOST,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": (
            f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


def _call(action: str, payload: dict, ak: str, sk: str) -> dict:
    body = json.dumps(payload).encode("utf-8")
    query = {"Action": action, "Version": VERSION}
    headers = _sign("POST", query, body, ak, sk)
    url = f"https://{HOST}/?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
    if data.get("code") != 10000:
        raise RuntimeError(f"API 错误 code={data.get('code')}: {data.get('message')}")
    return data


def generate(prompt: str, ak: str, sk: str, size: int = 2048 * 2048,
             poll_interval: float = 3.0, timeout: float = 300.0) -> list[str]:
    """即梦 4.0 文生图,返回图片 URL 列表。"""
    submit = _call("CVSync2AsyncSubmitTask", {
        "req_key": REQ_KEY,
        "prompt": prompt,
        "size": size,
        "force_single": True,
    }, ak, sk)
    task_id = submit["data"]["task_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        result = _call("CVSync2AsyncGetResult", {
            "req_key": REQ_KEY,
            "task_id": task_id,
            "req_json": json.dumps({"return_url": True}),
        }, ak, sk)
        status = result["data"].get("status")
        if status == "done":
            urls = result["data"].get("image_urls")
            if urls:
                return urls
            raise RuntimeError("任务完成但未返回图片(可能被审核拦截)")
        if status in ("not_found", "expired"):
            raise RuntimeError(f"任务状态异常: {status}")
    raise RuntimeError(f"生成超时({timeout}s)")
