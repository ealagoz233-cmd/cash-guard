"""
app.py  —  Cash Guard ana Streamlit arayüzü
────────────────────────────────────────────
Kurumsal Nakit Hayatta Kalma & Kredi Stres Testi Motoru.

Akış:
  1) Veri yükle (mock JSON veya kullanıcı CSV/Excel'i).
  2) Sidebar sürgüleriyle kredi + stres parametrelerini al.
  3) KPI şeridi + 3 modül:
       Modül 1  Kredi Kurtarır mı? (deterministik nakit eğrisi)
       Modül 2  Monte Carlo Kasa Stres Testi (batma olasılığı)
       Modül 3  Acımasız CFO Ajanı (LLM ya da kural tabanlı)

Sürgüler her değiştiğinde Streamlit script'i baştan koşar; pahalı Monte Carlo
hesabı @st.cache_data ile parametrelere göre önbelleğe alınır, böylece arayüz
akıcı kalır.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from modules import loan_simulator as ls
from modules import monte_carlo as mc
from modules import scenario
from modules.ai_cfo import RuthlessCFO
from modules.data_io import REQUIRED_HISTORY_COLS, load_mock, parse_uploaded
from modules.report import build_report
from modules.runway import static_runway, trend_runway
from utils import theme
from utils.theme import COLORS, expense_label, money, threat_color, tr_num

# ── Sayfa yapılandırması + tema ───────────────────────────────────────────
st.set_page_config(
    page_title="Cash Guard — Nakit Hayatta Kalma Motoru",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)
theme.inject_css()

# ══════════════════════════════════════════════════════════════════════════
#  MONTE CARLO — önbellekli sarmalayıcı
# ══════════════════════════════════════════════════════════════════════════
@st.cache_data(show_spinner="Monte Carlo senaryoları koşturuluyor…")
def run_monte_carlo(**kwargs) -> mc.StressResult:
    """StressParams'ı kurup mc.run çağırır. kwargs sayesinde cache anahtarı net."""
    return mc.run(mc.StressParams(**kwargs))


@st.cache_data(show_spinner=False)
def get_cfo_advice(ctx_json: str) -> dict:
    """CFO tavsiyesini bağlam JSON'una göre önbelleğe alır (LLM çağrısını az tutar)."""
    ctx = json.loads(ctx_json)
    advice = RuthlessCFO().advise(ctx)
    return {"text": advice.text, "source": advice.source}


@st.cache_data(show_spinner=False)
def get_pdf(ctx_json: str) -> bytes:
    """PDF raporu bağlama göre önbelleğe alır (her rerun'da yeniden üretmesin)."""
    return build_report(json.loads(ctx_json))


def md_to_html(text: str) -> str:
    """
    Ham HTML kutusu içinde göstermek için: **kalın** -> <b>, satır sonu -> <br>.

    ÖNCE HTML kaçırılır: metin LLM'den veya kullanıcının yüklediği dosyadan
    (şirket/bayi adı) geliyor; '<' içeren bir ad kaçırılmazsa sayfayı bozar.
    Kaçırma bittikten SONRA yalnızca bizim ürettiğimiz etiketler eklenir.
    """
    safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    return html.replace("\n", "<br>")


# ══════════════════════════════════════════════════════════════════════════
#  BAŞLIK
# ══════════════════════════════════════════════════════════════════════════
theme.brand_header()

# ── Sidebar: veri kaynağı ─────────────────────────────────────────────────
st.sidebar.markdown("### 📁 Veri Kaynağı")
upload = st.sidebar.file_uploader(
    "Kendi verini yükle (CSV/Excel) — opsiyonel",
    type=["csv", "xlsx", "xls"],
    help="Biçim A: 'alan,deger' sütunları. Biçim B: month, revenue, fixed_expense, "
         "collections, cash_end sütunlu aylık tablo.",
)
data = parse_uploaded(upload) if upload else load_mock()
if data is None:
    data = load_mock()

sym = "₺" if data.get("currency", "TRY") == "TRY" else "$"

# Not: değerler kullanıcı verisinden geliyor (yüklenen dosyanın adı dahil),
# ham HTML'e girmeden önce theme.esc ile kaçırılır.
st.sidebar.markdown(
    f'<div class="cg-badge">🏭 {theme.esc(data.get("company_name", "—"))}</div>'
    f'<div class="cg-badge">📅 {theme.esc(data.get("as_of", "—"))}</div>',
    unsafe_allow_html=True,
)

# ── Sidebar: kredi senaryosu sürgüleri ────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("### 💳 Yeni Kredi Senaryosu")
# Senaryo adres çubuğundan okunur: paylaşılan bir link AYNI analizi açar.
# Bozuk/aralık dışı parametreler scenario.py içinde kırpıldığı için burada
# güvenle kullanılabilir (bkz. tests/test_scenario.py).
senaryo = scenario.from_query_params(st.query_params)

loan_amount = st.sidebar.slider(
    f"Kredi Miktarı ({sym})", 0, 30_000_000,
    value=senaryo["kredi"], step=500_000, format="%d")
