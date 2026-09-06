"""
Net Talep Farkı ile Sentetik Grafik Oluşturucu - Aşama 1 (v2)
----------------------------------------------------------------
v1'e göre değişenler:
  - Çizim alanı büyütüldü
  - Sıfır çizgisi / değer ekseni artık canvas'ın kendi arka plan resmine
    bağımlı değil; yan tarafta garanti şekilde HTML/CSS ile gösteriliyor
    (bazı tarayıcı/versiyon kombinasyonlarında canvas'ın background_image
    özelliği görünmeyebiliyor, bu yüzden ayrı ve güvenilir bir gösterim ekledik)
  - Varsayılan toplam bar sayısı 500'e çıkarıldı
  - ADD / REMOVE sonrası grafik artık otomatik güncelleniyor, GRAPH butonuna
    basmaya gerek yok
  - Çizgi kapsamayan (veri olmayan) bölgeler grafikte gölgeli gösteriliyor
  - Beklenmeyen hatalar artık sayfayı tamamen çökertmiyor, okunabilir bir
    mesaj gösteriliyor
"""

import traceback

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit_drawable_canvas import st_canvas

# ----------------------------------------------------------------------
# Sabitler
# ----------------------------------------------------------------------
CANVAS_WIDTH = 1400     # sabit çizim/grafik genişliği (px) - artık genişlemiyor
CANVAS_HEIGHT = 420
LINE_PALETTE = [
    "#2563eb", "#f97316", "#16a34a", "#dc2626", "#9333ea",
    "#0891b2", "#ca8a04", "#db2777", "#059669", "#4338ca",
]

st.set_page_config(page_title="Net Talep Farkı Simülatörü", layout="wide")

