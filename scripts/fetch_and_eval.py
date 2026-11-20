import os
import re
import io
import json
import time
import base64
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from PIL import Image

UTC8 = timezone(timedelta(hours=8))
NOW_DT = datetime.now(UTC8)
NOW_STR = NOW_DT.strftime("%Y-%m-%d %H:%M (UTC+8)")

DATA_PATH = Path("data/items.json")
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

# 经探针实测验证后的多模态视觉主力轮询池
VISION_POOL = [
    {"provider": "gemini", "model": "gemini-4-hydrogen", "reasoning": "low", "timeout": 115},
    {"provider": "gemini", "model": "gemini-3.8-flash", "reasoning": "low", "timeout": 75},
    {"provider": "gemini", "model": "gemini-3.6-flash", "reasoning": None, "timeout": 60},
    {"provider": "gemini", "model": "gemini-3.5-flash-lite", "reasoning": None, "timeout": 45},
    {"provider": "gemini", "model": "gemma-4-31b-it", "reasoning": None, "timeout": 60},
]

# 纯文本预处理 / 应急兜底 / 历史单品上海穿搭建议刷新池
GROQ_POOL = [
    {"provider": "groq", "model": "openai/gpt-oss-120b", "timeout": 35},
    {"provider": "groq", "model": "qwen/qwen3.8-27b", "timeout": 35},
]

VALID_CATEGORIES = ["外套大衣", "毛衣针织", "衬衫卫衣", "休闲裤装", "精选鞋履"]
VALID_STYLES = ["老钱静奢", "极简通勤", "松弛街头", "都市机能", "复古运动", "质感基础"]
VALID_METRICS = ["天然面料", "上海适穿", "防水耐造", "高性价比"]

MAX_NEW_AI_ITEMS_PER_RUN = 40  # 每批5件最多8批，保护免费层高配额度
BATCH_SIZE = 5
MAX_STORED_ITEMS = 180

EXCLUDE_KEYWORDS = [
    "women", "woman", "ladies", "lady", "female", "girl", "skirt", "dress",
    "blouse", "bikini", "bra", "heel", "heels", "pump", "maternity", "kids",
    "boy", "toddler", "infant", "boot", "boots", "chelsea", "ankle boot",
    "chukka", "sandal", "slide", "slipper", "clog", "mule", "flip flop",
    "sock", "socks", "underwear", "boxer", "brief", "pajama", "pyjama",
    "bag", "tote", "backpack", "wallet", "belt", "cap", "hat", "beanie",
    "scarf", "glove", "sunglass", "perfume", "fragrance", "home",
    "女装", "女士", "裙", "胸罩", "文胸", "内裤", "袜", "靴", "凉鞋",
    "拖鞋", "包", "帽", "围巾", "腰带", "香水", "童装", "家居",
]


def log(msg: str):
    print(f"[{datetime.now(UTC8).strftime('%H:%M:%S')}] {msg}", flush=True)


def make_item_id(brand: str, raw_id: str, title: str) -> str:
    seed = f"{brand.lower()}|{raw_id}|{title.lower().strip()}"
    return hashlib.md5(seed.encode("utf-8")).hexdigest()[:12]