loan_term = st.sidebar.slider("Vade (ay)", 6, 60, value=senaryo["vade"], step=3)
interest = st.sidebar.slider(
    "Aylık Faiz Oranı (%)", 0.0, 8.0, value=senaryo["faiz"], step=0.1) / 100.0

# ── Sidebar: stres değişkenleri ───────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("### 🌪️ Stres Değişkenleri")
income_drop = st.sidebar.slider(
    "Beklenen Gelir Düşüşü (%)", 0, 40, value=senaryo["gelirdus"]) / 100.0
delay_prob = st.sidebar.slider(
    "Tahsilat Gecikme Olasılığı (%)", 0, 80, value=senaryo["gecikme"]) / 100.0
delay_sev = st.sidebar.slider(
    "Geciken Ayda Kayan Tahsilat (%)", 0, 80, value=senaryo["kayan"]) / 100.0
exp_infl = st.sidebar.slider(
    "Gider Artış Oranı (%)", 0, 40, value=senaryo["giderart"]) / 100.0
volatility = st.sidebar.slider(
    "Piyasa Oynaklığı (%)", 5, 40, value=senaryo["oynaklik"]) / 100.0

st.sidebar.markdown("---")
st.sidebar.markdown("### ⚙️ Simülasyon")
n_iter = st.sidebar.select_slider(
    "İterasyon Sayısı", options=[10_000, 20_000, 30_000, 50_000],
    value=senaryo["iterasyon"])

# Sürgülerin GÜNCEL hali adres çubuğuna yazılır; kullanıcı linki kopyalayıp
# paylaşabilir. Yalnızca fark varsa yazıyoruz: query_params'a her koşuda
# yazmak gereksiz yeniden çalıştırma tetikleyebiliyor.
_guncel = {
    "kredi": loan_amount, "vade": loan_term, "faiz": round(interest * 100, 1),
    "gelirdus": round(income_drop * 100), "gecikme": round(delay_prob * 100),
    "kayan": round(delay_sev * 100), "giderart": round(exp_infl * 100),
    "oynaklik": round(volatility * 100), "iterasyon": n_iter,
}
if not scenario.ayni_mi(_guncel, st.query_params):
    st.query_params.clear()
    st.query_params.update(scenario.to_query_params(_guncel))

st.sidebar.caption(
    "🔗 Ayarladığın senaryo adres çubuğunda taşınıyor — linki kopyalayıp "
    "paylaşırsan karşı taraf aynı analizi açar. Sunucuda hiçbir şey saklanmaz."
)

# ── Çekirdek skaler değerler ──────────────────────────────────────────────
# ÖNEMLİ: Nakit modeli "faturalanan gelir"i değil, fiilen TAHSİL EDİLEN nakdi
# (collections) kullanır. Aradaki fark alacaklarda sıkışan paradır ve şirketin
# "kârlı görünüp nakitsiz batma" riskinin kaynağıdır.
current_cash = float(data["current_cash"])
avg_rev = float(data["avg_monthly_revenue"])                 # faturalanan (kâğıt üstü)
avg_collections = float(data.get("avg_monthly_collections", avg_rev))  # fiili nakit girişi
avg_exp = float(data["avg_monthly_fixed_expense"])
debt_service = float(data["existing_monthly_debt_service"])
existing_debt = float(data.get("existing_debt", 0.0))        # mevcut borç STOKU
net_op = avg_collections - avg_exp                           # faaliyet NAKİT akışı


# ══════════════════════════════════════════════════════════════════════════
#  HESAPLAMALAR
# ══════════════════════════════════════════════════════════════════════════
# Modül 1 — kredi simülasyonu (deterministik). Nakit girişi = tahsilat.
scenario = ls.LoanScenario(
    current_cash=current_cash, monthly_revenue=avg_collections,
    monthly_fixed_expense=avg_exp, existing_debt_service=debt_service,
    loan_amount=loan_amount, loan_term_months=loan_term,
    monthly_interest_rate=interest, horizon_months=24,
)
loan_res = ls.simulate(scenario)

# Modül 2 — Monte Carlo. MEVCUT iş modelini (yeni kredi OLMADAN) stres testine
# sokar: "bugünkü halimle 12 ayda batma olasılığım ne?" Kredinin etkisi Modül
# 1'de deterministik olarak, CFO yorumunda ise sözlü olarak ele alınır.
mc_res = run_monte_carlo(
    current_cash=current_cash,
    monthly_revenue=avg_collections, monthly_fixed_expense=avg_exp,
    monthly_debt_service=debt_service,
    income_drop=income_drop, volatility=volatility,
    delay_prob=delay_prob, delay_severity=delay_sev,
    expense_inflation=exp_infl, months=12, n_iter=int(n_iter), seed=42,
)
ruin_pct = mc_res.ruin_probability * 100

