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
from modules import receivables
from modules import scenario
from modules import sensitivity
from modules import store
from modules import weekly
from modules import zscore
from modules.ai_cfo import RuthlessCFO
from modules import data_io
from modules.data_io import (REQUIRED_HISTORY_COLS, load_mock,
                             parse_uploaded_files)
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


@st.cache_data(show_spinner="Sürgülerin etkisi tek tek ölçülüyor…")
def run_tornado(**kwargs) -> sensitivity.TornadoResult:
    """
    Duyarlılık analizini önbelleğe alır. Maliyeti tek Monte Carlo'nun ~11 katı
    (her sürgü için iki uç + taban), o yüzden sürgü kıpırdadıkça yeniden
    koşmaması önemli.
    """
    return sensitivity.tornado(mc.StressParams(**kwargs))


@st.cache_data(show_spinner=False)
def get_cfo_advice(ctx_json: str, motorlar: str) -> dict:
    """
    CFO tavsiyesini bağlam JSON'una göre önbelleğe alır (LLM çağrısını az tutar).

    `motorlar` hesapta kullanılmaz; yalnızca önbellek anahtarına girsin diye var.
    Olmazsa şu olur: anahtarsız açılışta kural tabanlı cevap önbelleğe yazılır,
    sonra Secrets'a anahtar eklenir ve cevap DEĞİŞMEZ — çünkü senaryo sayıları
    aynıdır. Kullanıcı anahtarı doğru koymuştur ama hiçbir şey olmaz; sağlayıcı
    kümesi de anahtarın parçası olunca bu sessiz takılma ortadan kalkar.
    """
    ctx = json.loads(ctx_json)
    advice = RuthlessCFO().advise(ctx)
    return {"text": advice.text, "source": advice.source, "reason": advice.reason}


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
    accept_multiple_files=True,
    help="Birden fazla dosya yükleyebilirsin; hepsi tek şirkette birleşir. "
         "Biçim A: 'alan,deger'. Biçim B: month, revenue, fixed_expense, "
         "collections, cash_end. Biçim C: musteri, tutar, gecikme_gun.",
)

# Şablon, yükleyicinin hemen altında duruyor: biçimi öğrenmek için README'ye
# gitmek zorunda kalan kullanıcı çoğu zaman hiç denemiyor. İçerik data_io'nun
# gerçekten okuduğu alan adlarından üretiliyor — elle yazılan bir örnek, kod
# değişince sessizce yanlışa döner.
#
# `from modules.data_io import ornek_sablon` YAZILMIYOR, bilerek: Streamlit
# Cloud yeni commit'te script'i yeniden çalıştırırken sys.modules'teki eski
# modül nesnesini koruyabiliyor. O durumda modüle YENİ eklenen bir isim
# bulunamaz ve import satırı patlar — yani tek bir yeni fonksiyon, tüm
# uygulamayı ölü bir hata sayfasına çevirir (bir kez oldu). Modülün kendisini
# alıp özelliği yoklayınca, en kötü ihtimalle bu buton görünmez; uygulama ayakta
# kalır ve bir sonraki yeniden başlatmada kendiliğinden düzelir.
_sablon_uret = getattr(data_io, "ornek_sablon", None)
_alacak_sablon_uret = getattr(data_io, "ornek_alacak_sablonu", None)
if _sablon_uret is not None:
    sb1, sb2 = st.sidebar.columns(2)
    sb1.download_button(
        "⬇️ Şablon",
        data=_sablon_uret(),
        file_name="cash_guard_sablon.csv",
        mime="text/csv",
        width="stretch",
        help="Skalerler + bilanço + gider dağılımı. İndir, doldur, yükle.",
    )
    if _alacak_sablon_uret is not None:
        sb2.download_button(
            "⬇️ Alacaklar",
            data=_alacak_sablon_uret(),
            file_name="cash_guard_alacaklar.csv",
            mime="text/csv",
            width="stretch",
            help="Alacak yaşlandırma listesi. Diğer şablonla BİRLİKTE yüklenebilir.",
        )

data = parse_uploaded_files(upload) if upload else load_mock()
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
# Adı bilerek `scenario` DEĞİL: o ad `modules.scenario` modülüne ait ve burada
# yeniden bağlanınca modül bu satırdan sonra erişilemez hâle geliyordu.
loan_scn = ls.LoanScenario(
    current_cash=current_cash, monthly_revenue=avg_collections,
    monthly_fixed_expense=avg_exp, existing_debt_service=debt_service,
    loan_amount=loan_amount, loan_term_months=loan_term,
    monthly_interest_rate=interest, horizon_months=24,
)
loan_res = ls.simulate(loan_scn)

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

