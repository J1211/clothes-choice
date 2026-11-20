import json
import hashlib
from pathlib import Path
import streamlit as st

st.set_page_config(
    page_title="SHAI · 上海男装与鞋履买手库",
    page_icon="🧥",
    layout="wide",
    initial_sidebar_state="expanded",
)

DATA_PATH = Path("data/items.json")

CATEGORIES = ["全部单品", "外套大衣", "毛衣针织", "衬衫卫衣", "休闲裤装", "精选鞋履"]
STYLE_TAGS = ["老钱静奢", "极简通勤", "松弛街头", "都市机能", "复古运动", "质感基础"]
METRIC_TAGS = ["天然面料", "上海适穿", "防水耐造", "高性价比"]
ALL_BRANDS = [
    "Uniqlo", "Zara", "Massimo Dutti", "COS", "UR", "MUJI",
    "New Balance", "Adidas", "Nike", "Salomon",
]

# 全局深色审美 CSS + Streamlit 顶部遮挡修复 + 手机端方框2~3列并排修复
st.markdown(
    """
<style>
:root {
    --bg-main: #0b0e14;
    --bg-card: #141923;
    --border-subtle: #242c3d;
    --text-primary: #f1f5f9;
    --text-muted: #94a3b8;
    --accent: #38bdf8;
}
html, body, [data-testid="stAppViewContainer"] {
    background-color: var(--bg-main);
    color: var(--text-primary);
}
.block-container, div[data-testid="stAppViewBlockContainer"] {
    padding-top: 4.2rem !important;
    padding-bottom: 2.5rem !important;
    max-width: 1400px;
}

/* 左侧边栏紧凑深色样式 */
section[data-testid="stSidebar"] {
    background-color: #0f131c;
    border-right: 1px solid var(--border-subtle);
}

/* 方框按钮紧致化 */
div.stButton > button {
    border-radius: 6px !important;
    font-size: 0.86rem !important;
    font-weight: 500 !important;
    padding: 0.42rem 0.35rem !important;
    transition: all 0.15s ease;
    white-space: nowrap !important;
}
div.stButton > button[kind="secondary"] {
    background-color: #161c28 !important;
    border: 1px solid #283144 !important;
    color: #cbd5e1 !important;
}
div.stButton > button[kind="secondary"]:hover {
    border-color: #475569 !important;
    color: #ffffff !important;
}
div.stButton > button[kind="primary"] {
    background-color: #1d4ed8 !important;
    border: 1px solid #3b82f6 !important;
    color: #ffffff !important;
}

/* Lookbook 卡片容器 */
.look-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    padding: 0.75rem;
    margin-bottom: 0.5rem;
}
.look-meta-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 0.45rem;
    font-size: 0.8rem;
}
.brand-badge {
    background: #1e293b;
    color: #cbd5e1;
    padding: 2px 7px;
    border-radius: 4px;
    font-weight: 600;
    letter-spacing: 0.02em;
}
.score-badge {
    background: rgba(16, 185, 129, 0.15);
    color: #34d399;
    border: 1px solid rgba(52, 211, 153, 0.35);
    padding: 2px 7px;
    border-radius: 4px;
    font-weight: 700;
}
.look-title {
    font-size: 0.94rem;
    font-weight: 600;
    color: #f8fafc;
    margin: 0.38rem 0 0.2rem 0;
    line-height: 1.35;
    min-height: 2.55rem;
}
.look-price {
    font-size: 0.88rem;
    color: #e2e8f0;
    font-weight: 600;
    margin-bottom: 0.35rem;
}
.tag-row {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-bottom: 0.25rem;
}
.pill-style {
    font-size: 0.72rem;
    background: rgba(56, 189, 248, 0.12);
    color: #7dd3fc;
    padding: 1px 6px;
    border-radius: 4px;
}
.pill-metric {
    font-size: 0.72rem;
    background: rgba(251, 191, 36, 0.12);
    color: #fcd34d;
    padding: 1px 6px;
    border-radius: 4px;
}

/* 手机端响应式：确保右侧四字方框每行保持2~3个并排，绝不退化成单列叠罗汉 */
@media (max-width: 640px) {
    .block-container, div[data-testid="stAppViewBlockContainer"] {
        padding-top: 3.8rem !important;
        padding-left: 0.8rem !important;
        padding-right: 0.8rem !important;
    }
    div[data-testid="stVerticalBlock"]:has(> div.element-container .filter-matrix-anchor) div[data-testid="stHorizontalBlock"] {
        flex-direction: row !important;
        flex-wrap: wrap !important;
        gap: 0.35rem !important;
    }
    div[data-testid="stVerticalBlock"]:has(> div.element-container .filter-matrix-anchor) div[data-testid="stHorizontalBlock"] > div[data-testid="column"],
    div[data-testid="stVerticalBlock"]:has(> div.element-container .filter-matrix-anchor) div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"] {
        flex: 1 1 31% !important;
        min-width: 31% !important;
        max-width: 48.5% !important;
        width: auto !important;
    }
}
</style>
""",
    unsafe_allow_html=True,
)