# Karşılaştırma senaryosu: AYNI stres, ama krediyi çekmiş varsayarak
# (başta +kredi nakdi, vade boyunca +taksit). Kredi 12 ayı rahatlatıp
# 24 ayda batıran "borç tuzağını" sayısal olarak görünür kılar.
mc_loan = None
if loan_amount > 0:
    mc_loan = run_monte_carlo(
        current_cash=current_cash + loan_amount,
        monthly_revenue=avg_collections, monthly_fixed_expense=avg_exp,
        monthly_debt_service=debt_service + loan_res["installment"],
        income_drop=income_drop, volatility=volatility,
        delay_prob=delay_prob, delay_severity=delay_sev,
        expense_inflation=exp_infl, months=12, n_iter=int(n_iter), seed=42,
    )

# Nakit ömrü (runway): aylık net dış akış negatifse kasa / yakım.
# DİKKAT: Bu STATİK bir hesap — bugünkü yakım hızının sonsuza dek sabit kalacağını
# varsayar. Uygulamanın tezi tam da bunun olmadığı (tahsilat bozuluyor, giderler
# şişiyor). Bu yüzden runway, Monte Carlo'nun stresli beklentisiyle YAN YANA
# gösterilir: aradaki uçurum "statik hesap seni kandırır" mesajının kendisidir.
monthly_net = net_op - debt_service
runway = static_runway(current_cash, monthly_net)
# Trend runway: geçmişteki bozulma eğilimi (Theil–Sen) ileri uzatılır.
trend_rw = trend_runway(data.get("history", []), current_cash, debt_service)


# ══════════════════════════════════════════════════════════════════════════
#  KPI ŞERİDİ
# ══════════════════════════════════════════════════════════════════════════
k1, k2, k3, k4 = st.columns(4)
with k1:
    gap = avg_rev - avg_collections
    st.markdown(theme.kpi_card(
        "Mevcut Kasa", money(current_cash, sym),
        f"Alacak boşluğu: {money(gap, sym)}/ay tahsil edilemiyor",
        accent=COLORS["guardian"],
    ), unsafe_allow_html=True)
with k2:
    st.markdown(theme.kpi_card(
        "12 Ay Batma Olasılığı", f"%{ruin_pct:.1f}",
        f"{tr_num(mc_res.n_iter)} senaryo · {mc_res.acceleration}",
        accent=threat_color(ruin_pct),
    ), unsafe_allow_html=True)
with k3:
    ruin_month = mc_res.expected_ruin_month
    st.markdown(theme.kpi_card(
        "Beklenen İflas Ayı (stresli)",
        f"{ruin_month:.0f}. ay" if ruin_month else "Ufukta yok",
        f"~{ruin_month * 30:.0f}. gün civarı" if ruin_month else "12 ay içinde temerrüt yok",
        accent=COLORS["alarm"] if ruin_month and ruin_month <= 8 else COLORS["amber"]
        if ruin_month else COLORS["guardian"],
    ), unsafe_allow_html=True)
with k4:
    # Statik hesabın yanıltıcılığını kartın kendisinde göster: sabit gidiş vs trend.
    if runway and trend_rw and trend_rw.months:
        net_sub = f"Sabit gidişle ~{runway:.0f} ay · trend sürerse ~{trend_rw.months} ay"
    elif runway:
        net_sub = f"Sabit gidişle ~{runway:.0f} ay ömür"
    else:
        net_sub = "Baz senaryoda pozitif — kasa erimiyor"
    st.markdown(theme.kpi_card(
        "Aylık Net Nakit Akışı",
        f"{money(monthly_net, sym)}",
        net_sub,
        accent=COLORS["alarm"] if monthly_net < 0 else COLORS["guardian"],
    ), unsafe_allow_html=True)

# ── Nakit ömrü merdiveni ──────────────────────────────────────────────────
# Üç hesap üç farklı cevap veriyor ve aradaki uçurum uygulamanın asıl tezi:
# "kasam 42 ay dayanır" diyen statik hesap, bozulmayı ve oynaklığı yok sayıyor.
if runway and trend_rw and trend_rw.months:
    n_hist = len(data.get("history", []))
    stres_notu = (
        f"; Monte Carlo stresi altında beklenen temerrüt **{mc_res.expected_ruin_month:.0f}. ay**"
        if mc_res.expected_ruin_month else "")
    st.caption(
        f"**Nakit ömrü, varsayıma göre üç farklı cevap veriyor.** Bugünkü yakım "
        f"({money(monthly_net, sym)}/ay) sabit kalırsa **~{runway:.0f} ay**; son {n_hist} ayın "
        f"bozulma eğilimi (faaliyet nakdi her ay {money(abs(trend_rw.slope_per_month), sym)} "
        f"geriliyor) sürerse **~{trend_rw.months} ay**{stres_notu}. "
        f"Aradaki fark, statik runway hesabının neden yanılttığıdır."
    )


# ══════════════════════════════════════════════════════════════════════════
#  ŞİRKET RÖNTGENİ — geçmiş trend + gider dağılımı + alacak yaşlandırma
# ══════════════════════════════════════════════════════════════════════════
theme.section("Şirket Röntgeni — Son 12 Ay & Yapısal Görünüm", chip="GENEL BAKIŞ")

hist_df = pd.DataFrame(data.get("history", []))
rx1, rx2 = st.columns([2, 1])