# ── Altman Z-score: bilançonun kendi verdiği hüküm ────────────────────────
# Uygulamanın bütün hesapları nakde bakar. Altman ise tahakkuk esaslı yıllık bir
# fotoğraf çeker ve muhasebe temelli iflas modellerinin en çok test edilmişidir.
# İkisini yan yana koymanın değeri aynı şeyi söylemeleri değil, SÖYLEMEMELERİ:
# demo şirketinde Altman "güvenli" derken nakit modeli %94 batma diyor.
zres = zscore.from_company(data)
if zres.available:
    zc1, zc2 = st.columns([1, 1])

    zone_color = {zscore.ZONE_SAFE: COLORS["guardian"],
                  zscore.ZONE_GREY: COLORS["amber"],
                  zscore.ZONE_DISTRESS: COLORS["alarm"]}[zres.zone]

    with zc1:
        # Gösterge, skoru üç bölgenin üzerine oturtur: tek başına "3.02" hiçbir
        # şey ifade etmiyor, sınırlara göre nerede durduğu ifade ediyor.
        ust = max(6.0, zres.score * 1.25)
        alt = min(-1.0, zres.score - 1.0)
        figz = go.Figure(go.Indicator(
            mode="gauge+number",
            value=zres.score,
            number=dict(font=dict(size=42, color=zone_color), valueformat=".2f"),
            gauge=dict(
                axis=dict(range=[alt, ust], tickcolor=COLORS["muted"]),
                bar=dict(color=zone_color, thickness=0.28),
                bgcolor="rgba(0,0,0,0)", borderwidth=0,
                steps=[
                    dict(range=[alt, zres.distress_below],
                         color="rgba(255,59,71,0.28)"),
                    dict(range=[zres.distress_below, zres.safe_above],
                         color="rgba(255,176,32,0.24)"),
                    dict(range=[zres.safe_above, ust],
                         color="rgba(0,224,164,0.20)"),
                ],
            ),
        ))
        figz.update_layout(
            template="plotly_dark", height=250,
            title=dict(text=f"{zres.model_name} — {zres.zone} bölge",
                       x=0, xanchor="left", font=dict(size=14)),
            paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=20, r=20, t=46, b=6),
            font=dict(color=COLORS["text"]))
        st.plotly_chart(figz, width="stretch")

    with zc2:
        # Bileşen dökümü: skoru hangi oranın taşıdığı görünmezse sayı bir
        # kehanet gibi durur. Toplamları skora eşit (testle korunuyor).
        comps = sorted(zres.components, key=lambda c: c.contribution)
        figzc = go.Figure(go.Bar(
            x=[c.contribution for c in comps],
            y=[c.label for c in comps], orientation="h",
            marker=dict(color=[COLORS["guardian"] if c.contribution >= 0
                               else COLORS["alarm"] for c in comps],
                        line=dict(width=0)),
            customdata=[[c.ratio, c.weight, c.explain] for c in comps],
            hovertemplate=("<b>%{y}</b><br>Oran: %{customdata[0]:.3f} × katsayı "
                           "%{customdata[1]:.3f}<br>Skora katkısı: %{x:.3f}"
                           "<br><i>%{customdata[2]}</i><extra></extra>")))
        figzc.update_layout(
            template="plotly_dark", height=250,
            title=dict(text="Skoru kim taşıyor?", x=0, xanchor="left",
                       font=dict(size=14)),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=46, b=10),
            xaxis=dict(title="Skora katkı", gridcolor=COLORS["grid"]),
            yaxis=dict(tickfont=dict(size=10)), font=dict(color=COLORS["text"]))
        st.plotly_chart(figzc, width="stretch")

    # İki modelin farklı konuşması bir arıza değil; asıl mesaj bu.
    if zres.zone == zscore.ZONE_SAFE and ruin_pct >= 60:
        st.markdown(
            f'<div style="border-left:3px solid {COLORS["amber"]};'
            f'background:{COLORS["panel"]};padding:12px 16px;border-radius:10px;'
            f'font-size:14px;color:{COLORS["text"]};">'
            f'🧩 <b>İki model çelişiyor ve ikisi de haklı.</b> Altman bilançoya '
            f'bakıp <b>{zres.score:.2f} — güvenli bölge</b> diyor: şirket kâğıt '
            f'üstünde kârlı, özkaynağı sağlam. Nakit modeli aynı şirket için '
            f'<b>%{ruin_pct:.1f} batma</b> diyor. Fark yöntemde: Altman tahakkuk '
            f'esaslı <b>yıllık bir fotoğraf</b> çeker, nakdin <b>ne zaman</b> '
            f'geldiğini görmez. Şirketler tam olarak böyle batar — kârlı '
            f'görünerek, nakitsiz.'
            f'</div>', unsafe_allow_html=True)
    else:
        st.caption(
            f"**{zres.model_name}.** {zres.model_fits} için kalibre edilmiştir. "
            f"Bölge sınırları modelin kendi eşikleri: güvenli > {zres.safe_above}, "
            f"tehlike < {zres.distress_below}. Skor bir olasılık değildir — "
            f"şirketi tarihsel olarak batanlarla aynı bölgeye düşürüp "
            f"düşürmediğini söyler."
        )

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

