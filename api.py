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
from modules import monte_carlo as mc
from modules.ai_cfo import RuthlessCFO
from modules.runway import static_runway

# Tek bir istekle sunucuyu meşgul etmeyi engelleyen tavan. Arayüzdeki en
# yüksek seçenek 50.000; API'de de aynı sınır geçerli.
MAX_ITER = 50_000

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