# ── Sol: gelir vs tahsilat (bar) + kasa (çizgi) — TEK ortak ₺ ekseni ──────
# Not: Üçü de aynı birim ve benzer büyüklükte (₺, ~4–7.5M) olduğu için tek
# eksen kullanılıyor. Çift eksen "kasa çizgisi barların üstünde uçuyor" gibi
# yanıltıcı bir görüntü veriyordu; tek eksende "gelir sabitken kasa eriyor"
# kontrastı dürüst ve net okunuyor.
with rx1:
    # Sütunlar yüklenen dosyaya göre eksik olabilir; var olanı çiz, olmayanı atla.
    hcols = set(hist_df.columns)
    if not hist_df.empty and REQUIRED_HISTORY_COLS <= hcols:
        figh = go.Figure()
        figh.add_trace(go.Bar(
            x=hist_df["month"], y=hist_df["revenue"], name="Faturalanan Gelir",
            marker_color="rgba(0,224,164,0.32)",
            hovertemplate="%{x}: " + sym + "%{y:,.0f}<extra>Faturalanan</extra>"))
        if "collections" in hcols:
            figh.add_trace(go.Bar(
                x=hist_df["month"], y=hist_df["collections"], name="Fiili Tahsilat",
                marker_color=COLORS["guardian"],
                hovertemplate="%{x}: " + sym + "%{y:,.0f}<extra>Tahsilat</extra>"))
        if "cash_end" in hcols:
            figh.add_trace(go.Scatter(
                x=hist_df["month"], y=hist_df["cash_end"], name="Ay Sonu Kasa",
                mode="lines+markers", line=dict(color=COLORS["amber"], width=3.5),
                marker=dict(size=7),
                hovertemplate="%{x}: " + sym + "%{y:,.0f}<extra>Kasa</extra>"))
        figh.update_layout(
            template="plotly_dark", height=390, barmode="group",
            title=dict(text="Gelir vs. Tahsilat & Eriyen Kasa", x=0, xanchor="left",
                       y=0.98, font=dict(size=15)),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=40, b=64),
            legend=dict(orientation="h", yanchor="top", y=-0.22, x=0),
            font=dict(color=COLORS["text"]), hovermode="x unified",
            xaxis=dict(gridcolor=COLORS["grid"]),
            yaxis=dict(title=f"Tutar ({sym})", gridcolor=COLORS["grid"], rangemode="tozero"))
        st.plotly_chart(figh, width="stretch")

        # Yorum yazısı VERİDEN türetilir; sabit metin yüklenen veride yalan söylerdi.
        notes = []
        if "collections" in hcols:
            gap_avg = float((hist_df["revenue"] - hist_df["collections"]).mean())
            if gap_avg > 0:
                notes.append(f"Tahsilat, faturalanan gelirin ortalama {money(gap_avg, sym)}/ay "
                             f"altında kalıyor — aradaki boşluk alacaklara takılıyor.")
            elif gap_avg < 0:
                notes.append(f"Tahsilat faturalanan gelirin ortalama {money(abs(gap_avg), sym)}/ay "
                             f"ÜSTÜNDE — geçmiş alacaklar tahsil ediliyor.")
            else:
                notes.append("Faturalanan gelir ile tahsilat birebir örtüşüyor; alacak boşluğu yok.")
        if "cash_end" in hcols and len(hist_df) > 1:
            delta = float(hist_df["cash_end"].iloc[-1] - hist_df["cash_end"].iloc[0])
            if delta < 0:
                notes.append(f"Kasa bu dönemde {money(abs(delta), sym)} eridi.")
            elif delta > 0:
                notes.append(f"Kasa bu dönemde {money(delta, sym)} büyüdü.")
            else:
                notes.append("Kasa dönem başı ve sonunda aynı seviyede.")
        if notes:
            st.caption(" ".join(notes))
    elif not hist_df.empty:
        st.info("Geçmiş tablo okundu ama zorunlu 'month' ve 'revenue' sütunları bulunamadı — "
                "trend grafiği çizilemiyor.")
    else:
        st.info("Geçmiş veri bulunamadı — yüklenen dosyada aylık tablo yok. "
                "KPI'lar ve simülasyonlar ortalama değerlerle çalışmaya devam ediyor.")

# ── Sağ: gider dağılımı (donut) ───────────────────────────────────────────
with rx2:
    breakdown = data.get("expense_breakdown", {})
    if breakdown:
        # Etiketler utils.theme.expense_label'dan: .title() Türkçe'yi bozuyordu
        # ("isletme" -> "Isletme"). CFO metni de aynı haritayı kullanıyor.
        labels = [expense_label(k) for k in breakdown]
        figd = go.Figure(go.Pie(
            labels=labels, values=list(breakdown.values()), hole=0.58,
            marker=dict(colors=["#00E0A4", "#12b5cb", "#4f7cff", "#a06bff", "#ff6ad5"],
                        line=dict(color=COLORS["bg"], width=2)),
            textinfo="percent", textfont=dict(size=12),
            hovertemplate="%{label}: " + sym + "%{value:,.0f} (%{percent})<extra></extra>"))
        figd.update_layout(
            template="plotly_dark", height=360, title="Aylık Gider Dağılımı",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=44, b=10),
            legend=dict(orientation="h", y=-0.1, font=dict(size=11)),
            font=dict(color=COLORS["text"]),
            annotations=[dict(text=money(sum(breakdown.values()), sym), x=0.5, y=0.5,
                              font=dict(size=15, color=COLORS["text"]), showarrow=False)])
        st.plotly_chart(figd, width="stretch")

