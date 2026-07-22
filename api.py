"""
api.py  —  Cash Guard motorunun HTTP arayüzü (FastAPI)

Streamlit uygulaması bu motorun BİR tüketicisi; burası ikincisi. İkisi de
`modules/` altındaki aynı kodu çağırır — hesap mantığı hiçbir yerde
kopyalanmaz, çünkü kopyalanan mantık er geç ayrışır ve iki arayüz aynı
şirket için farklı sayı gösterir.

Çalıştırma:
    pip install -r requirements-api.txt
    uvicorn api:app --reload
    # http://localhost:8000/docs  (otomatik arayüz)

GÜVENLİK NOTU: Bu servis dışarı açılırsa girdiler güvenilmezdir. Monte Carlo
iterasyon sayısı bilerek sınırlandırıldı — sınırsız bırakmak, tek bir istekle
sunucunun CPU'sunu tüketmeye davetiye çıkarır.
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from modules import loan_simulator as ls
from modules import loan_sweep
from modules import monte_carlo as mc
from modules import receivables
from modules import sensitivity
from modules import weekly
from modules import zscore
from modules.ai_cfo import RuthlessCFO
from modules.runway import static_runway

# Tek bir istekle sunucuyu meşgul etmeyi engelleyen tavan. Arayüzdeki en
# yüksek seçenek 50.000; API'de de aynı sınır geçerli.
MAX_ITER = 50_000

# /sensitivity tek istekte ~11 simülasyon koşar. Aynı tavanı uygularsak bir
# istek 550.000 senaryo demek olur; bu uç için tavan bilerek daha düşük.
MAX_ITER_SENSITIVITY = 20_000

app = FastAPI(
    title="Cash Guard API",
    description=(
        "Nakit hayatta kalma ve kredi stres testi motoru.\n\n"
        "Streamlit arayüzü ([canlı demo](https://cash-guard-eren.streamlit.app)) "
        "bu motorun bir tüketicisi; burası ikincisi. **Hesap mantığı hiçbir yerde "
        "kopyalanmaz** — ikisi de `modules/` altındaki aynı kodu çağırır, bu yüzden "
        "iki arayüz aynı şirket için farklı sayı gösteremez.\n\n"
        "Kaynak kod: [github.com/ealagoz233-cmd/cash-guard]"
        "(https://github.com/ealagoz233-cmd/cash-guard)\n\n"
        "> Karar-destek prototipidir (PoC), yatırım/finans tavsiyesi değildir."
    ),
    version="1.0.0",
    license_info={"name": "MIT",
                  "url": "https://github.com/ealagoz233-cmd/cash-guard/blob/main/LICENSE"},
    openapi_tags=[
        {"name": "Servis", "description": "Sağlık kontrolü ve uyandırma."},
        {"name": "Analiz", "description":
            "Motorun asıl işi: stres testi, kredi senaryosu ve CFO aksiyon planı."},
    ],
)


# ── İstek modelleri ───────────────────────────────────────────────────────
class SimulasyonIstegi(BaseModel):
    """Monte Carlo stres testi girdileri."""
    current_cash: float = Field(..., ge=0, description="Mevcut kasa")
    monthly_revenue: float = Field(..., ge=0, description="Aylık tahsilat")
    monthly_fixed_expense: float = Field(..., ge=0, description="Aylık sabit gider")
    monthly_debt_service: float = Field(0, ge=0, description="Aylık borç servisi")
    income_drop: float = Field(0.10, ge=0, le=1)
    volatility: float = Field(0.12, ge=0, le=1)
    delay_prob: float = Field(0.30, ge=0, le=1)
    delay_severity: float = Field(0.35, ge=0, le=1)
    expense_inflation: float = Field(0.15, ge=0, le=1)
    months: int = Field(12, ge=1, le=120)
    n_iter: int = Field(10_000, ge=100, le=MAX_ITER)
    seed: int | None = Field(42, description="Aynı tohum = aynı sonuç")


class DuyarlilikIstegi(SimulasyonIstegi):
    """
    Tornado analizi girdileri: stres testiyle aynı, artı oynatma adımı.

    `n_iter` tavanı burada daha düşük (bkz. MAX_ITER_SENSITIVITY): tek istek
    sürgü sayısı × 2 + 1 simülasyon koşturur.
    """
    n_iter: int = Field(10_000, ge=100, le=MAX_ITER_SENSITIVITY)
    delta: float = Field(sensitivity.DEFAULT_DELTA, gt=0, le=0.5,
                         description="Her sürgünün ± oynatılacağı miktar (0.05 = 5 puan)")


class KrediIstegi(BaseModel):
    """Kredi senaryosu girdileri."""
    current_cash: float = Field(..., ge=0)
    monthly_revenue: float = Field(..., ge=0)
    monthly_fixed_expense: float = Field(..., ge=0)
    existing_debt_service: float = Field(0, ge=0)
    loan_amount: float = Field(..., ge=0)
    loan_term_months: int = Field(..., ge=1, le=360)
    monthly_interest_rate: float = Field(..., ge=0, le=1)
    horizon_months: int = Field(24, ge=1, le=360)


class TaramaIstegi(SimulasyonIstegi):
    """
    Kredi tutarı taraması girdileri: stres testine ek olarak vade ve faiz.

    `n_iter` tavanı düşük tutuldu (bkz. MAX_ITER_SENSITIVITY): tek istek adım
    sayısı kadar simülasyon koşar.
    """
    n_iter: int = Field(10_000, ge=100, le=MAX_ITER_SENSITIVITY)
    loan_term_months: int = Field(24, ge=1, le=360)
    monthly_interest_rate: float = Field(0.035, ge=0, le=1)
    max_amount: float = Field(loan_sweep.DEFAULT_MAX_AMOUNT, gt=0)
    steps: int = Field(loan_sweep.DEFAULT_STEPS, ge=2, le=41)


class AlacakKalemi(BaseModel):
    """Tek bir alacak kalemi."""
    customer: str = "—"
    amount: float = Field(..., ge=0)
    overdue_days: float = Field(0, description="Vadeyi aşan gün; negatif = vadesi gelmemiş")


class YaslandirmaIstegi(BaseModel):
    """Alacak yaşlandırma analizi girdileri."""
    receivables: list[AlacakKalemi] = Field(default_factory=list)
    total_outstanding: float | None = Field(
        None, ge=0, description="Defterin toplam bakiyesi; kalemlerden büyükse "
                                "fark 'listelenmemiş' sayılır")
    monthly_revenue: float | None = Field(
        None, ge=0, description="DSO için aylık FATURALANAN gelir (tahsilat değil)")
    declared_collection_days: float | None = None


class HaftalikIstek(BaseModel):
    """
    13 haftalık likidite tablosu girdileri.

    `expense_breakdown` verilmezse tüm gider aya eşit yayılır ve tablo ay-içi
    bilgi taşımaz; cevaptaki `informative` alanı bunu bildirir.
    """
    current_cash: float = Field(..., ge=0)
    monthly_collections: float = Field(..., ge=0, description="Aylık TAHSİLAT")
    monthly_fixed_expense: float = Field(..., ge=0)
    monthly_debt_service: float = Field(0, ge=0)
    expense_breakdown: dict[str, float] | None = Field(
        None, description="Kalem adı → aylık tutar; ödeme günleri kalem adından bulunur")
    as_of: str | None = Field(None, description="Veri tarihi (YYYY-AA-GG); "
                                                "projeksiyon ertesi gün başlar")
    weeks: int = Field(weekly.DEFAULT_WEEKS, ge=1, le=52)


class ZSkorIstegi(BaseModel):
    """
    Altman Z-score için bilanço kalemleri.

    `annual_sales` yalnızca X5 kullanan varyantta (Z′) gereklidir. `model`
    verilmezse sektöre bakılır: üretim/imalat ibaresi Z′, aksi hâlde Z″.
    """
    total_assets: float = Field(..., description="Toplam varlık")
    current_assets: float = Field(..., ge=0, description="Dönen varlık")
    current_liabilities: float = Field(..., ge=0, description="Kısa vadeli yükümlülük")
    total_liabilities: float = Field(..., ge=0, description="Toplam yükümlülük")
    retained_earnings: float = Field(..., description="Geçmiş yıl kârları")
    ebit_annual: float = Field(..., description="Yıllık FVÖK")
    annual_sales: float | None = Field(None, ge=0, description="Yıllık satış (Z′ için)")
    sector: str | None = Field(None, description="Model seçimi için sektör")
    model: str | None = Field(None, description="'zprime' | 'zdouble' — sektörü ezer")


class TavsiyeIstegi(BaseModel):
    """CFO aksiyon planı için analiz bağlamı."""
    current_cash: float
    net_operating: float
    monthly_net: float
    ruin_probability: float = Field(..., ge=0, le=1)
    expected_ruin_month: int | None = None
    loan_amount: float = 0
    relief_months: int = 0
    default_with_loan: int | None = None
    debt_service: float = 0
    runway_months: float | None = None
    trend_runway_months: int | None = None
    trend_slope: float | None = None
    currency_symbol: str = "TL"


# ── Uç noktalar ───────────────────────────────────────────────────────────
@app.get("/health", tags=["Servis"], summary="Servis ayakta mı?")
def health() -> dict:
    """Servis ayakta mı — dağıtım sağlık kontrolü için."""
    return {"status": "ok"}


@app.post("/simulate", tags=["Analiz"],
          summary="Monte Carlo stres testi — batma olasılığı")
def simulate(istek: SimulasyonIstegi) -> dict:
    """
    Monte Carlo stres testi: batma olasılığı, beklenen iflas ayı, nakit ömrü.
    """
    sonuc = mc.run(mc.StressParams(**istek.model_dump()))
    aylik_net = (istek.monthly_revenue - istek.monthly_fixed_expense
                 - istek.monthly_debt_service)
    return {
        "ruin_probability": sonuc.ruin_probability,
        "expected_ruin_month": sonuc.expected_ruin_month,
        "n_iter": sonuc.n_iter,
        "acceleration": sonuc.acceleration,
        "monthly_net": aylik_net,
        "runway_months": static_runway(istek.current_cash, aylik_net),
    }


@app.post("/sensitivity", tags=["Analiz"],
          summary="Duyarlılık (tornado) — hangi sürgü riski en çok oynatıyor")
def duyarlilik(istek: DuyarlilikIstegi) -> dict:
    """
    Tornado analizi: her stres sürgüsü tek tek ±`delta` oynatılır, diğerleri
    sabit tutulur ve batma olasılığındaki değişim ölçülür. Sonuç etkiye göre
    büyükten küçüğe sıralı döner; `drivers[0]` en büyük kaldıraçtır.

    Bütün koşular aynı tohumu paylaşır, dolayısıyla `swing` Monte Carlo
    gürültüsü değil parametrenin kendi etkisidir.
    """
    girdi = istek.model_dump()
    delta = girdi.pop("delta")
    sonuc = sensitivity.tornado(mc.StressParams(**girdi), delta=delta)
    return {
        "base_probability": sonuc.base_probability,
        "delta": sonuc.delta,
        "n_iter": sonuc.n_iter,
        "drivers": [
            {
                "key": i.key,
                "label": i.label,
                "low_value": i.low_value,
                "high_value": i.high_value,
                "low_probability": i.low_probability,
                "high_probability": i.high_probability,
                "swing": i.swing,
                "negligible": i.negligible,
            }
            for i in sonuc.impacts
        ],
    }


@app.post("/receivables", tags=["Analiz"],
          summary="Alacak yaşlandırma — DSO, şüpheli alacak, türetilmiş sürgüler")
def yaslandirma(istek: YaslandirmaIstegi) -> dict:
    """
    Alacak defterini yaşlandırıp üç şey döndürür: **DSO** (bakiye ÷ aylık
    faturalanan gelir × 30), **beklenen tahsil edilememe** (kova başına yerleşik
    karşılık oranlarıyla) ve stres sürgülerinin **yaşlandırmadan türetilmiş**
    karşılıkları.

    `implied.clamped` doğruysa defter sürgülerin taşıyabileceğinden daha
    bozuktur; o değerlerle koşulan simülasyon gerçekten daha iyimser olur.
    `dso_conflict` doğruysa yaşlandırma ile bakiye birbirini tutmuyordur —
    türetilmiş değerleri kullanmadan önce veriyi düzeltmek gerekir.
    """
    profil = receivables.age(
        [k.model_dump() for k in istek.receivables],
        total_outstanding=istek.total_outstanding,
        monthly_revenue=istek.monthly_revenue,
        declared_collection_days=istek.declared_collection_days,
    )
    turetilen = receivables.implied_stress(profil)
    return {
        "total": profil.total,
        "listed_amount": profil.listed_amount,
        "unlisted_amount": profil.unlisted_amount,
        "expected_loss": profil.expected_loss,
        "expected_loss_share": profil.expected_loss_share,
        "overdue_amount": profil.overdue_amount,
        "overdue_share": profil.overdue_share,
        "weighted_overdue_days": profil.weighted_overdue_days,
        "dso": profil.dso,
        "dso_conflict": profil.dso_conflict,
        "buckets": [
            {"name": r.name, "amount": r.amount, "share": r.share,
             "loss_rate": r.loss_rate, "expected_loss": r.expected_loss,
             "lag_months": r.lag_months}
            for r in profil.rows
        ],
        "implied": {
            "delay_prob": turetilen.delay_prob,
            "delay_severity": turetilen.delay_severity,
            "expected_slip_rate": turetilen.expected_slip_rate,
            "achievable_slip_rate": turetilen.achievable_slip_rate,
            "clamped": turetilen.clamped,
        },
    }


@app.post("/loan-sweep", tags=["Analiz"],
          summary="Kredi tutarı taraması — 'çekeyim mi' değil, 'kaç lira'")
def kredi_taramasi(istek: TaramaIstegi) -> dict:
    """
    Kredi tutarını sıfırdan üst sınıra tarar ve her tutar için iki ufku birden
    döndürür: **12 aylık batma olasılığı** (stokastik) ve **iflasın ötelenmesi**
    (deterministik, işaretli).

    `best_is_a_trap` doğruysa 12 aylık riski en aza indiren tutar, uzun vadede
    iflası ÖNE çekiyordur. Yalnızca `ruin_probability`'ye bakıp öneri üretmeyin —
    nakit enjeksiyonu ilk 12 ayı neredeyse her zaman rahatlatır, taksit sonra
    vurur. Bu uç, o hatayı yapmamak için iki sütunu birlikte veriyor.
    """
    girdi = istek.model_dump()
    vade = girdi.pop("loan_term_months")
    faiz = girdi.pop("monthly_interest_rate")
    ust = girdi.pop("max_amount")
    adim = girdi.pop("steps")

    stres = mc.StressParams(**girdi)
    sablon = ls.LoanScenario(
        current_cash=stres.current_cash, monthly_revenue=stres.monthly_revenue,
        monthly_fixed_expense=stres.monthly_fixed_expense,
        existing_debt_service=stres.monthly_debt_service,
        loan_amount=0.0, loan_term_months=vade,
        monthly_interest_rate=faiz, horizon_months=24,
    )
    sonuc = loan_sweep.sweep(stres, sablon, max_amount=ust, steps=adim)
    en_iyi = sonuc.best
    return {
        "n_iter": sonuc.n_iter,
        "baseline_probability": sonuc.baseline.ruin_probability if sonuc.baseline else None,
        "gain_pp": sonuc.gain_pp,
        "borrowing_helps": sonuc.borrowing_helps,
        "best_is_a_trap": sonuc.best_is_a_trap,
        "best": None if en_iyi is None else {
            "amount": en_iyi.amount, "installment": en_iyi.installment,
            "ruin_probability": en_iyi.ruin_probability,
            "relief_months": en_iyi.relief_months,
            "total_interest": en_iyi.total_interest,
        },
        "points": [
            {"amount": p.amount, "installment": p.installment,
             "total_interest": p.total_interest,
             "ruin_probability": p.ruin_probability,
             "relief_months": p.relief_months,
             "default_with_loan": p.default_with_loan,
             "is_trap": p.is_trap}
            for p in sonuc.points
        ],
    }


@app.post("/weekly", tags=["Analiz"],
          summary="13 haftalık likidite ufku — ay-içi nakit çukurları")
def haftalik(istek: HaftalikIstek) -> dict:
    """
    Aylık toplamları bir nakit takvimine dağıtıp haftalık kasa yolunu verir.
    Aylık model ayın en kritik gününü ortalamanın altında saklar; bu uç onu
    görünür kılar.

    `intramonth_gap`, dönem sonu kasası ile en dip hafta arasındaki farktır —
    yani aylık bakışın GÖREMEDİĞİ derinlik. `informative` yanlışsa gider
    dağılımı verilmemiş demektir ve eğri, aylık çizginin ince hâlinden ibarettir.
    """
    plan = weekly.build(
        current_cash=istek.current_cash,
        monthly_collections=istek.monthly_collections,
        expense_breakdown=istek.expense_breakdown,
        monthly_fixed_expense=istek.monthly_fixed_expense,
        monthly_debt_service=istek.monthly_debt_service,
        start=weekly.parse_start(istek.as_of),
        weeks=istek.weeks,
    )
    dip, ilk_eksi = plan.min_week, plan.first_negative
    return {
        "opening_cash": plan.opening_cash,
        "end_cash": plan.end_cash,
        "informative": plan.informative,
        "dated_items": plan.dated_items,
        "intramonth_gap": plan.intramonth_gap,
        "min_week": None if dip is None else {
            "index": dip.index, "start": dip.start.isoformat(),
            "closing_cash": dip.closing_cash},
        "first_negative_week": None if ilk_eksi is None else {
            "index": ilk_eksi.index, "start": ilk_eksi.start.isoformat()},
        "weeks": [
            {"index": w.index, "start": w.start.isoformat(),
             "end": w.end.isoformat(), "inflow": w.inflow,
             "outflow": w.outflow, "net": w.net, "closing_cash": w.closing_cash}
            for w in plan.weeks
        ],
    }


@app.post("/zscore", tags=["Analiz"],
          summary="Altman Z-score — bilançodan yapısal iflas riski")
def z_skoru(istek: ZSkorIstegi) -> dict:
    """
    Altman Z-score: şirketin bilançosunu tarihsel olarak batanlarınkiyle
    karşılaştırır. Nakit modelinden **bağımsız** bir hüküm verir ve sık sık
    onunla çelişir — çelişkinin kendisi bilgidir: Altman tahakkuk esaslı yıllık
    bir fotoğraf çeker, nakdin ne zaman geldiğini görmez.

    Veri yetersizse skor **üretilmez**; `missing_fields` dolu döner. Yarım
    veriyle hesaplanmış bir iflas skoru, hiç skor olmamasından tehlikelidir.
    """
    girdi = istek.model_dump()
    secim = girdi.pop("model", None)
    sektor = girdi.pop("sector", None)
    model = zscore.MODELS.get(secim) or zscore.pick_model(sektor)

    sonuc = zscore.compute(girdi, model)
    return {
        "model": sonuc.model_key,
        "model_name": sonuc.model_name,
        "model_fits": sonuc.model_fits,
        "score": sonuc.score,
        "zone": sonuc.zone,
        "safe_above": sonuc.safe_above,
        "distress_below": sonuc.distress_below,
        "distance_to_safe": sonuc.distance_to_safe,
        "missing_fields": sonuc.missing_fields,
        "components": [
            {"key": c.key, "label": c.label, "ratio": c.ratio,
             "weight": c.weight, "contribution": c.contribution}
            for c in sonuc.components
        ],
    }


@app.post("/loan", tags=["Analiz"],
          summary="Kredi senaryosu — borç tuzağı analizi")
def loan(istek: KrediIstegi) -> dict:
    """
    Kredi senaryosu: taksit, toplam faiz ve kredinin iflası öteleyip
    öteleyemediği (borç tuzağı analizi).
    """
    sonuc = ls.simulate(ls.LoanScenario(**istek.model_dump()))
    # numpy dizileri JSON'a çevrilemez; sadece skaler alanları döndür.
    return {k: v for k, v in sonuc.items() if isinstance(v, (int, float, type(None)))}


@app.post("/advise", tags=["Analiz"],
          summary="Acımasız CFO aksiyon planı")
def advise(istek: TavsiyeIstegi) -> dict:
    """
    Acımasız CFO aksiyon planı. API anahtarı tanımlıysa LLM, değilse
    deterministik kural tabanlı motor kullanılır — her koşulda cevap döner.
    """
    tavsiye = RuthlessCFO().advise(
        istek.model_dump() | {"top_receivables": [], "expense_breakdown": {}}
    )
    return {"text": tavsiye.text, "source": tavsiye.source}