def is_excluded(text: str) -> bool:
    low = text.lower()
    for kw in EXCLUDE_KEYWORDS:
        if re.search(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", low) if kw.isascii() else (kw in low):
            return True
    return False


def guess_apparel_category(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ["coat", "jacket", "blazer", "parka", "trench", "overcoat", "blouson", "down", "vest", "windbreaker", "anorak", "外套", "大衣", "夹克", "风衣", "西装", "羽绒", "棉服", "马甲"]):
        return "外套大衣"
    if any(k in t for k in ["sweater", "knit", "cardigan", "pullover", "cashmere", "turtleneck", "crewneck knit", "毛衣", "针织", "开衫", "羊绒", "羊毛衫"]):
        return "毛衣针织"
    if any(k in t for k in ["trouser", "pant", "chino", "jean", "denim", "slack", "cargo", "jogger", "sweatpant", "裤", "牛仔"]):
        return "休闲裤装"
    if any(k in t for k in ["shirt", "overshirt", "polo", "hoodie", "sweatshirt", "tee", "t-shirt", "fleece", "衬衫", "卫衣", "连帽", "打底", "t恤"]):
        return "衬衫卫衣"
    return "衬衫卫衣"


def clean_html(raw: str) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


# ================= 1. 气象与实时汇率 =================

def fetch_shanghai_weather() -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=31.2304&longitude=121.4737"
        "&daily=temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,precipitation_probability_max"
        "&timezone=Asia%2FShanghai&forecast_days=7"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        d = r.json()["daily"]
        t_min_week = round(min(d["temperature_2m_min"]), 1)
        t_max_week = round(max(d["temperature_2m_max"]), 1)
        today_min = round(d["temperature_2m_min"][0], 1)
        today_max = round(d["temperature_2m_max"][0], 1)
        rain_max = max(d.get("precipitation_probability_max", [0]))
        avg_temp = round((today_min + today_max) / 2, 1)

        if avg_temp <= 10:
            dress_hint = "寒冷防风（建议毛呢大衣/羽绒/厚羊绒针织叠穿）"
        elif avg_temp <= 17:
            dress_hint = "凉爽微风（适合风衣/夹克/薄针织配长裤）"
        elif avg_temp <= 24:
            dress_hint = "温和舒适（适合长袖衬衫/卫衣/薄外套单穿）"
        else:
            dress_hint = "温暖偏热（适合轻薄透气棉麻短袖与网面跑鞋）"

        return {
            "today_min": today_min,
            "today_max": today_max,
            "week_min": t_min_week,
            "week_max": t_max_week,
            "rain_max": rain_max,
            "summary": f"今日 {today_min}~{today_max}℃（本周 {t_min_week}~{t_max_week}℃），最高降水概率 {rain_max}%",
            "dress_hint": dress_hint,
        }
    except Exception as e:
        log(f"天气获取失败，使用兜底值: {e}")
        return {
            "today_min": 8.0, "today_max": 15.0, "week_min": 6.0, "week_max": 16.0,
            "rain_max": 20, "summary": "上海气象暂未刷新", "dress_hint": "适中厚度分层穿搭",
        }


def fetch_fx_rates() -> dict:
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=12)
        rates = r.json().get("rates", {})
        cny = float(rates.get("CNY", 7.25))
        hkd = float(rates.get("HKD", 7.78))
        return {"USD_CNY": cny, "HKD_CNY": round(cny / hkd, 4)}
    except Exception:
        return {"USD_CNY": 7.25, "HKD_CNY": 0.93}


# ================= 2. 10大品牌爬虫模块 =================

def fetch_uniqlo_cn(limit=18) -> list[dict]:
    items = []
    url = "https://d.uniqlo.cn/p/hmall-sc-service/search/searchWithDescriptionAndConditions/zh_CN"
    # 遍历优衣库男装常见类目分页
    for cat_url in ["/c/10101.html", "/c/men.html"]:
        payload = {
            "url": cat_url,
            "pageInfo": {"page": 1, "pageSize": 30, "withSideBar": "Y"},
            "belongTo": "pc", "rank": "overall", "priceRange": {"low": 0, "high": 0},
            "color": [], "size": [], "season": [], "material": [], "sex": [], "identity": [],
            "insiteDescription": "", "exist": [], "searchFlag": False, "description": "",
        }
        try:
            r = cffi_requests.post(url, json=payload, headers={"Content-Type": "application/json"}, impersonate="chrome", timeout=20)
            if r.status_code != 200:
                continue
            resp = r.json().get("resp", [])
            prod_list = []
            for elem in resp:
                if isinstance(elem, list) and elem and isinstance(elem[0], dict) and (
                    "productCode" in elem[0] or "minPrice" in elem[0] or "mainPic" in elem[0]
                ):
                    prod_list = elem
                    break
            for p in prod_list:
                title = p.get("name") or p.get("fullName") or ""
                code = p.get("productCode") or p.get("code") or ""
                if not title or not code or is_excluded(title):
                    continue
                price = int(float(p.get("minPrice") or p.get("price") or 0))
                if price <= 0:
                    continue
                pic = p.get("mainPic", "")
                cover = f"https://www.uniqlo.cn{pic}" if pic.startswith("/") else pic
                if not cover:
                    continue
                # 尝试拉取优衣库详情页材质 JSON
                mat_text = p.get("material") or p.get("description") or ""
                try:
                    dr = cffi_requests.get(f"https://www.uniqlo.cn/data/products/spu/zh_CN/{code}.json", impersonate="chrome", timeout=10)
                    if dr.status_code == 200:
                        dj = dr.json()
                        summary = dj.get("summary", {})
                        mat_text = summary.get("materialDescription") or summary.get("instruction") or mat_text
                except Exception:
                    pass
                items.append({
                    "id": make_item_id("Uniqlo", code, title),
                    "brand": "Uniqlo",
                    "category": guess_apparel_category(title),
                    "title": title,
                    "price_cny": price,
                    "price_note": f"¥{price} (官网人民币)",
                    "material_raw": clean_html(mat_text)[:220] or "优衣库官网男装当季款",
                    "cover_img": cover,
                    "model_img": "",
                    "url": f"https://www.uniqlo.cn/product-detail.html?productCode={code}",
                    "is_shoe": False,
                })
                if len(items) >= limit:
                    return items
        except Exception as e:
            log(f"Uniqlo 抓取异常 ({cat_url}): {e}")
    return items