# ── Alt: alacak yaşlandırma (yatay bar, gecikmeye göre renk) ──────────────
recv = data.get("top_receivables", [])
if recv:
    rdf = pd.DataFrame(recv)
    bar_colors = [COLORS["alarm"] if d >= 90 else COLORS["amber"] if d >= 60
                  else COLORS["guardian_dim"] if d >= 30 else COLORS["guardian"]
                  for d in rdf["overdue_days"]]
    figr = go.Figure(go.Bar(
        x=rdf["amount"], y=rdf["customer"], orientation="h",
        marker=dict(color=bar_colors),
        text=[f"{money(a, sym)} · {d} gün gecikmiş" for a, d in
              zip(rdf["amount"], rdf["overdue_days"])],
        textposition="auto", insidetextfont=dict(color="#06121f"),
        hovertemplate="%{y}: " + sym + "%{x:,.0f}<extra></extra>"))
    figr.update_layout(
        template="plotly_dark", height=270,
        title="Alacak Yaşlandırma — Kim, Ne Kadar, Kaç Gündür Ödemiyor?",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=44, b=10),
        xaxis=dict(title=f"Tutar ({sym})", gridcolor=COLORS["grid"]),
        yaxis=dict(autorange="reversed"), font=dict(color=COLORS["text"]))
    st.plotly_chart(figr, width="stretch")


# ══════════════════════════════════════════════════════════════════════════
#  MODÜL 1 — KREDİ KURTARIR MI?
# ══════════════════════════════════════════════════════════════════════════
theme.section("Kredi Kurtarır mı? — Borç Tuzağı Tahmini", chip="MODÜL 1")
# İki modül bilerek FARKLI ufuklara ve yöntemlere bakıyor; bu ekranda yazmayınca
# "%94 batma" ile "20. ayda iflas" çelişkili görünüyordu. Varsayımı açıkça yaz.
st.caption(
    f"**{scenario.horizon_months} aylık deterministik projeksiyon.** Tahsilat "
    f"({money(avg_collections, sym)}/ay) ve gider ({money(avg_exp, sym)}/ay) sabit "
    f"varsayılır, rastgelelik yoktur. Cevapladığı soru: *kredi çekersem kasa eğrisi "
    f"ne zaman sıfırı deler?* — yani **zamanlama**."
    + (f" Yeni kredi, hâlihazırdaki {money(existing_debt, sym)} borç stokunun "
       f"({money(debt_service, sym)}/ay servis) ÜSTÜNE biner."
       if existing_debt > 0 else "")
)

df1 = loan_res["df"]
fig1 = go.Figure()

# Sıfır çizgisi (temerrüt eşiği)
fig1.add_hline(y=0, line=dict(color=COLORS["alarm"], width=1, dash="dot"),
               annotation_text="TEMERRÜT EŞİĞİ", annotation_position="bottom right",
               annotation_font_color=COLORS["alarm"])

fig1.add_trace(go.Scatter(
    x=df1["month"], y=df1["cash_without_loan"], name="Kredisiz Nakit Akışı",
    line=dict(color=COLORS["muted"], width=2.5, dash="dash"),
    hovertemplate="Ay %{x}: " + sym + "%{y:,.0f}<extra>Kredisiz</extra>"))

fig1.add_trace(go.Scatter(
    x=df1["month"], y=df1["cash_with_loan"], name="Kredili Nakit Akışı",
    line=dict(color=COLORS["guardian"], width=3),
    fill="tozeroy", fillcolor="rgba(0,224,164,0.06)",
    hovertemplate="Ay %{x}: " + sym + "%{y:,.0f}<extra>Kredili</extra>"))

# İflas noktalarını kuru kafa ile işaretle
for key, label, color in [("default_with_loan", "Kredili", COLORS["guardian"]),
                          ("default_without_loan", "Kredisiz", COLORS["muted"])]:
    dm = loan_res[key]
    if dm is not None:
        yval = df1.loc[df1["month"] == dm, "cash_with_loan" if "with" in key
                       else "cash_without_loan"].values
        yv = float(yval[0]) if len(yval) else 0.0
        fig1.add_trace(go.Scatter(
            x=[dm], y=[yv], mode="markers+text", text=["☠"], textfont=dict(size=26),
            marker=dict(size=1, color=color), textposition="middle center",
            name=f"İflas ({label})",
            hovertemplate=f"İFLAS NOKTASI ({label})<br>Ay {dm}<extra></extra>"))

