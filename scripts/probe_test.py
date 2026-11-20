import os
import io
import json
import time
import base64
from datetime import datetime, timezone, timedelta

import requests
from curl_cffi import requests as cffi_requests
from PIL import Image

UTC8 = timezone(timedelta(hours=8))
NOW_STR = datetime.now(UTC8).strftime("%Y-%m-%d %H:%M:%S (UTC+8)")

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

VISION_MODELS = [
    "gemini-4-hydrogen",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemma-4-31b-it",
]

GROQ_TEXT_MODELS = [
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
]


def log(msg: str):
    print(msg, flush=True)


def probe_shanghai_weather() -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=31.2304&longitude=121.4737"
        "&daily=temperature_2m_max,temperature_2m_min,apparent_temperature_max,precipitation_probability_max"
        "&timezone=Asia%2FShanghai&forecast_days=7"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        daily = r.json().get("daily", {})
        today_min = daily["temperature_2m_min"][0]
        today_max = daily["temperature_2m_max"][0]
        rain_max = max(daily.get("precipitation_probability_max", [0]))
        return {
            "ok": True,
            "summary": f"今日 {today_min}~{today_max}℃，未来7天最高降水概率 {rain_max}%",
        }
    except Exception as e:
        return {"ok": False, "summary": f"失败: {e}"}


def test_http_get(name: str, url: str, headers=None, params=None, is_json=True):
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if headers:
        default_headers.update(headers)
    try:
        r = cffi_requests.get(
            url,
            headers=default_headers,
            params=params,
            impersonate="chrome",
            timeout=20,
        )
        snippet = r.text[:180].replace("\n", " ")
        parsed_info = ""
        sample_img = None
        if r.status_code == 200 and is_json:
            try:
                data = r.json()
                if isinstance(data, dict):
                    keys = list(data.keys())[:6]
                    parsed_info = f"JSON keys={keys}"
                    # 解析 Shopify 样式产品统计
                    if "products" in data and isinstance(data["products"], list):
                        prods = data["products"]
                        vendors = {}
                        for p in prods:
                            v = p.get("vendor", "unknown")
                            vendors[v] = vendors.get(v, 0) + 1
                            if not sample_img and p.get("images"):
                                sample_img = p["images"][0].get("src")
                        parsed_info = f"商品数={len(prods)}, 品牌分布={dict(list(vendors.items())[:5])}"
                elif isinstance(data, list):
                    parsed_info = f"JSON list len={len(data)}"
            except Exception as je:
                parsed_info = f"非标准JSON({je})"
        return {
            "name": name,
            "status": r.status_code,
            "info": parsed_info or snippet[:100],
            "sample_img": sample_img,
        }
    except Exception as e:
        return {"name": name, "status": "ERR", "info": str(e)[:120], "sample_img": None}


def test_uniqlo_cn():
    url = "https://www.uniqlo.cn/hmall-sc-service/search/searchWithDescriptionAndConditions/zh_CN"
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://www.uniqlo.cn",
        "Referer": "https://www.uniqlo.cn/c/10101.html",
    }
    payload = {
        "url": "/c/10101.html",
        "pageInfo": {"page": 1, "pageSize": 5, "withSideBar": "Y"},
        "belongTo": "pc",
        "rank": "overall",
        "priceRange": {"low": 0, "high": 0},
        "color": [],
        "size": [],
        "season": [],
        "material": [],
        "sex": [],
        "identity": [],
        "insiteDescription": "",
        "exist": [],
        "searchFlag": False,
        "description": "",
    }
    try:
        r = cffi_requests.post(
            url, json=payload, headers=headers, impersonate="chrome", timeout=20
        )
        info = r.text[:100].replace("\n", " ")
        sample_img = None
        if r.status_code == 200:
            data = r.json()
            resp = data.get("resp", [])
            if resp and isinstance(resp, list) and len(resp) > 1:
                items = resp[1] if isinstance(resp[1], list) else []
                info = f"成功拉取男装商品={len(items)}"
                if items and isinstance(items[0], dict):
                    pic = items[0].get("mainPic", "")
                    if pic:
                        sample_img = (
                            f"https://www.uniqlo.cn{pic}"
                            if pic.startswith("/")
                            else pic
                        )
        return {
            "name": "Uniqlo CN (男装API)",
            "status": r.status_code,
            "info": info,
            "sample_img": sample_img,
        }
    except Exception as e:
        return {"name": "Uniqlo CN (男装API)", "status": "ERR", "info": str(e)[:120], "sample_img": None}