def _collect_inditex_leaf_cats(cats: list, target_cat_map: dict, out: list):
    for c in cats:
        name = str(c.get("name", ""))
        if is_excluded(name):
            continue
        subs = c.get("subcategories", [])
        if subs:
            _collect_inditex_leaf_cats(subs, target_cat_map, out)
        else:
            cid = c.get("id")
            if cid:
                mapped = guess_apparel_category(name)
                if mapped != "精选鞋履":
                    out.append((cid, name, mapped))


def fetch_zara_cn(limit=18) -> list[dict]:
    items = []
    try:
        r = cffi_requests.get("https://www.zara.cn/cn/zh/categories?ajax=true", impersonate="chrome", timeout=20)
        cats = r.json().get("categories", [])
        men_root = [c for c in cats if "男士" in str(c.get("name", ""))]
        if not men_root:
            return []
        leaf_cats = []
        _collect_inditex_leaf_cats(men_root[0].get("subcategories", []), {}, leaf_cats)

        seen_cats = set()
        # 每类至多抽2个子分类避免请求过多
        cat_counts = {}
        for cid, cname, mapped in leaf_cats:
            if cid in seen_cats or cat_counts.get(mapped, 0) >= 2:
                continue
            if any(k in cname for k in ["查看全部", "特惠", "新品"]) and cat_counts.get(mapped, 0) >= 1:
                continue
            seen_cats.add(cid)
            cat_counts[mapped] = cat_counts.get(mapped, 0) + 1

            pr = cffi_requests.get(
                f"https://www.zara.cn/itxrest/2/catalog/store/11716/category/{cid}/products?locale=zh_CN",
                impersonate="chrome", timeout=20,
            )
            if pr.status_code != 200:
                continue
            groups = pr.json().get("productGroups", [])
            for g in groups:
                for el in g.get("elements", []):
                    for comp in el.get("commercialComponents", []):
                        title = comp.get("name") or ""
                        pid = str(comp.get("id") or "")
                        price_raw = comp.get("price") or 0
                        if not title or not pid or price_raw <= 0 or is_excluded(title):
                            continue
                        price = int(round(price_raw / 100))
                        xmedia = comp.get("xmedia", [])
                        plain_imgs, model_imgs = [], []
                        for xm in xmedia:
                            xm_url = xm.get("url") or ""
                            if not xm_url and xm.get("path") and xm.get("name"):
                                xm_url = f"https://static.zara.net/photos///{xm['path']}/w/563/{xm['name']}.jpg?ts={xm.get('timestamp', '')}"
                            xm_url = xm_url.replace("{width}", "563")
                            if not xm_url:
                                continue
                            if xm.get("kind") == "plain":
                                plain_imgs.append(xm_url)
                            else:
                                model_imgs.append(xm_url)
                        cover = plain_imgs[0] if plain_imgs else (model_imgs[0] if model_imgs else "")
                        if not cover:
                            continue
                        model_img = model_imgs[0] if (plain_imgs and model_imgs) else (model_imgs[1] if len(model_imgs) > 1 else "")

                        seo = comp.get("seo", {})
                        kw = seo.get("keyword", "")
                        seo_pid = seo.get("seoProductId", "")
                        prod_url = f"https://www.zara.cn/cn/zh/{kw}-p{seo_pid}.html" if kw and seo_pid else "https://www.zara.cn/cn/zh/man-l534.html"

                        # 拉取材质细节
                        mat_raw = comp.get("description") or ""
                        if kw and seo_pid and len(items) < limit:
                            try:
                                dr = cffi_requests.get(f"{prod_url}?ajax=true", impersonate="chrome", timeout=12)
                                if dr.status_code == 200:
                                    detail = dr.json().get("product", {}).get("detail", {})
                                    comps = detail.get("colors", [{}])[0].get("composition", [])
                                    if comps:
                                        parts = [f"{c.get('description','')} {c.get('percentage','')}" for c in comps]
                                        mat_raw = " / ".join(parts)
                            except Exception:
                                pass

                        items.append({
                            "id": make_item_id("Zara", pid, title),
                            "brand": "Zara",
                            "category": guess_apparel_category(f"{cname} {title}"),
                            "title": title,
                            "price_cny": price,
                            "price_note": f"¥{price} (官网人民币)",
                            "material_raw": clean_html(mat_raw)[:220] or "Zara 男装当季系列",
                            "cover_img": cover,
                            "model_img": model_img,
                            "url": prod_url,
                            "is_shoe": False,
                        })
                        if len(items) >= limit:
                            return items
    except Exception as e:
        log(f"Zara CN 抓取异常: {e}")
    return items