fig1.update_layout(
    template="plotly_dark", height=430,
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=10, r=10, t=20, b=58),
    xaxis=dict(title="Ay", gridcolor=COLORS["grid"], zeroline=False),
    yaxis=dict(title=f"Kasa ({sym})", gridcolor=COLORS["grid"], zeroline=False),
    # Diğer grafiklerle tutarlı: legend altta.
    legend=dict(orientation="h", yanchor="top", y=-0.16, x=0),
    hovermode="x unified", font=dict(color=COLORS["text"]),
)
st.plotly_chart(fig1, width="stretch")

c1, c2, c3 = st.columns(3)
c1.metric("Yeni Aylık Taksit", money(loan_res["installment"], sym))
c2.metric("Vade Sonu Toplam Faiz", money(loan_res["total_interest"], sym))
relief = loan_res["relief_months"]
if loan_amount <= 0:
    c3.metric("Kredinin Etkisi", "—", delta="Senaryo yok", delta_color="off")
elif loan_res["default_with_loan"] is None and loan_res["default_without_loan"] is None \
        and relief == 0:
    # Sağlıklı şirket: hiçbir senaryoda temerrüt yok. "+0 ay · sadece morfin"
    # demek burada yanıltıcıydı.
    c3.metric("Kredinin Etkisi", "Temerrüt yok",
              delta="Her iki senaryoda da güvenli", delta_color="off")
elif relief < 0:
    # Kredi iflası öne çekiyor → borç tuzağı
    c3.metric("Krediyle İflas", f"{abs(relief)} ay erken",
              delta="BORÇ TUZAĞI", delta_color="inverse")
else:
    c3.metric("Kredinin Öteleme Etkisi", f"+{relief} ay",
              delta="Sadece morfin" if relief <= 6 else "Nefes aralığı",
              delta_color="inverse")


# ══════════════════════════════════════════════════════════════════════════
#  MODÜL 2 — MONTE CARLO KASA STRES TESTİ
# ══════════════════════════════════════════════════════════════════════════
theme.section("Monte Carlo Kasa Stres Testi", chip="MODÜL 2")
st.caption(
    f"**12 aylık stokastik simülasyon** — {tr_num(n_iter)} senaryo, her ayın geliri ve "
    f"gideri sürgülerdeki şoklarla rastgele çekilir. Cevapladığı soru: *bugünkü halimle "
    f"batma **olasılığım** ne?* Ufku Modül 1'den kısa (12 ay vs "
    f"{scenario.horizon_months} ay) ve krediyi hesaba katmaz — kredinin etkisi aşağıdaki "
    f"senaryo karşılaştırmasında ayrıca koşulur."
)

# Devasa batma-olasılığı hükmü
vc = threat_color(ruin_pct)
if ruin_pct >= 60:
    verdict_msg = "Bu tablo bir kaza değil, planlanmış bir batıştır. Acil müdahale şart."
elif ruin_pct >= 30:
    verdict_msg = "Tehlike sinyali yanıyor. Nakit üretimini onarmadan yeni yük almayın."
else:
    verdict_msg = "Kasa şimdilik dirençli — ama nakit trendini yakından izleyin."
st.markdown(
    f'<div class="cg-verdict" style="--vc:{vc};">'
    f'<div class="cg-verdict-label">Önümüzdeki 12 Ayda Kasanın Sıfırlanma (Batma) Olasılığı</div>'
    f'<div class="cg-verdict-num">%{ruin_pct:.1f}</div>'
    f'<div class="cg-verdict-msg">{verdict_msg}</div></div>',
    unsafe_allow_html=True,
)

# ── Senaryo karşılaştırması: mevcut hal vs. kredi çekersen ─────────────────
if mc_loan is not None:
    loan_pct = mc_loan.ruin_probability * 100
    st.write("")
    cmp1, cmp2 = st.columns(2)
    with cmp1:
        st.markdown(theme.kpi_card(
            "Mevcut Hal (kredisiz) · 12 ay batma", f"%{ruin_pct:.1f}",
            "Bugünkü iş modelin", accent=threat_color(ruin_pct)),
            unsafe_allow_html=True)
    with cmp2:
        st.markdown(theme.kpi_card(
            f"{money(loan_amount, sym)} Kredi Çekersen · 12 ay batma", f"%{loan_pct:.1f}",
            f"Aylık +{money(loan_res['installment'], sym)} taksit yükü",
            accent=threat_color(loan_pct)),
            unsafe_allow_html=True)

    # Yorum: kredi 12 ayı rahatlatıp 24 ayda batıran tuzak mı?
    relief = loan_res["relief_months"]
    if loan_pct + 1.5 < ruin_pct and relief < 0:
        dwl = loan_res["default_with_loan"]
        dwl_txt = f"~{dwl}. ayda" if dwl else "vade içinde"
        note = (f"⚠️ <b>Klasik borç tuzağı:</b> Kredi 12 aylık batma riskini "
                f"%{ruin_pct:.1f}'den %{loan_pct:.1f}'e düşürüp seni <b>rahatlatıyormuş gibi</b> "
                f"görünüyor. Oysa Modül 1'e bak: aynı kredi iflası <b>{abs(relief)} ay öne "
                f"çekip</b> {dwl_txt} kasayı sıfırlıyor. Kısa vadeli morfin, uzun vadeli intihar.")
        ncolor = COLORS["amber"]
    elif loan_pct >= ruin_pct:
        note = (f"🚨 Kredi durumu <b>12 ayda bile kötüleştiriyor</b> "
                f"(%{ruin_pct:.1f} → %{loan_pct:.1f}). Ne kısa ne uzun vadede kurtarıcı; "
                f"taksit yükü nakit enjeksiyonunu daha baştan yiyor.")
        ncolor = COLORS["alarm"]
    else:
        note = (f"Kredi 12 aylık riski %{ruin_pct:.1f} → %{loan_pct:.1f} yapıyor. "
                f"Yine de Modül 1'deki uzun vadeli etkiyi ve toplam "
                f"{money(loan_res['total_interest'], sym)} faiz yükünü göz ardı etme.")
        ncolor = COLORS["muted"]
    st.markdown(
        f'<div style="border-left:3px solid {ncolor};background:{COLORS["panel"]};'
        f'padding:12px 16px;border-radius:10px;margin-top:12px;font-size:14px;'
        f'color:{COLORS["text"]};">{note}</div>', unsafe_allow_html=True)

