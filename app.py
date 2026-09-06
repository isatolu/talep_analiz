"""
Net Talep Farkı ile Sentetik Grafik Oluşturucu - Aşama 1
----------------------------------------------------------
Kullanıcı farklı "net talep" çizgilerini (akış / flow) fare ile çizer.
Her çizgi ADD ile eklenir. GRAPH butonuna basıldığında:
  - Tüm çizgilerin bar bazlı toplamı alınır (toplam net akış)
  - Bu akış bir önceki kapanışa eklenerek kümülatif fiyat (close) bulunur
  - Hacim = o bardaki tüm çizgilerin mutlak değerlerinin toplamı
  - Fitiller (kozmetik) hacme oranla uzar/kısalır
  - Sonuç mum grafik + hacim barları olarak çizilir
"""

import numpy as np
import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit_drawable_canvas import st_canvas

# ----------------------------------------------------------------------
# Sabitler
# ----------------------------------------------------------------------
BAR_PX = 10  # her bar'ın canvas üzerindeki piksel genişliği
CANVAS_HEIGHT = 320
STROKE_COLOR = "#1f2937"  # çizim rengi (koyu lacivert) - arka plandan ayırt edilebilmesi için
DARKNESS_THRESHOLD = 400  # R+G+B toplamı bu değerin altındaysa "çizilmiş" say
LINE_PALETTE = [
    "#2563eb", "#f97316", "#16a34a", "#dc2626", "#9333ea",
    "#0891b2", "#ca8a04", "#db2777", "#059669", "#4338ca",
]

st.set_page_config(page_title="Net Talep Farkı Simülatörü", layout="wide")

# ----------------------------------------------------------------------
# Session state başlangıç değerleri
# ----------------------------------------------------------------------
if "total_bars" not in st.session_state:
    st.session_state.total_bars = 100
if "lines" not in st.session_state:
    st.session_state.lines = []  # her biri: {"id", "name", "color", "values": np.array(total_bars)}
if "canvas_version" not in st.session_state:
    st.session_state.canvas_version = 0
if "next_line_id" not in st.session_state:
    st.session_state.next_line_id = 1
if "result" not in st.session_state:
    st.session_state.result = None  # GRAPH sonucu burada tutulur


def resize_lines(new_total):
    """Toplam bar sayısı değiştiğinde mevcut çizgilerin dizilerini uyumlu hale getirir."""
    old_total = len(st.session_state.lines[0]["values"]) if st.session_state.lines else new_total
    if old_total == new_total:
        return
    for line in st.session_state.lines:
        vals = line["values"]
        if new_total > len(vals):
            pad = np.full(new_total - len(vals), np.nan)
            line["values"] = np.concatenate([vals, pad])
        else:
            line["values"] = vals[:new_total]


# ----------------------------------------------------------------------
# Kenar çubuğu (sidebar) - genel ayarlar
# ----------------------------------------------------------------------
with st.sidebar:
    st.header("Ayarlar")

    new_total_bars = st.number_input(
        "Toplam bar sayısı", min_value=20, max_value=1000,
        value=st.session_state.total_bars, step=10,
    )
    if new_total_bars != st.session_state.total_bars:
        resize_lines(new_total_bars)
        st.session_state.total_bars = new_total_bars

    window_size = st.slider(
        "Görünen pencere genişliği (bar)", min_value=20, max_value=100,
        value=min(50, st.session_state.total_bars), step=5,
    )
    window_size = min(window_size, st.session_state.total_bars)

    max_start = max(0, st.session_state.total_bars - window_size)
    window_start = st.slider(
        "Pencere başlangıcı (kaydırma)", min_value=0, max_value=max_start,
        value=0, step=1,
    )

    y_max = st.slider("Değer aralığı (Y ekseni, +/-)", min_value=1, max_value=50, value=10)

    st.markdown("---")
    base_price = st.number_input("Başlangıç fiyatı", min_value=1.0, value=100.0, step=1.0)
    wick_strength = st.slider("Fitil şiddeti (kozmetik)", min_value=0.0, max_value=5.0, value=1.0, step=0.1)

    st.markdown("---")
    if st.button("🗑 Tüm çizgileri temizle"):
        st.session_state.lines = []
        st.session_state.result = None
        st.session_state.canvas_version += 1
        st.rerun()