def fetch_massimo_dutti_cn(limit=15) -> list[dict]:
    items = []
    try:
        hr = cffi_requests.get("https://www.massimodutti.cn/cn/", impersonate="chrome", timeout=20)
        m_store = re.search(r"storeId['\":\s]+(\d{6,})", hr.text)
        m_cat = re.search(r"catalogId['\":\s]+(\d{6,})", hr.text)
        store_id = m_store.group(1) if m_store else "35009478"
        cat_id = m_cat.group(1) if m_cat else "30359526"

        cr = cffi_requests.get(
            f"https://www.massimodutti.cn/itxrest/2/catalog/store/{store_id}/{cat_id}/category?languageId=-7&typeCatalog=1&appId=1",
            impersonate="chrome", timeout=20,
        )
        cats = cr.json().get("categories", [])
        men_root = [c for c in cats if any(k in str(c.get("name", "")).upper() for k in ("MEN", "男士", "男装"))]
        if not men_root:
            return []
        leaf_cats = []
        _collect_inditex_leaf_cats(men_root[0].get("subcategories", []), {}, leaf_cats)

        cat_counts = {}
        for cid, cname, mapped in leaf_cats:
            if cat_counts.get(mapped, 0) >= 2:
                continue
            cat_counts[mapped] = cat_counts.get(mapped, 0) + 1
            pr = cffi_requests.get(
                f"https://www.massimodutti.cn/itxrest/3/catalog/store/{store_id}/{cat_id}/category/{cid}/product?languageId=-7&appId=1",
                impersonate="chrome", timeout=20,
            )
            if pr.status_code != 200:
                continue
            prods = pr.json().get("products", [])
            for p in prods:
                title = p.get("name") or ""
                pid = str(p.get("id") or "")
                if not title or not pid or is_excluded(title):
                    continue
                bundle_colors = p.get("bundleProductSummaries", [{}])
                detail = (bundle_colors[0].get("detail") if bundle_colors else None) or p.get("detail") or {}
                colors = detail.get("colors", [])
                if not colors:
                    continue
                c0 = colors[0]
                sizes = c0.get("sizes", [])
                price_raw = sizes[0].get("price") if sizes else 0
                if not price_raw:
                    continue
                price = int(round(int(price_raw) / 100))

                img_obj = c0.get("image") or {}
                base_url = img_obj.get("url") or ""
                cover = f"https://static.massimodutti.net/3/photos/{base_url}_2_1_16.jpg" if base_url else ""
                model_img = f"https://static.massimodutti.net/3/photos/{base_url}_1_1_16.jpg" if base_url else ""
                if not cover:
                    continue

                comps = detail.get("composition", []) or c0.get("composition", [])
                mat_raw = " / ".join(f"{cp.get('composition', cp.get('description',''))}" for cp in comps) if comps else clean_html(p.get("longDescription") or p.get("description") or "")

                items.append({
                    "id": make_item_id("Massimo Dutti", pid, title),
                    "brand": "Massimo Dutti",
                    "category": guess_apparel_category(f"{cname} {title}"),
                    "title": title,
                    "price_cny": price,
                    "price_note": f"¥{price} (官网人民币)",
                    "material_raw": mat_raw[:220] or "Massimo Dutti 男装精选",
                    "cover_img": cover,
                    "model_img": model_img,
                    "url": f"https://www.massimodutti.cn/cn/p{pid}.html",
                    "is_shoe": False,
                })
                if len(items) >= limit:
                    return items
    except Exception as e:
        log(f"Massimo Dutti CN 抓取异常: {e}")
    return items


def _walk_for_cos_products(node, found: list):
    if isinstance(node, dict):
        # 识别 COS 商品对象特征
        if ("title" in node or "name" in node) and ("price" in node or "whitePrice" in node) and ("images" in node or "defaultImage" in node or "image" in node):
            found.append(node)
        else:
            for v in node.values():
                _walk_for_cos_products(v, found)
    elif isinstance(node, list):
        for el in node:
            _walk_for_cos_products(el, found)