st.write("")
mc1, mc2 = st.columns([2, 1])

# ── Fan chart: örnek yollar + yüzdelik bantlar ────────────────────────────
months_axis = np.arange(1, mc_res.sample_paths.shape[1] + 1)
fig2 = go.Figure()

# Binlerce olası çizgiden bir örnek (ince, şeffaf).
# Kaç yol taşınacağı mc.PLOT_SAMPLE_PATHS ile belirleniyor; burada hepsi çizilir.
for path in mc_res.sample_paths:
    fig2.add_trace(go.Scatter(
        x=months_axis, y=path, mode="lines",
        line=dict(color="rgba(138,149,168,0.10)", width=1),
        hoverinfo="skip", showlegend=False))

# p5–p95 ve p25–p75 bantları
pcs = mc_res.percentiles
fig2.add_trace(go.Scatter(x=months_axis, y=pcs["p95"], line=dict(width=0),
                          hoverinfo="skip", showlegend=False))
fig2.add_trace(go.Scatter(x=months_axis, y=pcs["p5"], line=dict(width=0), fill="tonexty",
                          fillcolor="rgba(0,224,164,0.08)", name="%5–%95 aralığı",
                          hoverinfo="skip"))
fig2.add_trace(go.Scatter(x=months_axis, y=pcs["p75"], line=dict(width=0),
                          hoverinfo="skip", showlegend=False))
fig2.add_trace(go.Scatter(x=months_axis, y=pcs["p25"], line=dict(width=0), fill="tonexty",
                          fillcolor="rgba(0,224,164,0.16)", name="%25–%75 aralığı",
                          hoverinfo="skip"))
# Medyan
fig2.add_trace(go.Scatter(x=months_axis, y=pcs["p50"], name="Medyan senaryo",
                          line=dict(color=COLORS["guardian"], width=3),
                          hovertemplate="Ay %{x}: " + sym + "%{y:,.0f}<extra>Medyan</extra>"))
fig2.add_hline(y=0, line=dict(color=COLORS["alarm"], width=1.5, dash="dot"),
               annotation_text="BATMA EŞİĞİ", annotation_font_color=COLORS["alarm"])

fig2.update_layout(
    template="plotly_dark", height=440,
    # Başlık sol-üstte, legend ALTA (başlık/legend çakışmasını önler).
    title=dict(text="12 Aylık Olası Nakit Yolları", x=0, xanchor="left", y=0.98,
               font=dict(size=15)),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=10, r=10, t=40, b=58),
    xaxis=dict(title="Ay", gridcolor=COLORS["grid"], zeroline=False),
    yaxis=dict(title=f"Kasa ({sym})", gridcolor=COLORS["grid"], zeroline=False),
    legend=dict(orientation="h", yanchor="top", y=-0.16, x=0),
    font=dict(color=COLORS["text"]),
)
mc1.plotly_chart(fig2, width="stretch")

# ── İflas ayı dağılımı ────────────────────────────────────────────────────
hist = mc_res.ruin_month_hist
fig3 = go.Figure(go.Bar(
    x=[f"{i+1}. ay" for i in range(len(hist))], y=hist,
    marker=dict(color=COLORS["alarm"], line=dict(width=0)),
    hovertemplate="%{x}: %{y:,.0f} senaryo<extra></extra>"))
fig3.update_layout(
    template="plotly_dark", height=430, title="Batışların Aya Göre Dağılımı",
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=10, r=10, t=44, b=10),
    xaxis=dict(gridcolor=COLORS["grid"], tickangle=-45),
    yaxis=dict(title="Batan senaryo sayısı", gridcolor=COLORS["grid"]),
    font=dict(color=COLORS["text"]),
)
mc2.plotly_chart(fig3, width="stretch")

s1, s2, s3 = st.columns(3)
s1.metric("Medyan 12. Ay Kasa", money(mc_res.median_end_cash, sym))
s2.metric("Kötü Senaryo (p5) 12. Ay", money(mc_res.p5_end_cash, sym),
          delta_color="inverse")