# ── Yaşlandırmayı nakit modeline bağla ────────────────────────────────────
# Yaşlandırma bugüne kadar sadece bir grafikti: ekranda duruyor, hiçbir hesabı
# beslemiyordu. Gecikme tek bir sürgüyle giriliyordu — yani 92 gün gecikmiş
# 3,15 milyonluk müşteri ile zamanında ödeyen bir defter aynı sürgüye düşüyordu.
aging = receivables.age(
    recv,
    total_outstanding=data.get("receivables_outstanding"),
    monthly_revenue=avg_rev,                       # DSO faturalanan gelire göre
    declared_collection_days=data.get("avg_collection_days"),
)

if aging.total > 0:
    a1, a2, a3 = st.columns(3)
    with a1:
        st.markdown(theme.kpi_card(
            "Alacak Devir Günü (DSO)",
            f"{aging.dso:.0f} gün" if aging.dso else "—",
            f"Bakiye {money(aging.total, sym)} ÷ aylık faturalanan gelir",
            accent=COLORS["alarm"] if aging.dso and aging.dso > 60
            else COLORS["amber"] if aging.dso and aging.dso > 45 else COLORS["guardian"],
        ), unsafe_allow_html=True)
    with a2:
        st.markdown(theme.kpi_card(
            "Muhtemelen Hiç Gelmeyecek",
            money(aging.expected_loss, sym),
            f"Defterin %{aging.expected_loss_share * 100:.0f}'i — yaşlandırma "
            f"karşılık oranlarıyla",
            accent=COLORS["alarm"],
        ), unsafe_allow_html=True)
    with a3:
        st.markdown(theme.kpi_card(
            "Vadesi Geçmiş Pay",
            f"%{aging.overdue_share * 100:.0f}",
            f"Ağırlıklı ortalama {aging.weighted_overdue_days:.0f} gün gecikme",
            accent=threat_color(aging.overdue_share * 100),
        ), unsafe_allow_html=True)

    st.caption(
        "**Gecikme ≠ kayıp.** Geciken para sonunda gelir, şüpheli alacak hiç "
        "gelmez. Yukarıdaki tahmin, kova başına yerleşik karşılık oranlarıyla "
        "(vadesinde %2 → 90+ gün %50) hesaplanır; kendi tahsilat geçmişin varsa "
        "bu oranlar değiştirilmelidir."
    )

    # Yaşlandırma ile bakiye birbirini tutmuyorsa sus-pus geçme.
    if aging.dso_conflict:
        st.warning(
            f"⚠️ **Veri tutarsızlığı:** Yaşlandırma listesi alacakların ortalama "
            f"**{aging.weighted_overdue_days:.0f} gün** vadesini aştığını söylüyor, "
            f"ama bakiyeden hesaplanan DSO yalnızca **{aging.dso:.0f} gün**. İkisi "
            f"aynı anda doğru olamaz: ya bakiye eksik ya da yaşlandırma listesi "
            f"defterin tamamını temsil etmiyor. Aşağıdaki türetilmiş sürgüleri "
            f"kullanmadan önce bu farkı çöz."
        )

    # ── Sürgü karşılıkları + tek tıkla uygula ─────────────────────────────
    imp = receivables.implied_stress(aging)
    imp_gecikme, imp_kayan = imp.as_slider_percents
    farkli = (imp_gecikme, imp_kayan) != (senaryo["gecikme"], senaryo["kayan"])

    u1, u2 = st.columns([3, 1])
    with u1:
        st.markdown(
            f'<div style="border-left:3px solid {COLORS["guardian"]};'
            f'background:{COLORS["panel"]};padding:12px 16px;border-radius:10px;'
            f'font-size:14px;color:{COLORS["text"]};">'
            f'📐 <b>Yaşlandırmadan türetilen gecikme profili:</b> bu defterle bir '
            f'ayın tahsilatının <b>%{imp.expected_slip_rate * 100:.0f}</b>\'inin '
            f'gelecek aydan sonraya sarkması bekleniyor. Sürgü karşılığı: '
            f'gecikme olasılığı <b>%{imp_gecikme}</b>, kayan tahsilat '
            f'<b>%{imp_kayan}</b> (şu an %{senaryo["gecikme"]} / '
            f'%{senaryo["kayan"]}).'
            f'</div>', unsafe_allow_html=True)
    with u2:
        st.write("")
        if st.button("📐 Sürgülere uygula", width="stretch", disabled=not farkli,
                     help="Stres sürgülerini yaşlandırmadan türetilen değerlere çeker"):
            hedef = dict(senaryo, gecikme=imp_gecikme, kayan=imp_kayan)
            st.query_params.clear()
            st.query_params.update(scenario.to_query_params(hedef))
            st.rerun()

    if imp.clamped:
        st.caption(
            f"⚠️ Türetilen kayma (%{imp.expected_slip_rate * 100:.0f}) sürgülerin "
            f"taşıyabileceğinin (%{imp.achievable_slip_rate * 100:.0f}) üstünde; "
            f"değerler tavana kırpıldı. Yani sürgüleri uygulasan bile simülasyon "
            f"bu defterden **daha iyimser** kalır."
        )


