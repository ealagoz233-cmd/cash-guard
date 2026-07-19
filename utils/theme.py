"""
theme.py
────────
Cash Guard'ın "war-room" görsel kimliği: karanlık kurumsal komuta merkezi.
Streamlit'in varsayılan görünümünü, iri KPI kartları ve alarm renkleriyle bir
yönetici kontrol paneline dönüştüren CSS + küçük HTML bileşenleri burada.

Palet:
    Arka plan   #0A0E17   derin karbon-lacivert
    Panolar     #141B2D
    Guardian    #00E0A4   (güvenli / pozitif / aksiyon)
    Alarm       #FF3B47   (tehlike / temerrüt / iflas)
    Amber       #FFB020   (uyarı)
"""
from __future__ import annotations

import html

import streamlit as st


def esc(value) -> str:
    """
    Ham HTML'e gömülecek her DEĞİŞKEN bundan geçmeli.

    Bu dosyadaki bileşenler `unsafe_allow_html=True` ile basılıyor; içine giren
    metin ise kullanıcı verisinden gelebiliyor (şirket adı, yüklenen dosyanın
    adı). Kaçırılmazsa `<img src=x onerror=...>` adlı bir dosya sayfaya ham
    HTML olarak enjekte olur — bir kez böyle bir açık oluştu, bu yüzden burada.
    """
    return html.escape(str(value), quote=True)

# ── Renk sabitleri (modüller de bu paletten çeksin diye dışa açık) ────────
COLORS = {
    "bg": "#0A0E17",
    "panel": "#141B2D",
    "panel_border": "#232B3E",
    "guardian": "#00E0A4",
    "guardian_dim": "#0B7A5C",
    "alarm": "#FF3B47",
    "amber": "#FFB020",
    "text": "#E6EDF3",
    "muted": "#8A95A8",
    "grid": "#1E2637",
}

# Batma olasılığına göre "tehdit rengi" eşikleri (KPI + metriklerde ortak)
def threat_color(prob_pct: float) -> str:
    """0–100 arası batma olasılığını renge çevirir."""
    if prob_pct >= 60:
        return COLORS["alarm"]
    if prob_pct >= 30:
        return COLORS["amber"]
    return COLORS["guardian"]