def make_expander_label(base: str, item_id: str, view_sig: str) -> str:
    """用零宽字符签名彻底隔绝切换分类时的 st.expander 状态串位"""
    digest = hashlib.md5(f"{item_id}|{view_sig}".encode()).hexdigest()[:8]
    bits = bin(int(digest, 16))[2:].zfill(32)
    zw = "".join("\u200b" if b == "0" else "\u200c" for b in bits[:14])
    return f"{base}{zw}"


@st.cache_data(ttl=60)
def load_catalog() -> dict:
    if not DATA_PATH.exists():
        return {}
    try:
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


# 初始化 Session State
if "selected_cat" not in st.session_state:
    st.session_state.selected_cat = "全部单品"
if "selected_styles" not in st.session_state:
    st.session_state.selected_styles = set()
if "selected_metrics" not in st.session_state:
    st.session_state.selected_metrics = set()


catalog = load_catalog()
items = catalog.get("items", [])
weather = catalog.get("weather", {})

# ================= 左侧固定边栏 =================
with st.sidebar:
    st.markdown("### 🧭 上海男装买手")
    if weather:
        st.info(
            f"**上海气象：** {weather.get('summary', '')}\n\n"
            f"**参考：** {weather.get('dress_hint', '')}"
        )

    st.markdown("#### 品类导航")
    for i in range(0, len(CATEGORIES), 2):
        cols = st.columns(2)
        for j, cat_name in enumerate(CATEGORIES[i:i + 2]):
            active = st.session_state.selected_cat == cat_name
            if cols[j].button(
                cat_name,
                key=f"sb_cat_{cat_name}",
                type="primary" if active else "secondary",
                use_container_width=True,
            ):
                st.session_state.selected_cat = cat_name
                st.rerun()

    st.markdown("---")
    min_score = st.slider("AI 买手评分门槛", min_value=8.0, max_value=10.0, value=8.0, step=0.1)
    selected_brands = st.multiselect("品牌范围", options=ALL_BRANDS, default=ALL_BRANDS)
    search_kw = st.text_input("关键词检索", placeholder="羊毛 / 麂皮 / GTX / 廓形...")

    st.markdown("---")
    st.caption(
        f"**运行状态**\n"
        f"- 更新时间：{catalog.get('updated_at', '未初始化')}\n"
        f"- 在库精品：{len(items)} 件\n"
        f"- 评审模型：{', '.join(catalog.get('models_used', [])) or '-'}"
    )

# ================= 右侧主区域：四字对称筛选方框 =================
with st.container():
    st.markdown('<div class="filter-matrix-anchor"></div>', unsafe_allow_html=True)

    # 第一排：6个风格四字方框（无多余标题、无help气泡）
    style_cols = st.columns(6)
    for idx, tag in enumerate(STYLE_TAGS):
        is_on = tag in st.session_state.selected_styles
        if style_cols[idx].button(
            tag,
            key=f"tag_style_{tag}",
            type="primary" if is_on else "secondary",
            use_container_width=True,
        ):
            if is_on:
                st.session_state.selected_styles.remove(tag)
            else:
                st.session_state.selected_styles.add(tag)
            st.rerun()

    # 第二排：4个买手硬指标四字方框
    metric_cols = st.columns(4)
    for idx, tag in enumerate(METRIC_TAGS):
        is_on = tag in st.session_state.selected_metrics
        if metric_cols[idx].button(
            tag,
            key=f"tag_metric_{tag}",
            type="primary" if is_on else "secondary",
            use_container_width=True,
        ):
            if is_on:
                st.session_state.selected_metrics.remove(tag)
            else:
                st.session_state.selected_metrics.add(tag)
            st.rerun()