# ══════════════════════════════════════════════════════════════════════════
#  MODÜL 1 — KREDİ KURTARIR MI?
# ══════════════════════════════════════════════════════════════════════════
theme.section("Kredi Kurtarır mı? — Borç Tuzağı Tahmini", chip="MODÜL 1")
# İki modül bilerek FARKLI ufuklara ve yöntemlere bakıyor; bu ekranda yazmayınca
# "%94 batma" ile "20. ayda iflas" çelişkili görünüyordu. Varsayımı açıkça yaz.
st.caption(
    f"**{loan_scn.horizon_months} aylık deterministik projeksiyon.** Tahsilat "
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
#  13 HAFTALIK LİKİDİTE UFKU — ay-içi nakit çukurları
# ══════════════════════════════════════════════════════════════════════════
# Modül 1 ve 2 AYLIK bakar; aylık ortalama, ayın en kritik gününü ortalamanın
# altında saklar. Ay artıda kapansa bile maaş günü kasa dibi görebilir.
# Kurumsal likidite kontrolünün fiili standardı bu yüzden 13 haftalık tablodur.
theme.section("13 Haftalık Likidite Ufku — Ayın Hangi Günü Dara Düşüyorsun?",
              chip="HAFTALIK")

hafta_basi = weekly.parse_start(data.get("as_of"))
wk = weekly.build(
    current_cash=current_cash,
    monthly_collections=avg_collections,
    expense_breakdown=data.get("expense_breakdown"),
    monthly_fixed_expense=avg_exp,
    monthly_debt_service=debt_service,
    start=hafta_basi,
)
# Kredili karşılığı: baştan +kredi nakdi, her ay +taksit yükü.
wk_loan = weekly.build(
    current_cash=current_cash + loan_amount,
    monthly_collections=avg_collections,
    expense_breakdown=data.get("expense_breakdown"),
    monthly_fixed_expense=avg_exp,
    monthly_debt_service=debt_service + loan_res["installment"],
    start=hafta_basi,
) if loan_amount > 0 else None

st.caption(
    f"**{len(wk.weeks)} haftalık nakit takvimi** ({wk.weeks[0].start:%d.%m.%Y} – "
    f"{wk.weeks[-1].end:%d.%m.%Y}). Aylık toplamlar güne dağıtılır: kira ayın "
    f"1'i, maaş 5'i, kredi taksiti 15'i, enerji/lojistik 20'si; hammadde, "
    f"pazarlama ve **tahsilat** aya eşit yayılır. Tahsilatın yayılması bilinçli "
    f"olarak nötr bir varsayımdır — gerçek tahsilat takvimi bilinmiyorken onu "
    f"tek güne yığmak, olmayan bir bilgiyi varmış gibi göstermek olurdu."
)

if not wk.informative:
    # Her şey aya yayılmışsa haftalık eğri, aylık çizginin ince çizilmiş hâlidir.
    st.info(
        "ℹ️ Yüklediğin veride gider dağılımı yok, bu yüzden tüm çıkışlar aya "
        "eşit yayıldı. Aşağıdaki eğri aylık çizginin daha ince çizilmiş hâli — "
        "**ay-içi çukur bilgisi taşımıyor.** Maaş/kira gibi kalemleri ayrı ayrı "
        "verirsen bu tablo asıl işini yapar."
    )

dip = wk.min_week
etiketler = [f"H{w.index}<br>{w.start:%d.%m}" for w in wk.weeks]

figw = go.Figure()
figw.add_trace(go.Bar(
    x=etiketler, y=[w.inflow for w in wk.weeks], name="Haftalık tahsilat",
    marker_color="rgba(0,224,164,0.32)",
    hovertemplate="%{x}: " + sym + "%{y:,.0f}<extra>Giriş</extra>"))
figw.add_trace(go.Bar(
    x=etiketler, y=[-w.outflow for w in wk.weeks], name="Haftalık çıkış",
    marker_color="rgba(255,59,71,0.38)",
    hovertemplate="%{x}: " + sym + "%{y:,.0f}<extra>Çıkış</extra>"))
figw.add_trace(go.Scatter(
    x=etiketler, y=[w.closing_cash for w in wk.weeks], name="Hafta sonu kasa",
    mode="lines+markers", line=dict(color=COLORS["guardian"], width=3),
    hovertemplate="%{x}: " + sym + "%{y:,.0f}<extra>Kasa</extra>"))
if wk_loan is not None:
    figw.add_trace(go.Scatter(
        x=etiketler, y=[w.closing_cash for w in wk_loan.weeks],
        name=f"{money(loan_amount, sym)} kredi çekilirse", mode="lines",
        line=dict(color=COLORS["amber"], width=2, dash="dash"),
        hovertemplate="%{x}: " + sym + "%{y:,.0f}<extra>Kredili</extra>"))
figw.add_hline(y=0, line=dict(color=COLORS["alarm"], width=1.5, dash="dot"))
if dip is not None:
    figw.add_annotation(
        x=f"H{dip.index}<br>{dip.start:%d.%m}", y=dip.closing_cash,
        text=f"en dip: {money(dip.closing_cash, sym)}",
        showarrow=True, arrowhead=2, ay=38, arrowcolor=COLORS["amber"],
        font=dict(color=COLORS["amber"], size=12))
figw.update_layout(
    template="plotly_dark", height=420, barmode="relative",
    title=dict(text="Haftalık nakit hareketi ve kasa yolu", x=0, xanchor="left",
               font=dict(size=15)),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=10, r=10, t=44, b=60),
    xaxis=dict(gridcolor=COLORS["grid"], tickfont=dict(size=10)),
    yaxis=dict(title=f"Tutar ({sym})", gridcolor=COLORS["grid"], zeroline=False),
    legend=dict(orientation="h", yanchor="top", y=-0.18, x=0),
    font=dict(color=COLORS["text"]))