def fetch_cos_hk(fx: dict, limit=15) -> list[dict]:
    items = []
    try:
        r = cffi_requests.get("https://www.cos.com/en-hk/men/view-all.html", impersonate="chrome", timeout=25)
        if r.status_code != 200:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        script = soup.find("script", id="official-site-state")
        if not script or not script.string:
            return []
        state = json.loads(script.string)
        raw_prods = []
        _walk_for_cos_products(state, raw_prods)
        seen = set()
        for p in raw_prods:
            title = str(p.get("title") or p.get("name") or "").strip()
            pid = str(p.get("code") or p.get("id") or title)
            if not title or pid in seen or is_excluded(title):
                continue
            seen.add(pid)
            price_val = p.get("whitePrice") or p.get("price")
            if isinstance(price_val, dict):
                price_val = price_val.get("value") or price_val.get("amount")
            try:
                hkd = float(re.sub(r"[^\d.]", "", str(price_val)))
            except Exception:
                continue
            if hkd <= 0:
                continue
            cny = int(round(hkd * fx["HKD_CNY"]))

            imgs = []
            if isinstance(p.get("images"), list):
                for im in p["images"]:
                    u = im.get("url") or im.get("src") if isinstance(im, dict) else str(im)
                    if u:
                        imgs.append(f"https:{u}" if u.startswith("//") else u)
            elif p.get("defaultImage") or p.get("image"):
                u = p.get("defaultImage") or p.get("image")
                if isinstance(u, dict):
                    u = u.get("url") or u.get("src")
                if u:
                    imgs.append(f"https:{u}" if str(u).startswith("//") else str(u))
            if not imgs:
                continue

            slug = p.get("url") or p.get("link") or "/en-hk/men/view-all.html"
            full_url = f"https://www.cos.com{slug}" if str(slug).startswith("/") else str(slug)
            items.append({
                "id": make_item_id("COS", pid, title),
                "brand": "COS",
                "category": guess_apparel_category(title),
                "title": title,
                "price_cny": cny,
                "price_note": f"≈¥{cny} (HK${int(hkd)})",
                "material_raw": clean_html(str(p.get("description") or p.get("material") or ""))[:220] or "COS HK 男装系列",
                "cover_img": imgs[-1] if len(imgs) > 1 else imgs[0],
                "model_img": imgs[0] if len(imgs) > 1 else "",
                "url": full_url,
                "is_shoe": False,
            })
            if len(items) >= limit:
                break
    except Exception as e:
        log(f"COS HK 抓取异常: {e}")
    return items


def fetch_shopify_apparel(brand: str, base_url: str, collection_urls: list[str], fx: dict, require_men_hint: bool, limit=15) -> list[dict]:
    items = []
    seen = set()
    for curl in collection_urls:
        try:
            r = cffi_requests.get(curl, impersonate="chrome", timeout=20)
            if r.status_code != 200:
                continue
            for p in r.json().get("products", []):
                pid = str(p.get("id"))
                title = p.get("title", "")
                tags = " ".join(p.get("tags", [])) if isinstance(p.get("tags"), list) else str(p.get("tags", ""))
                body = clean_html(p.get("body_html", ""))
                ptype = p.get("product_type", "")
                combined = f"{title} {tags} {ptype} {body}"

                if pid in seen or is_excluded(f"{title} {ptype} {tags}"):
                    continue
                if require_men_hint:
                    low_c = combined.lower()
                    if any(w in low_c for w in ["women", "woman", "ladies", "skirt", "dress", "maternity"]):
                        continue
                    if not any(m in low_c for m in ["men", "unisex", "male"]):
                        continue

                seen.add(pid)
                variants = p.get("variants", [])
                if not variants:
                    continue
                try:
                    usd = float(variants[0].get("price", 0))
                except Exception:
                    continue
                if usd <= 0:
                    continue
                cny = int(round(usd * fx["USD_CNY"]))

                imgs = [im.get("src") for im in p.get("images", []) if im.get("src")]
                if not imgs:
                    continue
                cover = imgs[1] if len(imgs) > 1 else imgs[0]
                model_img = imgs[0] if len(imgs) > 1 else ""

                items.append({
                    "id": make_item_id(brand, pid, title),
                    "brand": brand,
                    "category": guess_apparel_category(f"{title} {ptype}"),
                    "title": title,
                    "price_cny": cny,
                    "price_note": f"≈¥{cny} (${int(usd)})",
                    "material_raw": body[:220] or f"{brand} 当季男装",
                    "cover_img": cover,
                    "model_img": model_img,
                    "url": f"{base_url}/products/{p.get('handle', '')}",
                    "is_shoe": False,
                })
                if len(items) >= limit:
                    return items
        except Exception as e:
            log(f"{brand} Shopify 抓取异常 ({curl}): {e}")
    return items