def make_test_image_b64(img_url: str | None) -> tuple[str, str]:
    """优先下载探针抓到的真实商品图缩略图；失败则在内存中生成测试图"""
    if img_url:
        try:
            r = cffi_requests.get(img_url, impersonate="chrome", timeout=15)
            if r.status_code == 200 and len(r.content) > 1000:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                img.thumbnail((360, 360))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=80)
                return base64.b64encode(buf.getvalue()).decode("utf-8"), f"真实商品图({img_url[:50]}...)"
        except Exception:
            pass

    img = Image.new("RGB", (240, 240), color=(45, 55, 72))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8"), "内存占位测试图"


def probe_vision_models(img_b64: str) -> list[dict]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return [{"model": "ALL", "status": "SKIP", "info": "未配置 GEMINI_API_KEY"}]

    results = []
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for m in VISION_MODELS:
        payload = {
            "model": m,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "请看这张商品图，假设这是一款售价899元、材质/做工扎实的男装或男鞋单品。"
                                "请用严格的JSON格式返回：{\"color_style\": \"简短配色风格\", \"score\": 8.5}"
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 256,
        }
        t0 = time.time()
        try:
            r = requests.post(GEMINI_ENDPOINT, headers=headers, json=payload, timeout=45)
            cost = round(time.time() - t0, 2)
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"].replace("\n", " ")
                results.append({"model": m, "status": f"200 ({cost}s)", "info": content[:120]})
            else:
                results.append({"model": m, "status": r.status_code, "info": r.text[:140]})
        except Exception as e:
            results.append({"model": m, "status": "ERR", "info": str(e)[:120]})
        time.sleep(2.5)
    return results


def probe_groq_models() -> list[dict]:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return [{"model": "ALL", "status": "SKIP", "info": "未配置 GROQ_API_KEY"}]

    results = []
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    for m in GROQ_TEXT_MODELS:
        payload = {
            "model": m,
            "messages": [
                {
                    "role": "user",
                    "content": "将 'Men Wool Blend Oversized Coat - 75% Wool 25% Polyamide' 提取为JSON: {\"category\":\"外套大衣\",\"natural_pct\":75}",
                }
            ],
            "temperature": 0.1,
            "max_tokens": 150,
        }
        t0 = time.time()
        try:
            r = requests.post(GROQ_ENDPOINT, headers=headers, json=payload, timeout=30)
            cost = round(time.time() - t0, 2)
            if r.status_code == 200:
                content = r.json()["choices"][0]["message"]["content"].replace("\n", " ")
                results.append({"model": m, "status": f"200 ({cost}s)", "info": content[:120]})
            else:
                results.append({"model": m, "status": r.status_code, "info": r.text[:140]})
        except Exception as e:
            results.append({"model": m, "status": "ERR", "info": str(e)[:120]})
        time.sleep(1.5)
    return results