# ----------------------------------------------------------------------
# Arka plan görseli (grid + sıfır çizgisi) oluştur
# ----------------------------------------------------------------------
def make_background(window_size, window_start, total_bars, canvas_height):
    width = window_size * BAR_PX
    img = Image.new("RGB", (width, canvas_height), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    for j in range(window_size + 1):
        x = j * BAR_PX
        bar_idx = window_start + j
        color = (225, 225, 225) if bar_idx % 10 == 0 else (245, 245, 245)
        draw.line([(x, 0), (x, canvas_height)], fill=color, width=1)

    # sıfır çizgisi
    mid_y = canvas_height // 2
    draw.line([(0, mid_y), (width, mid_y)], fill=(190, 190, 190), width=1)

    # her 10 barda bir etiket
    for j in range(0, window_size, 10):
        bar_idx = window_start + j
        draw.text((j * BAR_PX + 2, 2), str(bar_idx), fill=(160, 160, 160))

    return img


# ----------------------------------------------------------------------
# Canvas'tan çizilen tek çizgiyi bar başına değerlere çevir
# ----------------------------------------------------------------------
def extract_stroke_values(image_data, window_size, canvas_height, y_max):
    arr = np.array(image_data)[:, :, :3].astype(int)
    darkness = arr.sum(axis=2)
    drawn_mask = darkness < DARKNESS_THRESHOLD

    values = np.full(window_size, np.nan)
    for j in range(window_size):
        x0, x1 = j * BAR_PX, (j + 1) * BAR_PX
        rows, _ = np.where(drawn_mask[:, x0:x1])
        if len(rows) > 0:
            y_mean = rows.mean()
            values[j] = ((canvas_height / 2 - y_mean) / (canvas_height / 2)) * y_max
    return values


# ----------------------------------------------------------------------
# Ana sayfa
# ----------------------------------------------------------------------
st.title("Net Talep Farkı ile Sentetik Grafik Oluşturucu")

with st.expander("Nasıl kullanılır?", expanded=False):
    st.markdown(
        """
1. Aşağıdaki tuvale fare ile istediğin şekilde bir çizgi çiz (yukarı = pozitif net talep, aşağı = negatif).
2. **Çizgiyi Ekle (ADD)** butonuna bas — çizgi listeye eklenir, tuval temizlenir.
3. İstediğin kadar çizgi ekle (vade ayrımı yapmana gerek yok, hepsi toplanacak).
4. Beğenmediğin bir çizgiyi listeden **Kaldır** ile silebilirsin.
5. Hazır olduğunda **GRAPH** butonuna bas — sonucu en altta göreceksin.
        """
    )

col_canvas, col_legend = st.columns([3, 1])

with col_canvas:
    bg_image = make_background(window_size, window_start, st.session_state.total_bars, CANVAS_HEIGHT)
    canvas_key = f"canvas_{st.session_state.canvas_version}_{window_start}_{window_size}"

    canvas_result = st_canvas(
        fill_color="rgba(255,255,255,0)",
        stroke_width=3,
        stroke_color=STROKE_COLOR,
        background_color="#FFFFFF",
        background_image=bg_image,
        update_streamlit=True,
        height=CANVAS_HEIGHT,
        width=window_size * BAR_PX,
        drawing_mode="freedraw",
        key=canvas_key,
    )

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        add_clicked = st.button("➕ Çizgiyi Ekle (ADD)", use_container_width=True)
    with btn_col2:
        graph_clicked = st.button("📊 GRAPH", type="primary", use_container_width=True)

with col_legend:
    st.subheader("Çizgiler")
    if not st.session_state.lines:
        st.caption("Henüz çizgi eklenmedi.")
    for line in list(st.session_state.lines):
        c1, c2 = st.columns([3, 1])
        with c1:
            st.markdown(
                f'<span style="display:inline-block;width:12px;height:12px;'
                f'background-color:{line["color"]};border-radius:2px;"></span> {line["name"]}',
                unsafe_allow_html=True,
            )
        with c2:
            if st.button("Kaldır", key=f"remove_{line['id']}"):
                st.session_state.lines = [l for l in st.session_state.lines if l["id"] != line["id"]]
                st.rerun()

# ----------------------------------------------------------------------
# ADD işlemi
# ----------------------------------------------------------------------
if add_clicked:
    if canvas_result.image_data is None:
        st.warning("Önce tuvale bir çizgi çiz.")
    else:
        stroke_values = extract_stroke_values(canvas_result.image_data, window_size, CANVAS_HEIGHT, y_max)
        if np.all(np.isnan(stroke_values)):
            st.warning("Çizim algılanamadı, tekrar dener misin?")
        else:
            full_values = np.full(st.session_state.total_bars, np.nan)
            full_values[window_start:window_start + window_size] = stroke_values

            new_id = st.session_state.next_line_id
            st.session_state.next_line_id += 1
            color = LINE_PALETTE[(new_id - 1) % len(LINE_PALETTE)]

            st.session_state.lines.append({
                "id": new_id,
                "name": f"Line {new_id}",
                "color": color,
                "values": full_values,
            })
            st.session_state.canvas_version += 1
            st.rerun()


# ----------------------------------------------------------------------
# GRAPH işlemi
# ----------------------------------------------------------------------
def build_ohlcv(flow, volume, base_price, wick_strength, y_max):
    n = len(flow)
    open_ = np.zeros(n)
    close = np.zeros(n)

    prev_close = base_price
    for i in range(n):
        open_[i] = prev_close
        close[i] = prev_close + flow[i]
        prev_close = close[i]

    avg_vol = volume.mean() if volume.mean() > 0 else 1.0
    unit = y_max * 0.05  # taban fitil birimi (kozmetik)
    rng = np.random.default_rng(42)

    high = np.zeros(n)
    low = np.zeros(n)
    for i in range(n):
        body_top = max(open_[i], close[i])
        body_bot = min(open_[i], close[i])
        rel_vol = volume[i] / avg_vol
        wick_total = wick_strength * unit * rel_vol
        split = rng.uniform(0.3, 0.7)
        high[i] = body_top + wick_total * split
        low[i] = body_bot - wick_total * (1 - split)

    return open_, high, low, close


if graph_clicked:
    n = st.session_state.total_bars
    if not st.session_state.lines:
        st.warning("Grafik oluşturmak için en az bir çizgi eklemelisin.")
    else:
        stacked = np.stack([l["values"] for l in st.session_state.lines])
        flow_total = np.nansum(stacked, axis=0)
        volume = np.nansum(np.abs(stacked), axis=0)

        open_, high, low, close = build_ohlcv(flow_total, volume, base_price, wick_strength, y_max)

        df = pd.DataFrame({
            "bar": np.arange(1, n + 1),
            "Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume,
        })
        st.session_state.result = df

if st.session_state.result is not None:
    df = st.session_state.result
    st.subheader("Sonuç")

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.72, 0.28], vertical_spacing=0.03,
    )
    fig.add_trace(
        go.Candlestick(
            x=df["bar"], open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
            name="Fiyat", increasing_line_color="#16a34a", decreasing_line_color="#dc2626",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Bar(x=df["bar"], y=df["Volume"], name="Hacim", marker_color="rgba(37,99,235,0.5)"),
        row=2, col=1,
    )
    fig.update_layout(
        height=650, xaxis_rangeslider_visible=False, showlegend=False,
        margin=dict(t=20, b=20, l=20, r=20),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.download_button(
        "OHLCV verisini CSV olarak indir",
        df.to_csv(index=False).encode("utf-8"),
        file_name="sentetik_ohlcv.csv",
        mime="text/csv",
    )