def fetch_sneakers_from_boutiques(fx: dict, limit_per_brand=10) -> list[dict]:
    """从三大开放买手店批量聚合 New Balance / Adidas / Nike / Salomon 男士球鞋（严格过滤靴子）"""
    target_endpoints = [
        "https://kith.com/collections/mens-footwear/products.json?limit=250",
        "https://feature.com/collections/footwear/products.json?limit=250",
        "https://extrabutterny.com/collections/footwear/products.json?limit=250",
        "https://kith.com/collections/salomon/products.json?limit=100",
        "https://extrabutterny.com/collections/salomon/products.json?limit=100",
        "https://feature.com/collections/salomon/products.json?limit=100",
    ]
    brand_counts = {"New Balance": 0, "Adidas": 0, "Nike": 0, "Salomon": 0}
    items = []
    seen = set()

    for url in target_endpoints:
        base_url = "/".join(url.split("/")[:3])
        try:
            r = cffi_requests.get(url, impersonate="chrome", timeout=22)
            if r.status_code != 200:
                continue
            for p in r.json().get("products", []):
                vendor = (p.get("vendor") or "").strip().lower()
                title = p.get("title", "")
                ptype = p.get("product_type", "")
                tags = " ".join(p.get("tags", [])) if isinstance(p.get("tags"), list) else str(p.get("tags", ""))

                norm_brand = None
                if "new balance" in vendor:
                    norm_brand = "New Balance"
                elif "adidas" in vendor:
                    norm_brand = "Adidas"
                elif vendor in ("nike", "nike sb"):
                    norm_brand = "Nike"
                elif "salomon" in vendor:
                    norm_brand = "Salomon"
                if not norm_brand:
                    continue
                if brand_counts[norm_brand] >= limit_per_brand:
                    continue

                if is_excluded(f"{title} {ptype} {tags}"):
                    continue
                if any(k in title.lower() for k in ["wmns", "(w)", "women's", "gs)", "ps)", "td)"]):
                    continue

                dedup_key = make_item_id(norm_brand, "", title)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)

                variants = p.get("variants", [])
                if not variants:
                    continue
                try:
                    usd = float(variants[0].get("price", 0))
                except Exception:
                    continue
                if usd < 55:  # 过滤鞋带/护理剂等配件
                    continue
                cny = int(round(usd * fx["USD_CNY"]))

                imgs = [im.get("src") for im in p.get("images", []) if im.get("src")]
                if not imgs:
                    continue
                body = clean_html(p.get("body_html", ""))

                brand_counts[norm_brand] += 1
                items.append({
                    "id": dedup_key,
                    "brand": norm_brand,
                    "category": "精选鞋履",
                    "title": title,
                    "price_cny": cny,
                    "price_note": f"≈¥{cny} (${int(usd)})",
                    "material_raw": body[:220] or f"{norm_brand} 精选男鞋",
                    "cover_img": imgs[0],
                    "model_img": imgs[1] if len(imgs) > 1 else "",
                    "url": f"{base_url}/products/{p.get('handle', '')}",
                    "is_shoe": True,
                })
        except Exception as e:
            log(f"买手店鞋履抓取异常 ({url}): {e}")

    log(f"鞋履聚合完成: {brand_counts}")
    return items


# ================= 3. 本地规则粗筛 =================

def local_prefilter(candidates: list[dict]) -> list[dict]:
    kept = []
    for it in candidates:
        mat = it.get("material_raw", "").lower()
        price = it.get("price_cny", 0)
        if not it.get("is_shoe"):
            # 拦截高价高化纤刺客（聚酯纤维/腈纶 >= 75% 且超过 ¥399 且非功能冲锋衣）
            m_poly = re.search(r"(\d{2,3})%\s*(polyester|聚酯纤维|涤纶|acrylic|腈纶)", mat)
            if m_poly and int(m_poly.group(1)) >= 75 and price >= 399:
                if not any(k in it["title"].lower() for k in ["gore-tex", "waterproof", "shell", "防风", "防水", "冲锋"]):
                    continue
        kept.append(it)
    return kept


# ================= 4. 图文 Batching + 6 模型轮询池 =================

def download_thumb_b64(url: str) -> str | None:
    if not url:
        return None
    try:
        r = cffi_requests.get(url, impersonate="chrome", timeout=15)
        if r.status_code == 200 and len(r.content) > 800:
            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            img.thumbnail((420, 420))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=78)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception:
        return None
    return None


def extract_json_array(text: str) -> list[dict]:
    text = text.strip()
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        return json.loads(m.group(0))
    return json.loads(text)


