"""
Net Talep Farkı ile Sentetik Grafik Oluşturucu - Aşama 1 (v3)
----------------------------------------------------------------
v2'ye göre KÖKLÜ değişiklik:
  Önceki sürümlerde grid'i (ve eklenmiş çizgileri) ayrı bir HTML/CSS
  katmanı olarak üçüncü parti bir canvas bileşeninin ÜSTÜNE bindirmeye
  çalışıyorduk. Bu, iki ayrı DOM elemanının pixel-perfect hizalanmasını
  gerektiriyordu ve bu hizalama tekrar tekrar bozuluyordu (Streamlit'in
  elemanlar arası boşluğu tahmin edilemiyor).

  Şimdi bunun yerine Streamlit'in "Custom Components v2" özelliğiyle
  KENDİ canvas bileşenimizi yazdık: grid, eklenmiş çizgiler VE fare ile
  çizim, hepsi TEK BİR <canvas> elemanına, TEK BİR JavaScript koduyla
  çiziliyor. İki ayrı eleman olmadığı için hizalama sorunu yapısal
  olarak imkansız hale geldi. Ayrıca artık üçüncü parti
  streamlit-drawable-canvas kütüphanesine hiç ihtiyacımız yok.
"""

import math
import traceback

import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ----------------------------------------------------------------------
# Sabitler
# ----------------------------------------------------------------------
CANVAS_WIDTH = 1400
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
    "next_line_id": 1,
    "polyline_points": [],   # kırık çizgi modunda biriken (bar, değer) noktaları
    "pending_stroke": None,  # serbest çizimde son tamamlanan çizginin (bar,değer) noktaları
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
    bar_px = CANVAS_WIDTH / total_bars
    st.caption(f"Bar başına ~{bar_px:.1f}px. Bar sayısı arttıkça çizim hassasiyeti düşer.")

    y_max = st.slider("Değer aralığı (Y ekseni, +/-)", min_value=1, max_value=50, value=10)

    st.markdown("---")
    base_price = st.number_input("Başlangıç fiyatı", min_value=1.0, value=100.0, step=1.0)
    wick_strength = st.slider("Fitil şiddeti (kozmetik)", min_value=0.0, max_value=5.0, value=1.0, step=0.1)

    st.markdown("---")
    if st.button("🗑 Tüm çizgileri temizle"):
        st.session_state.lines = []
        st.rerun()


# ----------------------------------------------------------------------
# Yan eksen etiketleri (basit, güvenilir - canvas'tan bağımsız bir sütunda)
# ----------------------------------------------------------------------
def value_axis_html(canvas_height, y_max, align="right"):
    ticks = [y_max, y_max / 2, 0, -y_max / 2, -y_max]
    side = "right" if align == "right" else "left"
    items = []
    for t in ticks:
        top = canvas_height / 2 - (t / y_max) * (canvas_height / 2)
        items.append(
            f'<div style="position:absolute; top:{top - 7:.1f}px; {side}:4px; '
            f'font-size:11px; color:#9ca3af;">{t:g}</div>'
        )
    return f'<div style="position:relative; height:{canvas_height}px;">{"".join(items)}</div>'