def main():
    log(f"=== 开始探针测试 | {NOW_STR} ===")
    weather = probe_shanghai_weather()
    log(f"[上海天气] {weather}")

    brand_checks = [
        # 服装品牌（5家：Uniqlo / Zara / Massimo Dutti / COS / UR）
        test_uniqlo_cn(),
        test_http_get("Zara CN (类目JSON)", "https://www.zara.cn/cn/zh/categories?ajax=true"),
        test_http_get("Zara HK (备选JSON)", "https://www.zara.com/hk/zh/categories?ajax=true"),
        test_http_get("Massimo Dutti CN (类目JSON)", "https://www.massimodutti.cn/cn/zh/categories?ajax=true"),
        test_http_get("Massimo Dutti HK (备选JSON)", "https://www.massimodutti.com/hk/zh/categories?ajax=true"),
        test_http_get("COS CN (首页连通)", "https://www.cos.cn/", is_json=False),
        test_http_get("COS HK (男装页)", "https://www.cos.com/en-hk/men/view-all.html", is_json=False),
        test_http_get("UR CN (官网连通)", "https://www.ur.cn/", is_json=False),
        test_http_get("UR Global (全站JSON)", "https://global.urbanrevivo.com/products.json?limit=25"),
        test_http_get("UR Global (男装JSON)", "https://global.urbanrevivo.com/collections/men/products.json?limit=25"),

        # 鞋履品牌（4家：Nike / Adidas / New Balance / Salomon + 买手店兜底）
        test_http_get(
            "Nike CN (Browse API)",
            "https://api.nike.com/cic/browse/v2",
            params={
                "queryid": "products",
                "country": "cn",
                "endpoint": "/product_feed/rollup_threads/v2?filter=marketplace(CN)&filter=language(zh-Hans)&filter=attributeIds(0f64ecc7-d624-4e91-b171-b83a03dd8550,16633190-45e5-4830-a068-232ac7aea82c)&anchor=0&count=5",
            },
        ),
        test_http_get("Adidas CN (首页连通)", "https://www.adidas.com.cn/", is_json=False),
        test_http_get("New Balance CN (首页连通)", "https://www.newbalance.com.cn/", is_json=False),
        test_http_get("Salomon CN (首页连通)", "https://www.salomon.com.cn/", is_json=False),
        test_http_get("买手店 KITH (鞋履JSON)", "https://kith.com/collections/mens-footwear/products.json?limit=25"),
        test_http_get("买手店 Feature (鞋履JSON)", "https://feature.com/collections/footwear/products.json?limit=25"),
        test_http_get("买手店 ExtraButter (鞋履JSON)", "https://extrabutterny.com/collections/footwear/products.json?limit=25"),
    ]

    sample_img_url = next((x["sample_img"] for x in brand_checks if x.get("sample_img")), None)
    img_b64, img_source = make_test_image_b64(sample_img_url)
    log(f"[测试图片源] {img_source}")

    vision_checks = probe_vision_models(img_b64)
    groq_checks = probe_groq_models()

    # 拼装 Markdown 报告并写入 GitHub Step Summary
    lines = [
        f"## 🧭 Shanghai Menswear & Sneaker 探针报告",
        f"- **测试时间:** `{NOW_STR}`",
        f"- **上海天气 (Open-Meteo):** `{weather['summary']}`",
        f"- **视觉测试图源:** `{img_source}`",
        "",
        "### 1. 数据源连通性探测",
        "| 数据源 | 状态码 | 返回摘要 |",
        "| :--- | :--- | :--- |",
    ]
    for b in brand_checks:
        safe_info = str(b["info"]).replace("|", "/").replace("\n", " ")
        lines.append(f"| {b['name']} | `{b['status']}` | {safe_info} |")

    lines += [
        "",
        "### 2. Gemini 视觉多模态模型池探测",
        "| 模型 | 状态 / 耗时 | 返回片段 |",
        "| :--- | :--- | :--- |",
    ]
    for v in vision_checks:
        safe_info = str(v["info"]).replace("|", "/").replace("\n", " ")
        lines.append(f"| `{v['model']}` | `{v['status']}` | {safe_info} |")

    lines += [
        "",
        "### 3. Groq 文本清洗/兜底模型探测",
        "| 模型 | 状态 / 耗时 | 返回片段 |",
        "| :--- | :--- | :--- |",
    ]
    for g in groq_checks:
        safe_info = str(g["info"]).replace("|", "/").replace("\n", " ")
        lines.append(f"| `{g['model']}` | `{g['status']}` | {safe_info} |")

    report_md = "\n".join(lines)
    log("\n" + report_md)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write(report_md + "\n")


if __name__ == "__main__":
    main()
