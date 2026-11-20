import os
import io
import re
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

VISION_MODELS_CHECK = [
    "gemini-4-hydrogen",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
]


def clean_md(text: str) -> str:
    s = re.sub(r"<[^>]*>", " ", str(text))
    s = s.replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("|", "/").replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", s).strip()[:160]


def inspect_uniqlo_sample():
    url = "https://d.uniqlo.cn/p/hmall-sc-service/search/searchWithDescriptionAndConditions/zh_CN"
    payload = {
        "url": "/c/10101.html",
        "pageInfo": {"page": 1, "pageSize": 3, "withSideBar": "Y"},
        "belongTo": "pc", "rank": "overall", "priceRange": {"low": 0, "high": 0},
        "color": [], "size": [], "season": [], "material": [], "sex": [], "identity": [],
        "insiteDescription": "", "exist": [], "searchFlag": False, "description": "",
    }
    try:
        r = cffi_requests.post(url, json=payload, headers={"Content-Type": "application/json"}, impersonate="chrome", timeout=20)
        j = r.json()
        resp = j.get("resp", [])
        # 找包含商品列表的元素
        items = []
        for elem in resp:
            if isinstance(elem, list) and elem and isinstance(elem[0], dict) and ("productCode" in elem[0] or "name" in elem[0]):
                items = elem
                break
        if items:
            s = items[0]
            keys = list(s.keys())[:12]
            return f"200 | keys={keys} | sample_name={s.get('name')} | price={s.get('minPrice')} | mainPic={s.get('mainPic')}"
        return f"200 | resp结构={[type(x).__name__ for x in resp]}"
    except Exception as e:
        return f"ERR: {e}"


def inspect_zara_sample():
    try:
        r = cffi_requests.get("https://www.zara.cn/cn/zh/categories?ajax=true", impersonate="chrome", timeout=20)
        cats = r.json().get("categories", [])
        # 查找男装(MAN)下的子分类
        man_cats = [c for c in cats if str(c.get("name", "")).upper() in ("MAN", "男装") or "man" in str(c.get("key", "")).lower()]
        sub_id = None
        if man_cats:
            for sub in man_cats[0].get("subcategories", []):
                if sub.get("subcategories"):
                    sub_id = sub["subcategories"][0].get("id")
                    break
                elif sub.get("id"):
                    sub_id = sub.get("id")
                    break
        if not sub_id:
            return f"200 | top_cats={[c.get('name') for c in cats[:5]]}"
        pr = cffi_requests.get(f"https://www.zara.cn/itxrest/2/catalog/store/11716/category/{sub_id}/products?locale=zh_CN", impersonate="chrome", timeout=20)
        if pr.status_code != 200:
            return f"分类={sub_id}, 商品接口={pr.status_code}"
        pj = pr.json()
        pg = pj.get("productGroups", [{}])[0].get("elements", [{}])[0].get("commercialComponents", [])
        if pg:
            item = pg[0]
            return f"200 | 分类={sub_id}, 商品数={len(pg)} | name={item.get('name')} | price={item.get('price')} | xmedia_len={len(item.get('xmedia', []))}"
        return f"200 | keys={list(pj.keys())}"
    except Exception as e:
        return f"ERR: {e}"


def inspect_md_cn():
    try:
        r = cffi_requests.get("https://www.massimodutti.cn/cn/", impersonate="chrome", timeout=20)
        m_store = re.search(r"storeId['\":\s]+(\d{6,})", r.text)
        m_cat = re.search(r"catalogId['\":\s]+(\d{6,})", r.text)
        store_id = m_store.group(1) if m_store else "34009471"
        cat_id = m_cat.group(1) if m_cat else "30359526"
        cr = cffi_requests.get(
            f"https://www.massimodutti.cn/itxrest/2/catalog/store/{store_id}/{cat_id}/category?languageId=-7&typeCatalog=1&appId=1",
            impersonate="chrome", timeout=20
        )
        return f"首页={r.status_code} | store={store_id}, cat={cat_id} | 类目API={cr.status_code} keys={list(cr.json().keys())[:4] if cr.status_code==200 else cr.text[:60]}"
    except Exception as e:
        return f"ERR: {e}"


def inspect_cos_hk():
    try:
        r = cffi_requests.get("https://www.cos.com/en-hk/men/view-all.html", impersonate="chrome", timeout=20)
        has_next = "__NEXT_DATA__" in r.text
        has_prod = "product" in r.text.lower()
        scripts = re.findall(r'<script[^>]*id="([^"]+)"', r.text)
        return f"200 | len={len(r.text)} | __NEXT_DATA__={has_next} | script_ids={scripts[:6]} | has_product={has_prod}"
    except Exception as e:
        return f"ERR: {e}"