def bar_axis_html(window_size, canvas_width, bar_px):
    step = max(1, window_size // 10)
    spans = []
    for j in range(0, window_size, step):
        left_px = j * bar_px
        spans.append(
            f'<span style="position:absolute; left:{left_px:.1f}px; '
            f'font-size:11px; color:#9ca3af;">{j}</span>'
        )
    return f'<div style="position:relative; height:16px; width:{canvas_width}px;">{"".join(spans)}</div>'


# ----------------------------------------------------------------------
# Özel canvas bileşeni - grid + eklenmiş çizgiler + önizleme + serbest çizim
# hepsi TEK canvas elemanında, TEK JS kodunda (Custom Components v2)
# ----------------------------------------------------------------------
_CANVAS_HTML = '<canvas id="dc"></canvas>'
_CANVAS_CSS = """
canvas { display:block; cursor:crosshair; touch-action:none; background:#ffffff; }
"""
_CANVAS_JS = r"""
export default function(component) {
    const { data, parentElement, setTriggerValue } = component;

    const oldCanvas = parentElement.querySelector("#dc");
    const canvas = oldCanvas.cloneNode(true);
    oldCanvas.replaceWith(canvas);
    const ctx = canvas.getContext("2d");

    const W = data.canvas_width;
    const H = data.canvas_height;
    const yMax = data.y_max;
    const barPx = data.bar_px;
    const windowSize = data.window_size;
    const lines = data.lines || [];
    const activeColor = data.active_color || "#1f2937";
    const previewType = data.preview_type || "none";
    const previewValue = data.preview_value;
    const previewPoints = data.preview_points || [];

    canvas.width = W;
    canvas.height = H;
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";

    function valueToY(v) { return H / 2 - (v / yMax) * (H / 2); }
    function yToValue(y) { return ((H / 2 - y) / (H / 2)) * yMax; }
    function barToX(b) { return (b + 0.5) * barPx; }

    function drawGrid() {
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(0, 0, W, H);
        const step = Math.max(1, Math.floor(windowSize / 20));
        for (let j = 0; j <= windowSize; j += step) {
            const x = j * barPx;
            const strong = (j % (step * 4)) === 0;
            ctx.strokeStyle = strong ? "rgba(0,0,0,0.35)" : "rgba(0,0,0,0.15)";
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
        }
        for (let k = -4; k <= 4; k++) {
            if (k === 0) continue;
            const y = valueToY(yMax * k / 4);
            const strong = (k % 2 === 0);
            ctx.strokeStyle = strong ? "rgba(0,0,0,0.30)" : "rgba(0,0,0,0.14)";
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
        }
        ctx.strokeStyle = "#f59e0b";
        ctx.lineWidth = 2;
        const yz = valueToY(0);
        ctx.beginPath(); ctx.moveTo(0, yz); ctx.lineTo(W, yz); ctx.stroke();
    }

    function drawLines() {
        lines.forEach(line => {
            const vals = line.values;
            const vis = line.visible !== false;
            ctx.strokeStyle = line.color;
            ctx.lineWidth = vis ? 2.5 : 1.5;
            ctx.globalAlpha = vis ? 0.9 : 0.35;
            ctx.setLineDash(vis ? [] : [5, 4]);
            let drawing = false;
            ctx.beginPath();
            for (let j = 0; j < vals.length; j++) {
                const v = vals[j];
                if (v === null || v === undefined) { drawing = false; continue; }
                const x = barToX(j), y = valueToY(v);
                if (!drawing) { ctx.moveTo(x, y); drawing = true; } else { ctx.lineTo(x, y); }
            }
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.globalAlpha = 1;
        });
    }

    function drawPreview() {
        if (previewType === "horizontal" && previewValue !== null && previewValue !== undefined) {
            const y = valueToY(previewValue);
            ctx.strokeStyle = activeColor;
            ctx.lineWidth = 2;
            ctx.setLineDash([6, 4]);
            ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
            ctx.setLineDash([]);
        } else if (previewType === "points" && previewPoints.length > 0) {
            const pts = previewPoints.map(p => ({ x: barToX(p.bar), y: valueToY(p.value) }));
            if (pts.length >= 2) {
                ctx.strokeStyle = activeColor;
                ctx.lineWidth = 2;
                ctx.setLineDash([6, 4]);
                ctx.beginPath();
                pts.forEach((p, i) => { if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y); });
                ctx.stroke();
                ctx.setLineDash([]);
            }
            pts.forEach((p, i) => {
                ctx.beginPath();
                ctx.arc(p.x, p.y, 5, 0, 2 * Math.PI);
                ctx.fillStyle = "#ffffff";
                ctx.fill();
                ctx.strokeStyle = activeColor;
                ctx.lineWidth = 2;
                ctx.stroke();
                ctx.fillStyle = activeColor;
                ctx.font = "11px sans-serif";
                ctx.fillText(String(i + 1), p.x + 7, p.y - 7);
            });
        }
    }

    function redraw() {
        drawGrid();
        drawLines();
        drawPreview();
    }
    redraw();

    let drawingPoints = null;

    function getPos(e) {
        const rect = canvas.getBoundingClientRect();
        const x = (e.clientX - rect.left) * (W / rect.width);
        const y = (e.clientY - rect.top) * (H / rect.height);
        return { x, y };
    }

    function onDown(e) {
        drawingPoints = [getPos(e)];
    }
    function onMove(e) {
        if (!drawingPoints) return;
        drawingPoints.push(getPos(e));
        redraw();
        ctx.strokeStyle = activeColor;
        ctx.lineWidth = 2;
        ctx.beginPath();
        drawingPoints.forEach((p, i) => { if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y); });
        ctx.stroke();
    }
    function finishStroke() {
        if (!drawingPoints || drawingPoints.length < 2) { drawingPoints = null; return; }
        const pts = drawingPoints.map(p => ({ bar: p.x / barPx, value: yToValue(p.y) }));
        setTriggerValue("stroke_done", pts);
        drawingPoints = null;
    }
    function onUp() { finishStroke(); }
    function onLeave() { if (drawingPoints) finishStroke(); }

    canvas.addEventListener("mousedown", onDown);
    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseup", onUp);
    canvas.addEventListener("mouseleave", onLeave);

    return () => {
        canvas.removeEventListener("mousedown", onDown);
        canvas.removeEventListener("mousemove", onMove);
        canvas.removeEventListener("mouseup", onUp);
        canvas.removeEventListener("mouseleave", onLeave);
    };
}
"""

draw_canvas = st.components.v2.component(
    name="net_talep_draw_canvas",
    html=_CANVAS_HTML,
    css=_CANVAS_CSS,
    js=_CANVAS_JS,
)


def build_lines_payload():
    payload = []
    for line in st.session_state.lines:
        vals = line["values"]
        payload.append({
            "color": line["color"],
            "visible": bool(line.get("visible", True)),
            "values": [
                None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 4)
                for v in vals
            ],
        })
    return payload