st.plotly_chart(figw, width="stretch")

w1, w2, w3 = st.columns(3)
with w1:
    st.markdown(theme.kpi_card(
        "En Dip Hafta", f"H{dip.index} · {dip.start:%d.%m}" if dip else "—",
        f"Hafta sonu kasa {money(dip.closing_cash, sym)}" if dip else "",
        accent=COLORS["alarm"] if dip and dip.closing_cash < 0
        else COLORS["amber"] if dip and dip.closing_cash < current_cash * 0.5
        else COLORS["guardian"]), unsafe_allow_html=True)
with w2:
    st.markdown(theme.kpi_card(
        "Aylık Modelin Göremediği Derinlik", money(wk.intramonth_gap, sym),
        "Dönem sonu kasası ile en dip hafta arasındaki fark",
        accent=COLORS["amber"]), unsafe_allow_html=True)
with w3:
    ilk_eksi = wk.first_negative
    st.markdown(theme.kpi_card(
        "13 Hafta İçinde Kasa Eksiye Düşüyor mu?",
        f"Evet · H{ilk_eksi.index}" if ilk_eksi else "Hayır",
        f"{ilk_eksi.start:%d.%m.%Y} haftası" if ilk_eksi
        else "Bu ufukta nakit tükenmiyor",
        accent=COLORS["alarm"] if ilk_eksi else COLORS["guardian"]),
        unsafe_allow_html=True)