def inspect_nike_cn():
    try:
        r = cffi_requests.get(
            "https://api.nike.com/discover/product_wall/v1/marketplace/CN/language/zh-Hans/consumerChannelId/d9a5bc42-4b9c-4976-858a-f159cf99c647",
            params={
                "path": "/cn/w/mens-shoes-nik1zy7ok",
                "attributeIds": "0f64ecc7-d624-4e91-b171-b83a03dd8550,16633190-45e5-4830-a068-232ac7aea82c",
                "queryType": "PRODUCTS",
                "anchor": 0,
                "count": 24,
            },
            headers={"nike-api-caller-id": "nike:dotcom:browse:wall.client:2.0"},
            impersonate="chrome",
            timeout=20,
        )
        if r.status_code == 200:
            j = r.json()
            prods = j.get("productGroupings", [])
            sample = ""
            if prods:
                p0 = prods[0].get("products", [{}])[0]
                sample = f"title={p0.get('copy', {}).get('title')}, price={p0.get('prices', {}).get('currentPrice')}"
            return f"200 | groupings={len(prods)} | {sample}"
        return f"{r.status_code} | {r.text[:100]}"
    except Exception as e:
        return f"ERR: {e}"


def inspect_shopify_details(name: str, url: str):
    try:
        r = cffi_requests.get(url, impersonate="chrome", timeout=20)
        if r.status_code != 200:
            return f"{r.status_code} | {r.text[:80]}"
        prods = r.json().get("products", [])
        vendors = {}
        for p in prods:
            v = p.get("vendor", "unknown")
            vendors[v] = vendors.get(v, 0) + 1
        top_v = dict(sorted(vendors.items(), key=lambda x: -x[1])[:8])
        sample = prods[0] if prods else {}
        body_snip = re.sub(r"<[^>]+>", " ", sample.get("body_html", "") or "")[:60]
        return f"200 | 数={len(prods)} | 品牌={top_v} | 样本描述={body_snip}"
    except Exception as e:
        return f"ERR: {e}"


def inspect_vision_fix():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return []
    # 生成简单小图用于测试 thinking 参数修复
    img = Image.new("RGB", (240, 240), color=(40, 50, 65))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    results = []
    for m in VISION_MODELS_CHECK:
        payload = {
            "model": m,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "返回JSON: {\"status\": \"ok\", \"color\": \"深灰\"}"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                ],
            }],
            "max_tokens": 2048,
        }
        if m in ("gemini-4-hydrogen", "gemini-3.8-flash"):
            payload["reasoning_effort"] = "low"

        t0 = time.time()
        try:
            r = requests.post(GEMINI_ENDPOINT, headers=headers, json=payload, timeout=60)
            cost = round(time.time() - t0, 2)
            if r.status_code == 200:
                msg = r.json()["choices"][0]["message"]
                content = msg.get("content") or f"[无content, keys={list(msg.keys())}]"
                results.append((m, f"200 ({cost}s)", clean_md(content)))
            else:
                results.append((m, str(r.status_code), clean_md(r.text)))
        except Exception as e:
            results.append((m, "ERR", clean_md(str(e))))
        time.sleep(2.5)
    return results


def main():
    checks = [
        ("Uniqlo CN 样本字段", inspect_uniqlo_sample()),
        ("Zara CN 类目与商品样本", inspect_zara_sample()),
        ("Massimo Dutti CN 店铺探测", inspect_md_cn()),
        ("COS HK 页面结构", inspect_cos_hk()),
        ("UR Global 男装集合", inspect_shopify_details("UR", "https://global.urbanrevivo.com/collections/best-sellers-men-nv030700/products.json?limit=20")),
        ("MUJI US 男装集合", inspect_shopify_details("MUJI", "https://www.muji.us/collections/apparel/products.json?limit=50")),
        ("Nike CN (count=24)", inspect_nike_cn()),
        ("KITH 全量鞋履(250条)", inspect_shopify_details("KITH", "https://kith.com/collections/mens-footwear/products.json?limit=250")),
        ("Feature 全量鞋履(250条)", inspect_shopify_details("Feature", "https://feature.com/collections/footwear/products.json?limit=250")),
        ("ExtraButter 全量鞋履(250条)", inspect_shopify_details("EB", "https://extrabutterny.com/collections/footwear/products.json?limit=250")),
    ]
    v_checks = inspect_vision_fix()

    lines = [
        f"## 🔬 三轮字段与 Hydrogen 修复探测 ({NOW_STR})",
        "",
        "| 检查项 | 结果详情 |",
        "| :--- | :--- |",
    ]
    for k, v in checks:
        lines.append(f"| {k} | {clean_md(v)} |")

    lines += [
        "",
        "| 模型 (带低推理/2048Token) | 状态/耗时 | 输出内容 |",
        "| :--- | :--- | :--- |",
    ]
    for m, st, info in v_checks:
        lines.append(f"| `{m}` | `{st}` | {info} |")

    md = "\n".join(lines)
    print(md, flush=True)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.getenv("GITHUB_STEP_SUMMARY"), "a", encoding="utf-8") as f:
            f.write(md + "\n")


if __name__ == "__main__":
    main()