# ----------------------------------------------------------------------
# Ana sayfa
# ----------------------------------------------------------------------
st.title("Net Talep Farkı ile Sentetik Grafik Oluşturucu")

with st.expander("Nasıl kullanılır?", expanded=False):
    st.markdown(
        """
**Üç çizim aracı var (üstteki seçiciden):**
- **Serbest çizim:** fare ile istediğin (yaklaşık) şekli çiz, ADD ile ekle — organik
  dalgalar için, piksel kesinliği gerekmez.
- **Yatay çizgi:** kaydırıcıdan kesin değeri seç (canvas'ta kesikli önizlemesini
  görürsün), "Ekle" de — %100 kesin, fareye hiç gerek yok.
- **Kırık çizgi (nokta nokta):** her köşe için Bar/Değer kaydırıcılarını ayarla
  (canvas'ta o an nerede duracağını önizlersin), "Nokta Ekle" de. Noktalar
  aralarında düz çizgilerle birleşir. Bitirince "Çizgiyi Tamamla" ile ekle.

Eklediğin tüm çizgiler, canvas'ın üzerinde kendi renkleriyle kalıcı olarak
çizili kalır — grid ve çizimler artık AYNI canvas elemanına, aynı kodla
çiziliyor, bu yüzden hizalama sorunu yapısal olarak mümkün değil.
        """
    )

tool = st.radio(
    "Çizim aracı", ["Serbest çizim", "Yatay çizgi", "Kırık çizgi (nokta nokta)"],
    horizontal=True, key="tool_select",
)

preview_type = "none"
preview_value = None
horiz_value = 0.0
point_bar, point_value = 0, 0.0

if tool == "Yatay çizgi":
    horiz_value = st.slider(
        "Yatay çizginin değeri (canvas'ta kesikli önizlemesini göreceksin)",
        min_value=-float(y_max), max_value=float(y_max), value=0.0, step=0.1, key="horiz_value_slider",
    )
    preview_type = "horizontal"
    preview_value = float(horiz_value)

elif tool == "Kırık çizgi (nokta nokta)":
    st.caption("Kesin köşe noktaları için fareye değil, sayıya güveniyoruz — önizlemesini canvas'ta göreceksin.")
    pc_a, pc_b = st.columns(2)
    with pc_a:
        point_bar = st.slider(
            "Nokta - Bar", min_value=0, max_value=st.session_state.total_bars - 1,
            value=0, step=1, key="point_bar_slider",
        )
    with pc_b:
        point_value = st.slider(
            "Nokta - Değer", min_value=-float(y_max), max_value=float(y_max),
            value=0.0, step=0.1, key="point_value_slider",
        )
    preview_type = "points"