if wk.informative and dip is not None:
    st.markdown(
        f'<div style="border-left:3px solid {COLORS["amber"]};'
        f'background:{COLORS["panel"]};padding:12px 16px;border-radius:10px;'
        f'margin-top:12px;font-size:14px;color:{COLORS["text"]};">'
        f'📅 <b>Aylık bakınca görünmeyen an.</b> Dönem sonunda kasa '
        f'<b>{money(wk.end_cash, sym)}</b> görünüyor, ama {dip.start:%d.%m.%Y} '
        f'haftasında <b>{money(dip.closing_cash, sym)}</b>\'ye iniyor — aradaki '
        f'<b>{money(wk.intramonth_gap, sym)}</b> aylık ortalamanın içinde kaybolan '
        f'gerçek bir daralmadır. Kredi limiti, teminat ve tedarikçi vadesi '
        f'pazarlıkları ay sonuna değil <b>bu haftaya</b> göre kurulmalı.'
        f'</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════
#  MODÜL 2 — MONTE CARLO KASA STRES TESTİ
# ══════════════════════════════════════════════════════════════════════════
theme.section("Monte Carlo Kasa Stres Testi", chip="MODÜL 2")
st.caption(
    f"**12 aylık stokastik simülasyon** — {tr_num(n_iter)} senaryo, her ayın geliri ve "
    f"gideri sürgülerdeki şoklarla rastgele çekilir. Cevapladığı soru: *bugünkü halimle "
    f"batma **olasılığım** ne?* Ufku Modül 1'den kısa (12 ay vs "
    f"{loan_scn.horizon_months} ay) ve krediyi hesaba katmaz — kredinin etkisi aşağıdaki "
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
#  DUYARLILIK (TORNADO) — hangi sürgü manşeti oynatıyor?
# ══════════════════════════════════════════════════════════════════════════
# Beş sürgüyü aynı anda oynatan kullanıcı, %94.3'ü hangisinin yaptığını göremez.
# Tornado tam olarak bunu söyler ve "neyi düzeltirsem ne kazanırım" sorusunu
# manşet sayıdan daha eylemli hâle getirir.
theme.section("Neyi Düzeltirsen Ne Kazanırsın — Duyarlılık Analizi", chip="TORNADO")

tor = run_tornado(
    current_cash=current_cash,
    monthly_revenue=avg_collections, monthly_fixed_expense=avg_exp,
    monthly_debt_service=debt_service,
    income_drop=income_drop, volatility=volatility,
    delay_prob=delay_prob, delay_severity=delay_sev,
    expense_inflation=exp_infl, months=12, n_iter=int(n_iter), seed=42,
)
delta_pp = round(tor.delta * 100)
st.caption(
    f"Her stres sürgüsü tek tek **±{delta_pp} puan** oynatılıp diğerleri sabit "
    f"tutuldu; barlar batma olasılığının nereye gittiğini gösteriyor. Bütün "
    f"koşular **aynı rastgele tohumu** paylaşır — aksi hâlde ölçülen fark "
    f"parametreden mi Monte Carlo gürültüsünden mi geldiği ayırt edilemezdi."
)

base_pct = tor.base_probability * 100
# Plotly yatay barları alttan yukarı dizer; en etkili sürgü ÜSTTE dursun diye ters.
imp = list(reversed(tor.impacts))


def _tornado_side(values, label):
    """Tabanın bir yanındaki barları (aşağı uç ya da yukarı uç) çizer."""
    deltas = [v * 100 - base_pct for v in values]
    return go.Bar(
        y=[i.label for i in imp], x=deltas, base=base_pct, orientation="h",
        name=label, showlegend=False,
        marker=dict(color=[COLORS["alarm"] if d > 0 else COLORS["guardian"]
                           for d in deltas], line=dict(width=0)),
        customdata=[[v * 100, d] for v, d in zip(values, deltas)],
        hovertemplate=("<b>%{y}</b><br>Batma olasılığı: %%{customdata[0]:.1f}"
                       "<br>Tabana göre: %{customdata[1]:+.1f} puan<extra></extra>"),
    )


figt = go.Figure()
figt.add_trace(_tornado_side([i.low_probability for i in imp], "aşağı uç"))
figt.add_trace(_tornado_side([i.high_probability for i in imp], "yukarı uç"))
figt.add_vline(x=base_pct, line=dict(color=COLORS["text"], width=1.5, dash="dot"),
               annotation_text=f"bugün: %{base_pct:.1f}",
               annotation_font_color=COLORS["muted"])
figt.update_layout(
    template="plotly_dark", height=330, barmode="overlay",
    title=dict(text=f"Sürgü başına ±{delta_pp} puanın batma olasılığına etkisi",
               x=0, xanchor="left", font=dict(size=15)),
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=10, r=10, t=44, b=10),
    xaxis=dict(title="12 ay içinde batma olasılığı (%)", gridcolor=COLORS["grid"],
               ticksuffix="%", zeroline=False),
    yaxis=dict(gridcolor="rgba(0,0,0,0)"),
    font=dict(color=COLORS["text"]),
)
st.plotly_chart(figt, width="stretch")

# ── Grafiğin sözlü karşılığı: grafik okumayan da cevabı alsın ─────────────
top = tor.top
if top is None:
    st.info(
        "Bu ayarlarda hiçbir sürgü batma olasılığını anlamlı biçimde "
        "oynatmıyor — sonuç sürgülerden değil, şirketin yapısal nakit "
        "açığından geliyor. Kaldıraç stres varsayımlarında değil, "
        "tahsilat/gider tarafında."
    )
else:
    yon = "artırıyor" if top.swing > 0 else "düşürüyor"
    tsat = ("<b>Ters yönlü ve bu bir hata değil:</b> taban senaryo zaten derin "
            "zararda olduğu için oynaklığın artması bazı yollara toparlanma "
            "şansı verir; kasa suyun çok altındayken dalga boyu büyüdükçe "
            "yüzeye çıkan senaryo sayısı artar. "
            if top.key == "volatility" and top.swing < 0 else "")
    st.markdown(
        f'<div style="border-left:3px solid {COLORS["amber"]};'
        f'background:{COLORS["panel"]};padding:12px 16px;border-radius:10px;'
        f'font-size:14px;color:{COLORS["text"]};">'
        f'🎯 <b>En büyük kaldıraç: {theme.esc(top.label)}.</b> Tek başına '
        f'±{delta_pp} puanlık değişimi batma olasılığını '
        f'<b>{abs(top.swing_pp):.1f} puan</b> {yon} '
        f'(%{top.low_probability * 100:.1f} ↔ %{top.high_probability * 100:.1f}). '
        f'{tsat}Diğer dört sürgüyü kurcalamadan önce buraya bak.'
        f'</div>', unsafe_allow_html=True)

    olu = [i.label for i in tor.impacts if i.negligible]
    if olu:
        st.caption(
            f"⚪ Şu ayarlarda pratikte etkisiz: **{', '.join(olu)}** "
            f"(±{delta_pp} puan oynatmak manşeti {sensitivity.NEGLIGIBLE_SWING_PP} "
            f"puandan az değiştiriyor). Bu sürgülerle uğraşmak zaman kaybı."
        )


# ══════════════════════════════════════════════════════════════════════════
#  SENARYO DEFTERİ — birkaç senaryoyu yan yana koy
# ══════════════════════════════════════════════════════════════════════════
# Bir CFO aracının asıl işi tek senaryo hesaplamak değil, seçenekleri
# karşılaştırmaktır: "10M kredi mi, 5M mi, hiç çekmemek mi?"
theme.section("Senaryo Defteri — Seçenekleri Yan Yana Koy", chip="KARŞILAŞTIR")

if "defter" not in st.session_state:
    st.session_state.defter = []

kay1, kay2 = st.columns([2, 1])
# Defter kredi seçeneklerini karşılaştırıyor; kaydedilecek dip de kredili
# senaryonunki olmalı (kredisizken zaten hepsi aynı dibi gösterirdi).
_kayit_wk = wk_loan if wk_loan is not None else wk

with kay1:
    _ad = st.text_input("Bu senaryoya bir ad ver", value="", max_chars=store.MAX_AD,
                        placeholder=f"örn. {money(loan_amount, sym)} kredi · {loan_term} ay")
with kay2:
    st.write("")
    if st.button("💾 Senaryoyu Kaydet", width="stretch"):
        st.session_state.defter = store.kaydet(
            st.session_state.defter,
            _ad or f"{money(loan_amount, sym)} · {loan_term} ay",
            _guncel,
            {"batma_yuzde": round(ruin_pct, 1),
             "iflas_ayi": (round(mc_res.expected_ruin_month)
                           if mc_res.expected_ruin_month else None),
             "aylik_net": round(monthly_net),
             # Kredili senaryo varsa ONUN dibi kaydedilir: defterin sorusu
             # "bu krediyi çekersem ne olur", taksit yükü de dibi derinleştirir.
             "en_dip_hafta": round(_kayit_wk.min_week.closing_cash)
             if _kayit_wk.min_week else None},
        )

# Geri yükleme, tabloyu çizmeden ÖNCE işlenmeli: yoksa dosyayı yükleyen
# kullanıcı "n kayıt geri yüklendi" mesajını görür ama tabloyu bir sonraki
# etkileşime kadar göremez.
_yuklenen_defter = st.file_uploader(
    "Daha önce indirdiğin defteri geri yükle (JSON)", type=["json"],
    help="Kayıtlar sunucuda tutulmaz; kalıcılık indirdiğin dosyadadır.")

# Dosya, seçili kaldığı sürece HER yeniden koşuda yükleyiciden geri gelir.
# Koşulsuz içe aktarmak, o arada kaydedilen yeni senaryoları ezerdi: kullanıcı
# kaydını tabloda görür, sonra bir sürgü oynatınca kaydı sessizce kaybolurdu.
# Bu yüzden her dosya yalnızca bir kez içe aktarılır.
if _yuklenen_defter is not None:
    if st.session_state.get("_yuklenen_dosya") != _yuklenen_defter.file_id:
        st.session_state._yuklenen_dosya = _yuklenen_defter.file_id
        _gelen = store.ice_aktar(_yuklenen_defter.getvalue())
        if _gelen:
            st.session_state.defter = _gelen
            st.success(f"{len(_gelen)} kayıt geri yüklendi.")
        else:
            st.error("Dosya okunamadı ya da içinde geçerli kayıt yok.")
else:
    # Kullanıcı dosyayı kaldırdıysa aynı dosyayı tekrar yükleyebilmeli.
    st.session_state.pop("_yuklenen_dosya", None)

if st.session_state.defter:
    st.dataframe(pd.DataFrame(store.karsilastirma_tablosu(st.session_state.defter)),
                 width="stretch", hide_index=True)

    sil1, sil2, sil3 = st.columns([2, 1, 1])
    _silinecek = sil1.selectbox("Kaydı sil",
                                [k["ad"] for k in st.session_state.defter],
                                label_visibility="collapsed")
    if sil2.button("🗑️ Sil", width="stretch"):
        st.session_state.defter = store.sil(st.session_state.defter, _silinecek)
        st.rerun()
    sil3.download_button(
        "⬇️ Defteri İndir", data=store.disa_aktar(st.session_state.defter),
        file_name="cash_guard_senaryolar.json", mime="application/json",
        width="stretch",
    )
else:
    st.caption("Henüz kayıt yok. Sürgüleri ayarlayıp bir senaryoyu kaydet, "
               "sonra başka bir senaryo deneyip ikisini yan yana gör.")


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
    # Yaşlandırmadan türeyenler: CFO "gecikmiş" ile "hiç gelmeyecek" arasındaki
    # farkı ancak bu sayıları görürse kurabilir.
    "expected_uncollectible": round(aging.expected_loss) if aging.total else None,
    "dso_days": round(aging.dso) if aging.dso else None,
    "overdue_share": round(aging.overdue_share, 3) if aging.total else None,
    # Yapısal (bilanço) hüküm — nakit hükmüyle çeliştiğinde CFO bunu kurabilsin
    "z_score": round(zres.score, 2) if zres.available else None,
    "z_zone": zres.zone if zres.available else None,
    "z_model": zres.model_name if zres.available else None,
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

advice = get_cfo_advice(json.dumps(cfo_ctx, ensure_ascii=False, sort_keys=True),
                        ",".join(llm_engines) or "yerel")
cols.markdown(
    f'<span class="cg-badge">Kaynak: {advice["source"]}</span>'
    + ("" if llm_engines else
       '<span class="cg-badge">Deterministik — aynı sayılar, aynı plan</span>'),
    unsafe_allow_html=True,
)

# Gerçek LLM beklenip yerel motora düşüldüyse sebebini göster. Sessiz fallback,
# anahtarı ekleyen kişiyi karanlıkta bırakıyordu: "olmadı" görünüyor ama paket
# mi, anahtar mı, çağrı mı sorunlu belli olmuyordu.
if advice.get("reason"):
    cols.caption(f"⚠️ Gerçek LLM devrede değil — {advice['reason']}")

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
    "z_score": round(zres.score, 2) if zres.available else None,
    "z_zone": zres.zone if zres.available else None,
    "expected_uncollectible": round(aging.expected_loss) if aging.total else None,
    "dso_days": round(aging.dso) if aging.dso else None,
    # 13 haftalık ufkun özeti: aylık tabloların göremediği an
    "weekly_min_cash": round(dip.closing_cash) if dip else None,
    "weekly_min_week": f"{dip.start:%d.%m.%Y} haftası" if dip else None,
    "weekly_end_cash": round(wk.end_cash),
    "weekly_intramonth_gap": round(wk.intramonth_gap),
    # Tornado'nun tek cümlelik hükmü
    "top_driver": tor.top.label if tor.top else None,
    "top_driver_swing": round(abs(tor.top.swing_pp), 1) if tor.top else 0.0,
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