st.markdown("<div style='height: 0.6rem;'></div>", unsafe_allow_html=True)

if not items:
    st.warning("尚未生成 `data/items.json`。请先前往 GitHub Actions 手动运行一次 **Daily Wardrobe & Sneaker AI Update**。")
    st.stop()

# ================= 过滤逻辑 =================
filtered = []
kw_low = search_kw.strip().lower()

for it in items:
    if st.session_state.selected_cat != "全部单品" and it.get("category") != st.session_state.selected_cat:
        continue
    if it.get("brand") not in selected_brands:
        continue
    if float(it.get("score", 0)) < min_score:
        continue
    if st.session_state.selected_styles:
        if not st.session_state.selected_styles.intersection(set(it.get("style_tags", []))):
            continue
    if st.session_state.selected_metrics:
        if not st.session_state.selected_metrics.issubset(set(it.get("metric_tags", []))):
            continue
    if kw_low:
        blob = " ".join([
            it.get("title_cn", ""), it.get("title", ""), it.get("brand", ""),
            it.get("material_note", ""), it.get("visual_note", ""), it.get("material_raw", ""),
        ]).lower()
        if kw_low not in blob:
            continue
    filtered.append(it)

# 默认按评分降序排列
filtered.sort(key=lambda x: float(x.get("score", 0)), reverse=True)

view_sig = f"{st.session_state.selected_cat}|{sorted(st.session_state.selected_styles)}|{sorted(st.session_state.selected_metrics)}|{min_score}|{kw_low}"

if not filtered:
    st.info("当前筛选组合下暂无匹配单品，可尝试放宽右侧方框或降低评分门槛。")
else:
    # 3列 Lookbook 画廊网格
    cols_per_row = 3
    for row_start in range(0, len(filtered), cols_per_row):
        row_items = filtered[row_start:row_start + cols_per_row]
        grid_cols = st.columns(cols_per_row)
        for col_idx, item in enumerate(row_items):
            with grid_cols[col_idx]:
                st.image(item["cover_img"], use_container_width=True)

                style_pills = "".join(f"<span class='pill-style'>{t}</span>" for t in item.get("style_tags", []))
                metric_pills = "".join(f"<span class='pill-metric'>{t}</span>" for t in item.get("metric_tags", []))
                title_show = item.get("title_cn") or item.get("title")

                st.markdown(
                    f"""
<div class="look-card">
  <div class="look-meta-row">
    <span class="brand-badge">{item['brand']}</span>
    <span class="score-badge">⭐ {item['score']:.1f}</span>
  </div>
  <div class="look-title">{title_show}</div>
  <div class="look-price">{item.get('price_note', f"¥{item.get('price_cny', 0)}")}</div>
  <div class="tag-row">{style_pills}{metric_pills}</div>
</div>
""",
                    unsafe_allow_html=True,
                )

                exp_label = make_expander_label("查看上身图与买手解析", item["id"], view_sig)
                with st.expander(exp_label, expanded=False):
                    if item.get("model_img"):
                        st.image(item["model_img"], caption="上身 / 多角度视图", use_container_width=True)
                    st.markdown(f"**材质与工艺：** {item.get('material_note', '-')}")
                    st.markdown(f"**视觉与版型：** {item.get('visual_note', '-')}")
                    st.markdown(f"**上海穿搭建议：** {item.get('shanghai_advice', '-')}")
                    if item.get("material_raw"):
                        st.caption(f"原始成分/参数：{item['material_raw']}")
                    st.caption(f"入库时间：{item.get('added_at', '-')} · 评审模型：{item.get('ai_model', '-')}")
                    st.link_button("前往商品页面", item["url"], use_container_width=True)