col_axis, col_canvas, col_axis_right, col_legend = st.columns([0.06, 0.74, 0.06, 0.14])

with col_axis:
    st.markdown(value_axis_html(CANVAS_HEIGHT, y_max), unsafe_allow_html=True)

with col_axis_right:
    st.markdown(value_axis_html(CANVAS_HEIGHT, y_max, align="left"), unsafe_allow_html=True)

with col_canvas:
    active_color = LINE_PALETTE[(st.session_state.next_line_id - 1) % len(LINE_PALETTE)]

    preview_points_payload = []
    if preview_type == "points":
        pts = st.session_state.polyline_points + [(float(point_bar), float(point_value))]
        preview_points_payload = [{"bar": b, "value": v} for b, v in pts]

    canvas_result = draw_canvas(
        data={
            "canvas_width": CANVAS_WIDTH,
            "canvas_height": CANVAS_HEIGHT,
            "window_size": total_bars,
            "bar_px": bar_px,
            "y_max": y_max,
            "lines": build_lines_payload(),
            "active_color": active_color,
            "preview_type": preview_type,
            "preview_value": preview_value,
            "preview_points": preview_points_payload,
        },
        on_stroke_done_change=lambda: None,
        key="draw_canvas_main",
        width=CANVAS_WIDTH,
        height=CANVAS_HEIGHT,
    )
    # ÖNEMLİ: stroke_done bir trigger değeri, sadece çizim biten anki rerun'da
    # dolu oluyor, hemen sonrasında (örn. ADD'e basınca) otomatik sıfırlanıyor.
    # Bu yüzden gelir gelmez kalıcı hafızaya (session_state) kaydediyoruz.
    if canvas_result.stroke_done:
        st.session_state.pending_stroke = canvas_result.stroke_done

    st.markdown(bar_axis_html(total_bars, CANVAS_WIDTH, bar_px), unsafe_allow_html=True)
    st.caption(f"Şu an çizdiğin renk: **{active_color}** (bu, eklendiğinde bu çizginin rengi olacak)")

    if tool == "Serbest çizim":
        add_clicked = st.button("➕ Çizgiyi Ekle (ADD)", use_container_width=True)
        horiz_clicked = False
        point_clicked = finish_clicked = undo_clicked = cancel_clicked = False

    elif tool == "Yatay çizgi":
        horiz_clicked = st.button("➕ Yatay Çizgiyi Ekle", use_container_width=True)
        add_clicked = False
        point_clicked = finish_clicked = undo_clicked = cancel_clicked = False

    else:  # Kırık çizgi
        pc1, pc2, pc3 = st.columns(3)
        with pc1:
            point_clicked = st.button("📍 Nokta Ekle", use_container_width=True)
        with pc2:
            undo_clicked = st.button("↩ Son Noktayı Sil", use_container_width=True)
        with pc3:
            cancel_clicked = st.button("✕ İptal", use_container_width=True)
        n_pts = len(st.session_state.polyline_points)
        if n_pts > 0:
            pts_str = "  ·  ".join(f"({b:.0f}, {v:+.2f})" for b, v in st.session_state.polyline_points)
            st.caption(f"Noktalar ({n_pts}): {pts_str}")
        finish_clicked = st.button(
            "✅ Çizgiyi Tamamla (ADD)", use_container_width=True, disabled=n_pts < 2,
        )
        add_clicked = horiz_clicked = False