def call_ai_batch(batch: list[dict], weather: dict) -> tuple[list[dict], str]:
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()

    # 准备缩略图
    thumbs = [download_thumb_b64(it["cover_img"]) for it in batch]

    items_meta = []
    for idx, it in enumerate(batch):
        items_meta.append({
            "idx": idx,
            "id": it["id"],
            "type": "鞋履" if it["is_shoe"] else "男装",
            "brand": it["brand"],
            "category": it["category"],
            "title": it["title"],
            "price_cny": it["price_cny"],
            "material_raw": it["material_raw"],
        })

    prompt_text = f"""你是一位理性、客观、眼光毒辣的男装与潮流鞋履买手顾问。
请按顺序审阅我提供的 {len(batch)} 件单品图片与参数（图片顺序与 idx 0..{len(batch)-1} 一一对应）：

【严格客观评分规则（满分10分，精确到0.1，严禁受当前天气温度影响！）】
- 若为【男装】：只考察「材质价值比(55%)：天然材质(羊毛/羊绒/丝/麻/高支纯棉)占比与人民币定价是否匹配，高价纯化纤给低分」+「视觉版型与色彩(45%)：肩线廓形、垂坠质感与高级配色」。
- 若为【鞋履】：只考察「鞋型与配色视觉(55%)：鞋楦比例、拼接层次、日常Clean Fit/机能百搭度」+「鞋面材质与工艺(45%)：麂皮/头层皮/Gore-Tex/Vibram等用料，大面积廉价合成革给低分」。
- 只有真正值得入手的单品才能给 >= 8.0 分；平庸或高价低质款请客观打 6.0~7.8 分。

【上海本周气象（仅用于写穿搭建议与判断是否“上海适穿”，绝不扣减评分！）】
{weather['summary']}；穿衣参考：{weather['dress_hint']}

【待评审单品列表】
{json.dumps(items_meta, ensure_ascii=False)}

请严格输出一个 JSON 数组，每个元素字段如下（不要输出任何额外解释）：
[
  {{
    "idx": 0,
    "title_cn": "简练准确的中文品名(不超过22字)",
    "score": 8.6,
    "style_tags": ["从 {VALID_STYLES} 中选1~2个"],
    "metric_tags": ["从 {VALID_METRICS} 中选符合项(如天然面料/上海适穿/防水耐造/高性价比)"],
    "material_note": "客观材质/鞋面工艺解析(30~50字)",
    "visual_note": "客观版型/鞋型与配色视觉评价(30~50字)",
    "shanghai_advice": "结合本周上海气温({weather['today_min']}~{weather['today_max']}℃)与降水的具体穿搭建议(25~45字)"
  }}
]"""

    # 1. 优先轮询 Gemini 多模态视觉池
    if gemini_key:
        headers = {"Authorization": f"Bearer {gemini_key}", "Content-Type": "application/json"}
        content_parts = [{"type": "text", "text": prompt_text}]
        for idx, b64 in enumerate(thumbs):
            if b64:
                content_parts.append({"type": "text", "text": f"[单品 idx={idx} 图片]"})
                content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

        for cfg in VISION_POOL:
            payload = {
                "model": cfg["model"],
                "messages": [{"role": "user", "content": content_parts}],
                "max_tokens": 4096,
            }
            if cfg["reasoning"]:
                payload["reasoning_effort"] = cfg["reasoning"]

            try:
                r = requests.post(GEMINI_ENDPOINT, headers=headers, json=payload, timeout=cfg["timeout"])
                if r.status_code == 200:
                    msg = r.json()["choices"][0]["message"]
                    raw_content = msg.get("content") or ""
                    if raw_content:
                        parsed = extract_json_array(raw_content)
                        return parsed, cfg["model"]
                else:
                    log(f"模型 {cfg['model']} 返回 {r.status_code}，自动切换下一顺位...")
            except Exception as e:
                log(f"模型 {cfg['model']} 异常 ({e})，自动切换下一顺位...")
            time.sleep(2.5)

    # 2. 应急兜底：Groq 纯文本模型（自动剥离图片）
    if groq_key:
        headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}
        for cfg in GROQ_POOL:
            payload = {
                "model": cfg["model"],
                "messages": [{"role": "user", "content": prompt_text}],
                "temperature": 0.2,
                "max_tokens": 4096,
            }
            try:
                r = requests.post(GROQ_ENDPOINT, headers=headers, json=payload, timeout=cfg["timeout"])
                if r.status_code == 200:
                    raw_content = r.json()["choices"][0]["message"].get("content") or ""
                    if raw_content:
                        parsed = extract_json_array(raw_content)
                        return parsed, f"{cfg['model']} (纯文本兜底)"
            except Exception as e:
                log(f"Groq 模型 {cfg['model']} 异常: {e}")
            time.sleep(2.0)

    return [], "NONE"