s3.metric("Beklenen İflas Ayı",
          f"{mc_res.expected_ruin_month:.1f}" if mc_res.expected_ruin_month else "—")


# ══════════════════════════════════════════════════════════════════════════
#  MODÜL 3 — ACIMASIZ CFO AJANI
# ══════════════════════════════════════════════════════════════════════════
theme.section("Acımasız CFO Ajanı — Aksiyon Planı", chip="MODÜL 3")

# CFO'ya verilecek analiz bağlamı
cfo_ctx = {
    "company_name": data.get("company_name", "Şirket"),
    "currency_symbol": sym,
    "current_cash": current_cash,
    "net_operating": net_op,
    "debt_service": debt_service,
    "monthly_net": monthly_net,
    "runway_months": round(runway, 1) if runway else None,
    "trend_runway_months": trend_rw.months if trend_rw else None,
    "trend_slope": round(trend_rw.slope_per_month) if trend_rw else None,
    "ruin_probability": mc_res.ruin_probability,
    "expected_ruin_month": mc_res.expected_ruin_month,
    "loan_amount": loan_amount,
    "installment": loan_res["installment"],
    "total_interest": loan_res["total_interest"],
    "relief_months": loan_res["relief_months"],
    "default_with_loan": loan_res["default_with_loan"],
    "default_without_loan": loan_res["default_without_loan"],
    "top_receivables": data.get("top_receivables", []),
    "expense_breakdown": data.get("expense_breakdown", {}),
}

# "Yeniden çağır" yalnızca GERÇEK bir LLM varsa anlamlı: kural tabanlı motor
# deterministiktir, aynı sayılarla harfi harfine aynı planı üretir. Buton eskiden
# her koşulda etkindi ve tıklayınca hiçbir şey değişmediği için bozuk görünüyordu.
llm_engines = RuthlessCFO().available_llms()
colb, cols = st.columns([1, 3])
regen = colb.button(
    "🔁 CFO'yu Yeniden Çağır", width="stretch", disabled=not llm_engines,
    help=("Aynı sayılarla LLM'i yeniden çalıştırır; ifade farklılaşır."
          if llm_engines else
          "Kural tabanlı motor deterministiktir — aynı sayılar aynı planı üretir, "
          "yeniden çağırmak metni değiştirmez. Farklı bir yorum için "
          "ANTHROPIC_API_KEY, OPENAI_API_KEY veya GOOGLE_API_KEY tanımlayın."),
)
if regen:
    get_cfo_advice.clear()  # önbelleği temizle, tazele

advice = get_cfo_advice(json.dumps(cfo_ctx, ensure_ascii=False, sort_keys=True))
cols.markdown(
    f'<span class="cg-badge">Kaynak: {advice["source"]}</span>'
    + ("" if llm_engines else
       '<span class="cg-badge">Deterministik — aynı sayılar, aynı plan</span>'),
    unsafe_allow_html=True,
)

# Markdown'ı CFO kutusunda göster (** -> <b>, satır sonları -> <br>)
st.markdown(f'<div class="cg-cfo">{md_to_html(advice["text"])}</div>', unsafe_allow_html=True)

# ── PDF rapor indirme ─────────────────────────────────────────────────────
report_ctx = {
    "company_name": data.get("company_name", "Şirket"),
    "sector": data.get("sector"),
    "as_of": data.get("as_of"),
    "currency_symbol": sym,
    "current_cash": current_cash,
    "ruin_pct": ruin_pct,
    "expected_ruin_month": mc_res.expected_ruin_month,
    "monthly_net": monthly_net,
    "net_operating": net_op,
    "debt_service": debt_service,
    "runway_months": round(runway, 1) if runway else None,
    "trend_runway_months": trend_rw.months if trend_rw else None,
    "loan_amount": loan_amount,
    "installment": loan_res["installment"],
    "total_interest": loan_res["total_interest"],
    "relief_months": loan_res["relief_months"],
    "default_with_loan": loan_res["default_with_loan"],
    "base_ruin_pct": ruin_pct,
    "loan_ruin_pct": (mc_loan.ruin_probability * 100) if mc_loan else None,
    "cfo_text": advice["text"],
    "cfo_source": advice["source"],
}
pdf_bytes = get_pdf(json.dumps(report_ctx, ensure_ascii=False, sort_keys=True))
st.write("")
dl1, dl2 = st.columns([1, 3])
dl1.download_button(
    "📄 Yönetim Raporunu İndir (PDF)", data=pdf_bytes,
    file_name=f"CashGuard_Rapor_{data.get('as_of','')}.pdf",
    mime="application/pdf", width="stretch",
)
dl2.caption("Yönetici özeti + kredi analizi + senaryo karşılaştırması + CFO aksiyon planı "
            "tek sayfalık kurumsal PDF olarak. Yönetim kuruluna sunulacak kıvamda.")

st.markdown(
    f'<div style="margin-top:26px;color:{COLORS["muted"]};font-size:12px;text-align:center;">'
    "🛡️ Cash Guard · Bu bir karar-destek prototipidir (PoC), yatırım/finans tavsiyesi değildir."
    "</div>",
    unsafe_allow_html=True,
)