# ----------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------
defaults = {
    "total_bars": 150,
    "lines": [],
    "canvas_version": 0,
    "next_line_id": 1,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


def resize_lines(new_total):
    if not st.session_state.lines:
        return
    old_total = len(st.session_state.lines[0]["values"])
    if old_total == new_total:
        return
    for line in st.session_state.lines:
        vals = line["values"]
        if new_total > len(vals):
            pad = np.full(new_total - len(vals), np.nan)
            line["values"] = np.concatenate([vals, pad])
        else:
            line["values"] = vals[:new_total]


def clamp(value, lo, hi):
    return max(lo, min(value, hi))


# ----------------------------------------------------------------------
# Kenar çubuğu
# ----------------------------------------------------------------------
with st.sidebar:
    st.header("Ayarlar")

    new_total_bars = st.number_input(
        "Toplam bar sayısı", min_value=20, max_value=300,
        value=int(st.session_state.total_bars), step=10,
    )
    if new_total_bars != st.session_state.total_bars:
        resize_lines(new_total_bars)
        st.session_state.total_bars = new_total_bars

    total_bars = st.session_state.total_bars
    window_size = total_bars   # artık kaydırma yok, pencere = toplam
    window_start = 0
    bar_px = CANVAS_WIDTH / total_bars
    st.caption(f"Bar başına ~{bar_px:.1f}px. Bar sayısı arttıkça çizim hassasiyeti düşer.")

    y_max = st.slider("Değer aralığı (Y ekseni, +/-)", min_value=1, max_value=50, value=10)

    st.markdown("---")
    base_price = st.number_input("Başlangıç fiyatı", min_value=1.0, value=100.0, step=1.0)
    wick_strength = st.slider("Fitil şiddeti (kozmetik)", min_value=0.0, max_value=5.0, value=1.0, step=0.1)

    st.markdown("---")
    if st.button("🗑 Tüm çizgileri temizle"):
        st.session_state.lines = []
        st.session_state.canvas_version += 1
        st.rerun()


# ----------------------------------------------------------------------
# Grid overlay - CANVAS BİLEŞENİNİN KENDİ ARKA PLAN ÖZELLİĞİNE GÜVENMİYORUZ
# (bazı Streamlit sürümlerinde background_color/background_image çalışmıyor).
# Bunun yerine, canvas'ın TAM ÜSTÜNE, mouse olaylarını engellemeyen
# (pointer-events:none) saf bir HTML/CSS katmanı bindiriyoruz. Bu katman
# component'in içine değil, Streamlit sayfasının kendi DOM'una render
# olduğu için component'in versiyon uyumluluğundan bağımsız, garanti çalışır.
# ----------------------------------------------------------------------
ZERO_LINE_COLOR = "#f59e0b"  # amber - net farklı, dikkat çekici


def grid_overlay_html(window_size, window_start, canvas_width, canvas_height, bar_px):
    parts = []
    step = max(1, window_size // 20)
    for j in range(0, window_size + 1, step):
        bar_idx = window_start + j
        left = j * bar_px
        strong = bar_idx % (step * 4) == 0
        color = "rgba(0,0,0,0.22)" if strong else "rgba(0,0,0,0.08)"
        parts.append(
            f'<div style="position:absolute; left:{left:.1f}px; top:0; width:1px; '
            f'height:{canvas_height}px; background:{color};"></div>'
        )
    mid = canvas_height // 2
    parts.append(
        f'<div style="position:absolute; left:0; top:{mid}px; width:{canvas_width}px; '
        f'height:2px; background:{ZERO_LINE_COLOR}; box-shadow:0 0 3px {ZERO_LINE_COLOR};"></div>'
    )
    return f"""
    <div style="position:relative; height:0; margin-bottom:-16px;">
      <div style="position:absolute; top:0; left:0; width:{canvas_width}px;
                  height:{canvas_height}px; pointer-events:none; z-index:999;">
        {''.join(parts)}
      </div>
    </div>
    """


def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def extract_stroke_values(image_data, window_size, canvas_height, y_max, target_color_hex, bar_px, tol=45):
    arr = np.array(image_data)[:, :, :3].astype(int)
    target = np.array(hex_to_rgb(target_color_hex))
    dist = np.sqrt(((arr - target) ** 2).sum(axis=2))
    drawn_mask = dist < tol

    values = np.full(window_size, np.nan)
    for j in range(window_size):
        x0 = int(round(j * bar_px))
        x1 = max(x0 + 1, int(round((j + 1) * bar_px)))
        rows, _ = np.where(drawn_mask[:, x0:x1])
        if len(rows) > 0:
            y_mean = rows.mean()
            values[j] = ((canvas_height / 2 - y_mean) / (canvas_height / 2)) * y_max
    return values


def value_axis_html(canvas_height, y_max):
    """Canvas'ın soluna, garanti şekilde görünen +/- değer etiketleri."""
    ticks = [y_max, y_max / 2, 0, -y_max / 2, -y_max]
    items = "".join(
        f'<div style="font-size:11px;color:#9ca3af;">{t:g}</div>' for t in ticks
    )
    return f"""
    <div style="height:{canvas_height}px; display:flex; flex-direction:column;
                justify-content:space-between; text-align:right; padding-right:4px;">
        {items}
    </div>
    """


def bar_axis_html(window_size, window_start, canvas_width, bar_px):
    step = max(1, window_size // 10)
    spans = []
    for j in range(0, window_size, step):
        left_px = j * bar_px
        spans.append(
            f'<span style="position:absolute; left:{left_px:.1f}px; '
            f'font-size:11px; color:#9ca3af;">{window_start + j}</span>'
        )
    return f'<div style="position:relative; height:16px; width:{canvas_width}px;">{"".join(spans)}</div>'


# ----------------------------------------------------------------------
# Ana sayfa
# ----------------------------------------------------------------------
st.title("Net Talep Farkı ile Sentetik Grafik Oluşturucu")

with st.expander("Nasıl kullanılır?", expanded=False):
    st.markdown(
        """
1. Tuvale fare ile bir çizgi çiz. Şu an hangi renkle çizdiğin canvas'ın altında yazar —
   bu renk, çizgiyi eklediğinde onun kalıcı rengi olacak (legend'daki ile aynı).
2. **Çizgiyi Ekle (ADD)** ile listeye ekle — grafik otomatik güncellenir. Çizgi canvas'ta
   **silinmeden kalır**, böylece bir sonraki çizgiyi öncekiyle kıyaslayarak çizebilirsin.
3. İstediğin kadar çizgi ekle, listeden istediğini **Kaldır** ile sil (canvas'taki iz
   kalabilir, sadece görsel referans amaçlıdır — hesaplamaya dahil edilmez).
4. Tüm bar'lar tek seferde, sabit genişlikte gösterilir — artık kaydırma/pencere yok.
   Bar sayısını artırırsan bar başına düşen piksel azalır, çizim daha hassas olmaktan çıkar.
        """
    )

canvas_width = CANVAS_WIDTH
col_axis, col_canvas, col_legend = st.columns([0.06, 0.74, 0.20])

with col_axis:
    st.markdown(value_axis_html(CANVAS_HEIGHT, y_max), unsafe_allow_html=True)

with col_canvas:
    canvas_key = f"canvas_{st.session_state.canvas_version}_{total_bars}"
    active_color = LINE_PALETTE[(st.session_state.next_line_id - 1) % len(LINE_PALETTE)]

    st.markdown(
        grid_overlay_html(window_size, window_start, canvas_width, CANVAS_HEIGHT, bar_px),
        unsafe_allow_html=True,
    )
    canvas_result = st_canvas(
        fill_color="rgba(255,255,255,0)",
        stroke_width=2,
        stroke_color=active_color,
        background_color="#FFFFFF",
        update_streamlit=True,
        height=CANVAS_HEIGHT,
        width=canvas_width,
        drawing_mode="freedraw",
        key=canvas_key,
    )
    st.markdown(bar_axis_html(window_size, window_start, canvas_width, bar_px), unsafe_allow_html=True)
    st.caption(f"Şu an çizdiğin renk: **{active_color}** (bu, eklendiğinde bu çizginin rengi olacak)")

    add_clicked = st.button("➕ Çizgiyi Ekle (ADD)", use_container_width=True)

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
        stroke_values = extract_stroke_values(
            canvas_result.image_data, window_size, CANVAS_HEIGHT, y_max, active_color, bar_px
        )
        if np.all(np.isnan(stroke_values)):
            st.warning("Bu renkte bir çizim algılanamadı, tekrar dener misin?")
        else:
            full_values = np.full(st.session_state.total_bars, np.nan)
            full_values[window_start:window_start + window_size] = stroke_values

            new_id = st.session_state.next_line_id
            st.session_state.next_line_id += 1

            st.session_state.lines.append({
                "id": new_id, "name": f"Line {new_id}", "color": active_color, "values": full_values,
            })
            st.rerun()


# ----------------------------------------------------------------------
# OHLCV hesapla ve otomatik göster
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
    unit = y_max * 0.05
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


try:
    if st.session_state.lines:
        n = st.session_state.total_bars
        stacked = np.stack([l["values"] for l in st.session_state.lines])
        covered = ~np.all(np.isnan(stacked), axis=0)
        flow_total = np.nansum(stacked, axis=0)
        volume = np.nansum(np.abs(stacked), axis=0)

        open_, high, low, close = build_ohlcv(flow_total, volume, base_price, wick_strength, y_max)

        df = pd.DataFrame({
            "bar": np.arange(0, n),
            "Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume,
        })

        _, result_col, _ = st.columns([0.06, 0.74, 0.20])
        with result_col:
            st.subheader("Sonuç")
            if not covered.all():
                st.caption("Gri gölgeli bölgeler: hiçbir çizginin kapsamadığı bar'lar (akış = 0 kabul edildi).")

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

            # kapsanmayan bölgeleri gölgele (0-tabanlı bar indeksine göre)
            in_gap = False
            gap_start = None
            for i in range(n):
                if not covered[i] and not in_gap:
                    in_gap = True
                    gap_start = i
                elif covered[i] and in_gap:
                    in_gap = False
                    fig.add_vrect(x0=gap_start - 0.5, x1=i - 0.5, fillcolor="gray", opacity=0.12, line_width=0)
            if in_gap:
                fig.add_vrect(x0=gap_start - 0.5, x1=n - 0.5, fillcolor="gray", opacity=0.12, line_width=0)

            fig.update_xaxes(range=[-0.5, n - 0.5])
            fig.update_yaxes(showticklabels=False)
            fig.update_layout(
                width=CANVAS_WIDTH, height=650,
                xaxis_rangeslider_visible=False, showlegend=False,
                margin=dict(t=20, b=20, l=0, r=0),
            )
            st.caption("Hizalama için fiyat eksen etiketleri gizlendi — değerleri görmek için mumların üzerine gel.")
            st.plotly_chart(fig)

            st.download_button(
                "OHLCV verisini CSV olarak indir",
                df.to_csv(index=False).encode("utf-8"),
                file_name="sentetik_ohlcv.csv",
                mime="text/csv",
            )
except Exception:
    st.error("Grafik oluşturulurken beklenmeyen bir hata oluştu. Detayları aşağıda paylaşabilirsin.")
    with st.expander("Hata detayı (bana gönderebilirsin)"):
        st.code(traceback.format_exc())