def refresh_cached_weather_advice(cached_items: list[dict], weather: dict):
    """用极速免费 Groq 为历史保留单品刷新本周上海穿搭建议与'上海适穿'标签"""
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if not groq_key or not cached_items:
        return
    headers = {"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"}

    # 分批每30条刷新一次
    for start in range(0, len(cached_items), 30):
        chunk = cached_items[start:start + 30]
        mini = [{"id": x["id"], "title": x.get("title_cn") or x["title"], "cat": x["category"], "mat": x.get("material_note", "")} for x in chunk]
        prompt = (
            f"上海本周气象：{weather['summary']}，参考：{weather['dress_hint']}。\n"
            f"请根据每件单品的厚度/品类，判断它是否适合本周上海气温直接穿出门（suit_now: true/false），并更新20~35字客观上海穿搭建议。\n"
            f"输入：{json.dumps(mini, ensure_ascii=False)}\n"
            f"仅返回JSON数组：[ {{\"id\":\"...\", \"suit_now\": true, \"shanghai_advice\":\"...\"}} ]"
        )
        for cfg in GROQ_POOL:
            try:
                r = requests.post(
                    GROQ_ENDPOINT,
                    headers=headers,
                    json={"model": cfg["model"], "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 3000},
                    timeout=35,
                )
                if r.status_code == 200:
                    res = extract_json_array(r.json()["choices"][0]["message"]["content"])
                    rmap = {x["id"]: x for x in res if isinstance(x, dict) and "id" in x}
                    for it in chunk:
                        if it["id"] in rmap:
                            upd = rmap[it["id"]]
                            it["shanghai_advice"] = upd.get("shanghai_advice", it.get("shanghai_advice", ""))
                            mtags = [t for t in it.get("metric_tags", []) if t != "上海适穿"]
                            if upd.get("suit_now"):
                                mtags.append("上海适穿")
                            it["metric_tags"] = mtags
                    break
            except Exception:
                continue
        time.sleep(1.5)


def main():
    log(f"=== 开始更新上海男装与鞋履精选库 | {NOW_STR} ===")
    weather = fetch_shanghai_weather()
    fx = fetch_fx_rates()
    log(f"上海气象: {weather['summary']} | 汇率: 1 USD = ¥{fx['USD_CNY']}, 1 HKD = ¥{fx['HKD_CNY']}")

    # 读取已有缓存
    existing_items = []
    if DATA_PATH.exists():
        try:
            old_data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            existing_items = old_data.get("items", [])
        except Exception:
            existing_items = []
    existing_ids = {x["id"] for x in existing_items}

    # 刷新已有单品的本周上海天气穿搭建议
    if existing_items:
        log(f"正在用 Groq 刷新 {len(existing_items)} 件历史精品的本周上海穿搭建议...")
        refresh_cached_weather_advice(existing_items, weather)

    # 抓取全量10品牌新品
    all_raw = []
    all_raw.extend(fetch_uniqlo_cn(limit=18))
    all_raw.extend(fetch_zara_cn(limit=18))
    all_raw.extend(fetch_massimo_dutti_cn(limit=15))
    all_raw.extend(fetch_cos_hk(fx, limit=15))
    all_raw.extend(fetch_shopify_apparel(
        "UR", "https://global.urbanrevivo.com",
        ["https://global.urbanrevivo.com/collections/best-sellers-men-nv030700/products.json?limit=40"],
        fx, require_men_hint=False, limit=15,
    ))
    all_raw.extend(fetch_shopify_apparel(
        "MUJI", "https://www.muji.us",
        ["https://www.muji.us/collections/apparel/products.json?limit=120"],
        fx, require_men_hint=True, limit=15,
    ))
    all_raw.extend(fetch_sneakers_from_boutiques(fx, limit_per_brand=10))

    log(f"全平台共抓取原始候选 {len(all_raw)} 件，过滤已入库与化纤刺客...")
    new_candidates = [x for x in local_prefilter(all_raw) if x["id"] not in existing_ids]
    # 均衡截取不同品牌，避免单一品牌占满单次AI额度
    by_brand = {}
    for c in new_candidates:
        by_brand.setdefault(c["brand"], []).append(c)
    balanced = []
    while len(balanced) < MAX_NEW_AI_ITEMS_PER_RUN and any(by_brand.values()):
        for b in list(by_brand.keys()):
            if by_brand[b]:
                balanced.append(by_brand[b].pop(0))
                if len(balanced) >= MAX_NEW_AI_ITEMS_PER_RUN:
                    break

    log(f"本轮送审 AI 买手队列: {len(balanced)} 件 (每批 {BATCH_SIZE} 件)")
    newly_kept = []
    used_models = set()

    for i in range(0, len(balanced), BATCH_SIZE):
        batch = balanced[i:i + BATCH_SIZE]
        evals, model_used = call_ai_batch(batch, weather)
        if model_used != "NONE":
            used_models.add(model_used)
        emap = {e.get("idx"): e for e in evals if isinstance(e, dict)}
        for idx, item in enumerate(batch):
            ev = emap.get(idx)
            if not ev:
                continue
            try:
                score = round(float(ev.get("score", 0)), 1)
            except Exception:
                continue
            if score < 8.0:
                continue
            item["title_cn"] = ev.get("title_cn") or item["title"]
            item["score"] = min(10.0, score)
            item["style_tags"] = [t for t in ev.get("style_tags", []) if t in VALID_STYLES][:2] or ["质感基础"]
            item["metric_tags"] = [t for t in ev.get("metric_tags", []) if t in VALID_METRICS]
            item["material_note"] = ev.get("material_note", "")
            item["visual_note"] = ev.get("visual_note", "")
            item["shanghai_advice"] = ev.get("shanghai_advice", "")
            item["ai_model"] = model_used
            item["added_at"] = NOW_STR
            newly_kept.append(item)
        time.sleep(3.5)

    log(f"本轮 AI 严选 (>=8.0分) 新增入库: {len(newly_kept)} 件")

    merged = newly_kept + existing_items
    # 按更新时间/评分排序并限制总库容
    final_items = merged[:MAX_STORED_ITEMS]

    out_payload = {
        "updated_at": NOW_STR,
        "weather": weather,
        "fx": fx,
        "models_used": sorted(used_models) or ["cache_only"],
        "total_count": len(final_items),
        "items": final_items,
    }
    DATA_PATH.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已写入 {DATA_PATH}，当前总计精选单品 {len(final_items)} 件。")


if __name__ == "__main__":
    main()