with col_legend:
    st.subheader("Çizgiler")
    if not st.session_state.lines:
        st.caption("Henüz çizgi eklenmedi.")
    for line in list(st.session_state.lines):
        is_visible = line.get("visible", True)
        c1, c2, c3 = st.columns([3, 1, 1])
        with c1:
            opacity = "1" if is_visible else "0.35"
            st.markdown(
                f'<span style="display:inline-block;width:12px;height:12px;'
                f'background-color:{line["color"]};border-radius:2px;opacity:{opacity};"></span> '
                f'<span style="opacity:{opacity};">{line["name"]}</span>',
                unsafe_allow_html=True,
            )
        with c2:
            toggle_label = "Göster" if not is_visible else "Gizle"
            if st.button(toggle_label, key=f"toggle_{line['id']}"):
                line["visible"] = not is_visible
                st.rerun()
        with c3:
            if st.button("Kaldır", key=f"remove_{line['id']}"):
                st.session_state.lines = [l for l in st.session_state.lines if l["id"] != line["id"]]
                st.rerun()

# ----------------------------------------------------------------------
# ADD işlemi - Serbest çizim (canvas'tan gelen (bar, değer) noktalarından)
# ----------------------------------------------------------------------
# ADD işlemi - Serbest çizim (canvas'tan gelen (bar, değer) noktalarından)
# ----------------------------------------------------------------------
if add_clicked and tool == "Serbest çizim":
    points = st.session_state.pending_stroke
    if not points:
        st.warning("Önce tuvale bir çizgi çiz.")
    else:
        bars = np.array([p["bar"] for p in points])
        vals = np.array([p["value"] for p in points])

        stroke_values = np.full(total_bars, np.nan)
        for j in range(total_bars):
            mask = (bars >= j) & (bars < j + 1)
            if mask.any():
                stroke_values[j] = vals[mask].mean()

        if np.all(np.isnan(stroke_values)):
            st.warning("Çizim algılanamadı, tekrar dener misin?")
        else:
            new_id = st.session_state.next_line_id
            st.session_state.next_line_id += 1
            st.session_state.lines.append({
                "id": new_id, "name": f"Line {new_id}", "color": active_color,
                "values": stroke_values, "visible": True,
            })
            st.session_state.pending_stroke = None
            st.rerun()

# ----------------------------------------------------------------------
# ADD işlemi - Yatay çizgi (sayısal girişten, %100 kesin)
# ----------------------------------------------------------------------
if horiz_clicked:
    full_values = np.full(st.session_state.total_bars, float(horiz_value))
    new_id = st.session_state.next_line_id
    st.session_state.next_line_id += 1
    st.session_state.lines.append({
        "id": new_id, "name": f"Line {new_id}", "color": active_color, "values": full_values, "visible": True,
    })
    st.rerun()

# ----------------------------------------------------------------------
# Kırık çizgi - nokta ekleme / geri alma / iptal / tamamlama
# ----------------------------------------------------------------------
if point_clicked:
    st.session_state.polyline_points.append((float(point_bar), float(point_value)))
    st.rerun()

if undo_clicked and st.session_state.polyline_points:
    st.session_state.polyline_points.pop()
    st.rerun()

if cancel_clicked:
    st.session_state.polyline_points = []
    st.rerun()

if finish_clicked:
    pts = sorted(st.session_state.polyline_points, key=lambda p: p[0])
    bars = np.array([p[0] for p in pts])
    vals = np.array([p[1] for p in pts])

    full_values = np.full(st.session_state.total_bars, np.nan)
    lo, hi = int(np.ceil(bars.min())), int(np.floor(bars.max()))
    lo, hi = max(lo, 0), min(hi, st.session_state.total_bars - 1)
    if hi >= lo:
        xs = np.arange(lo, hi + 1)
        full_values[lo:hi + 1] = np.interp(xs, bars, vals)

    new_id = st.session_state.next_line_id
    st.session_state.next_line_id += 1
    st.session_state.lines.append({
        "id": new_id, "name": f"Line {new_id}", "color": active_color, "values": full_values, "visible": True,
    })
    st.session_state.polyline_points = []
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

    body_sizes = np.abs(close - open_)
    avg_body = body_sizes.mean()
    if avg_body <= 0:
        avg_body = max(base_price * 0.01, 0.5)
    unit = avg_body * 0.8

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
    visible_lines = [l for l in st.session_state.lines if l.get("visible", True)]
    if visible_lines:
        n = st.session_state.total_bars
        stacked = np.stack([l["values"] for l in visible_lines])
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
            st.plotly_chart(fig, width=CANVAS_WIDTH)

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