def inject_css() -> None:
    """Global CSS'i sayfaya enjekte eder. app.py'de en başta bir kez çağrılır."""
    st.markdown(
        f"""
        <style>
        /* ── Genel zemin ─────────────────────────────────────────── */
        .stApp {{
            background:
                radial-gradient(1200px 600px at 15% -10%, #10192b 0%, transparent 55%),
                radial-gradient(900px 500px at 100% 0%, #161022 0%, transparent 50%),
                {COLORS['bg']};
        }}
        .block-container {{ padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1500px; }}

        /* Streamlit başlık/menü sadeleştir */
        #MainMenu, footer {{ visibility: hidden; }}

        /* ── Marka başlığı ───────────────────────────────────────── */
        .cg-brand {{
            display:flex; align-items:center; gap:14px; margin-bottom:2px;
        }}
        .cg-brand .cg-logo {{
            font-size:30px; line-height:1;
            filter: drop-shadow(0 0 10px {COLORS['guardian']}55);
        }}
        .cg-brand h1 {{
            font-size:30px; font-weight:800; letter-spacing:-0.5px; margin:0;
            background: linear-gradient(90deg, {COLORS['text']}, {COLORS['guardian']});
            -webkit-background-clip:text; -webkit-text-fill-color:transparent;
        }}
        .cg-sub {{ color:{COLORS['muted']}; font-size:13.5px; margin:2px 0 4px 46px; }}

        /* ── KPI kartları ────────────────────────────────────────── */
        .cg-kpi {{
            background: linear-gradient(180deg, #172037 0%, {COLORS['panel']} 100%);
            border:1px solid {COLORS['panel_border']};
            border-radius:16px; padding:16px 18px; height:100%;
            box-shadow: 0 8px 24px #00000040, inset 0 1px 0 #ffffff08;
            position:relative; overflow:hidden;
            /* Değer yazısının kart genişliğine göre ölçeklenebilmesi için
               (cqw birimi buna bağlı). Ekran genişliği yetmez: sidebar
               açılınca ekran aynı kalır ama kolonlar daralır. */
            container-type: inline-size;
        }}
        .cg-kpi::before {{
            content:""; position:absolute; left:0; top:0; bottom:0; width:4px;
            background: var(--accent, {COLORS['guardian']});
            box-shadow: 0 0 18px var(--accent, {COLORS['guardian']});
        }}
        .cg-kpi .cg-kpi-label {{
            color:{COLORS['muted']}; font-size:12px; font-weight:600;
            text-transform:uppercase; letter-spacing:0.8px;
        }}
        .cg-kpi .cg-kpi-value {{
            /* Sabit 29px'ti ve dar kolonda sayı ORTADAN bölünüyordu:
               "₺4.200.000" monospace'te ~174px ister; kolon darsa tarayıcı
               boşluksuz diziyi kırıp "₺4.200.0 / 00" yapıyordu. Üstelik eksi
               işareti tek başına alt satıra düşüyordu.
               Çözüm üç parça: sayı asla kırılmasın (nowrap + keep-all), yazı
               kart genişliğine göre küçülsün (cqw), ama okunaklılık için
               14px'in altına inmesin ve 29px'i aşmasın. */
            font-size: clamp(14px, 13cqw, 29px);
            white-space: nowrap; word-break: keep-all; overflow-wrap: normal;
            font-weight:800; margin-top:6px; line-height:1.05;
            font-variant-numeric: tabular-nums; color:var(--accent, {COLORS['text']});
            font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
        }}
        .cg-kpi .cg-kpi-sub {{ color:{COLORS['muted']}; font-size:12px; margin-top:5px; }}

        /* ── Bölüm başlıkları ────────────────────────────────────── */
        .cg-section {{
            display:flex; align-items:center; gap:10px;
            margin:26px 0 6px; font-size:19px; font-weight:700; color:{COLORS['text']};
        }}
        .cg-section .cg-chip {{
            font-size:11px; font-weight:700; color:{COLORS['bg']};
            background:{COLORS['guardian']}; padding:2px 9px; border-radius:999px;
            letter-spacing:0.5px;
        }}
        .cg-hr {{ border:0; border-top:1px solid {COLORS['panel_border']}; margin:6px 0 14px; }}

        /* ── Devasa "batma olasılığı" uyarı bloğu ────────────────── */
        .cg-verdict {{
            border-radius:18px; padding:22px 26px; margin-top:4px;
            border:1px solid var(--vc, {COLORS['alarm']})55;
            background:
                radial-gradient(600px 200px at 0% 0%, var(--vc, {COLORS['alarm']})1f, transparent 60%),
                {COLORS['panel']};
        }}
        .cg-verdict .cg-verdict-label {{
            color:{COLORS['muted']}; font-size:13px; font-weight:600;
            text-transform:uppercase; letter-spacing:1px;
        }}
        .cg-verdict .cg-verdict-num {{
            font-size:66px; font-weight:900; line-height:1; margin:6px 0;
            color:var(--vc, {COLORS['alarm']});
            text-shadow:0 0 30px var(--vc, {COLORS['alarm']})55;
            font-variant-numeric: tabular-nums;
        }}
        .cg-verdict .cg-verdict-msg {{ color:{COLORS['text']}; font-size:15px; font-weight:500; }}

        /* ── CFO raporu kutusu ───────────────────────────────────── */
        .cg-cfo {{
            background: linear-gradient(180deg, #1a1220 0%, {COLORS['panel']} 100%);
            border:1px solid {COLORS['alarm']}33; border-left:4px solid {COLORS['alarm']};
            border-radius:14px; padding:20px 24px; margin-top:6px;
            font-size:15px; line-height:1.65; color:{COLORS['text']};
        }}
        .cg-cfo strong {{ color:{COLORS['amber']}; }}

        /* ── Sidebar rötuşları ───────────────────────────────────── */
        section[data-testid="stSidebar"] {{
            background:{COLORS['panel']}; border-right:1px solid {COLORS['panel_border']};
        }}
        section[data-testid="stSidebar"] .stSlider label {{ font-weight:600; }}

        /* Küçük rozet */
        .cg-badge {{
            display:inline-block; font-size:11px; font-weight:600;
            color:{COLORS['muted']}; border:1px solid {COLORS['panel_border']};
            border-radius:999px; padding:2px 10px; margin-right:6px;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def brand_header() -> None:
    """Üstteki 'Cash Guard' marka başlığı."""
    st.markdown(
        """
        <div class="cg-brand">
            <span class="cg-logo">🛡️</span>
            <h1>Cash&nbsp;Guard</h1>
        </div>
        <div class="cg-sub">Kurumsal Nakit Hayatta Kalma &amp; Kredi Stres Testi Motoru</div>
        """,
        unsafe_allow_html=True,
    )


def kpi_card(label: str, value: str, sub: str = "", accent: str | None = None) -> str:
    """
    Tek bir KPI kartının HTML'ini döndürür (st.columns içine yazılır).

    Metin alanları kaçırılır: çağıranlar düz metin veriyor ve bir kısmı
    kullanıcı verisinden türüyor. Kart içinde HTML gerekirse bu bilinçli
    olarak değiştirilmeli, kazara değil.
    """
    accent = accent or COLORS["guardian"]
    return (
        f'<div class="cg-kpi" style="--accent:{accent};">'
        f'<div class="cg-kpi-label">{esc(label)}</div>'
        f'<div class="cg-kpi-value">{esc(value)}</div>'
        f'<div class="cg-kpi-sub">{esc(sub)}</div>'
        f"</div>"
    )


def section(title: str, chip: str = "") -> None:
    """Numaralı/etiketli bölüm başlığı + ince ayraç."""
    chip_html = f'<span class="cg-chip">{chip}</span>' if chip else ""
    st.markdown(
        f'<div class="cg-section">{chip_html}{title}</div><hr class="cg-hr"/>',
        unsafe_allow_html=True,
    )


# Gider kalemi anahtarı -> düzgün Türkçe etiket.
# str.title() burada KULLANILAMAZ: "kira_ve_isletme" -> "Kira Ve Isletme" gibi
# hem noktasız İ hem büyük "Ve" üretir. app.py (donut) ve ai_cfo.py (rapor
# metni) aynı haritayı kullansın diye ortak yere konuldu.
EXPENSE_LABELS = {
    "personel": "Personel",
    "kira_ve_isletme": "Kira ve İşletme",
    "hammadde_ve_tedarik": "Hammadde ve Tedarik",
    "pazarlama": "Pazarlama",
    "enerji_ve_lojistik": "Enerji ve Lojistik",
}


def expense_label(key: str) -> str:
    """Gider anahtarını okunur Türkçe etikete çevirir; bilinmeyende nazik fallback."""
    if key in EXPENSE_LABELS:
        return EXPENSE_LABELS[key]
    # Bilinmeyen anahtar: alt çizgileri boşluğa çevir, yalnız ilk harfi büyüt
    # (Türkçe'de "ve/ile" gibi bağlaçlar küçük kalsın diye .title() yok).
    words = key.replace("_", " ").strip()
    return words[:1].upper() + words[1:] if words else key


def tr_num(value: float) -> str:
    """
    10000 -> '10.000' (Türk stili binlik ayracı, para birimi yok).

    Ayrı bir yardımcı olmasının sebebi: cümle içinde `f"...".replace(",", ".")`
    yazmak metindeki normal virgülleri de noktaya çeviriyordu. Dönüşüm yalnız
    sayıya uygulanmalı.
    """
    try:
        return f"{value:,.0f}".replace(",", ".")
    except (ValueError, TypeError):
        return str(value)


def money(value: float, symbol: str = "₺") -> str:
    """4200000 -> '₺4.200.000' (Türk stili binlik ayracı)."""
    try:
        s = f"{abs(value):,.0f}".replace(",", ".")
    except (ValueError, TypeError):
        return f"{symbol}0"
    sign = "-" if value < 0 else ""
    return f"{sign}{symbol}{s}"
