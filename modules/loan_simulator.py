"""
loan_simulator.py  —  MODÜL 1: "Kredi Kurtarır mı?" Simülatörü
──────────────────────────────────────────────────────────────
Amaç: Patronun aklındaki "acil nakit için kredi çekeyim" fikrini deterministik
olarak sınamak. Kredi çekilince ilk aylardaki SAHTE RAHATLAMAYI ve sonrasında
gelen bileşik faiz + anapara yükünü ay-ay (varsayılan 24 ay) hesaplayıp,
"Kredili" ile "Kredisiz" nakit eğrilerini yan yana koyar.

Temel matematik — eşit taksitli (annüite) kredi:
        A = P · r · (1+r)^n / ((1+r)^n − 1)
    P: anapara, r: aylık faiz (ondalık), n: vade (ay), A: aylık taksit.

Her ay nakit güncellemesi:
        kasa += net_faaliyet_akışı − borç_servisi
    net_faaliyet_akışı = ortalama_gelir − ortalama_sabit_gider
    borç_servisi = mevcut_borç_servisi + (varsa) yeni kredinin aylık taksidi
                   (yeni taksit sadece vade boyunca eklenir).

Çıktı, app.py'nin doğrudan Plotly'ye verebileceği bir DataFrame + özet sözlük.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class LoanScenario:
    """Kullanıcı / mock datadan gelen tüm Modül-1 parametreleri."""
    current_cash: float           # mevcut kasa nakdi
    monthly_revenue: float        # ortalama aylık gelir (tahsil edilen)
    monthly_fixed_expense: float  # aylık sabit gider (burn rate)
    existing_debt_service: float  # mevcut aylık borç ödemesi
    # Yeni çekilecek kredi senaryosu:
    loan_amount: float            # kredi miktarı (0 ise "kredisiz" senaryo)
    loan_term_months: int         # vade (ay)
    monthly_interest_rate: float  # aylık faiz oranı, ondalık (ör. 0.035 = %3.5)
    horizon_months: int = 24      # simülasyon ufku


def monthly_installment(principal: float, monthly_rate: float, term: int) -> float:
    """Annüite (eşit taksit) formülüyle aylık ödemeyi hesaplar."""
    if principal <= 0 or term <= 0:
        return 0.0
    if monthly_rate <= 0:            # faizsiz özel durum: anapara / vade
        return principal / term
    r, n = monthly_rate, term
    factor = (1 + r) ** n
    return principal * r * factor / (factor - 1)


def amortization_schedule(principal: float, monthly_rate: float, term: int) -> pd.DataFrame:
    """
    Ay-ay itfa tablosu: taksit, faiz payı, anapara payı, kalan bakiye.
    CFO raporunda "toplam ödenen faiz" gibi metrikler için de kullanılır.
    """
    pay = monthly_installment(principal, monthly_rate, term)
    rows, balance = [], principal
    for m in range(1, term + 1):
        interest = balance * monthly_rate
        principal_part = pay - interest
        balance = max(0.0, balance - principal_part)
        rows.append(
            {"month": m, "payment": pay, "interest": interest,
             "principal": principal_part, "balance": balance}
        )
    return pd.DataFrame(rows)


def _project_cash(cash0: float, net_operating: float,
                  debt_service_by_month: np.ndarray) -> np.ndarray:
    """Verilen aylık borç servisi dizisiyle nakit yolunu üretir."""
    months = len(debt_service_by_month)
    cash = np.empty(months + 1)
    cash[0] = cash0
    for t in range(months):
        cash[t + 1] = cash[t] + net_operating - debt_service_by_month[t]
    return cash


def _first_default_month(cash_path: np.ndarray) -> int | None:
    """Nakit yolunda kasanın ilk kez sıfırın altına düştüğü ayı döndürür."""
    idx = np.where(cash_path[1:] < 0)[0]     # cash[0] başlangıç, aylar 1..N
    return int(idx[0] + 1) if idx.size else None


def _build_service(existing: float, installment: float, term: int, horizon: int) -> np.ndarray:
    """Aylık borç servisi dizisi: mevcut servis + vade boyunca yeni taksit."""
    service = np.full(horizon, float(existing), dtype=float)  # dtype=float: int cast hatası olmasın
    t = min(term, horizon)
    service[:t] += installment                               # taksit yalnız vade boyunca
    return service


def simulate(scenario: LoanScenario) -> dict:
    """
    Modül 1 ana fonksiyonu.

    Döndürür:
        {
          "df": DataFrame[month, cash_with_loan, cash_without_loan],  # grafik (horizon)
          "installment": float,            # yeni kredinin aylık taksidi
          "total_interest": float,         # kredi boyunca ödenecek toplam faiz
          "default_with_loan": int|None,   # kredili iflas ayı (grafik ufku içinde)
          "default_without_loan": int|None,
          "relief_months": int,            # İŞARETLİ etki: + kredi zaman kazandırır,
                                           #   − kredi iflası ÖNE çeker (borç tuzağı)
          "net_operating": float,          # aylık faaliyet net NAKİT akışı
        }

    Not: "relief_months" grafik ufkuyla sınırlı değildir; işareti doğru vermek
    için iflas ayları daha geniş bir iç pencerede (60 ay) hesaplanır. Böylece
    "kredisiz de batıyordun ama 22 ay sonra; kredi bunu 20. aya çekiyor" gibi
    durumlar işaretle doğru ifade edilir.
    """
    n = scenario.horizon_months
    net_op = scenario.monthly_revenue - scenario.monthly_fixed_expense
    inst = monthly_installment(
        scenario.loan_amount, scenario.monthly_interest_rate, scenario.loan_term_months
    )

    # ── Grafik için ufuk (horizon) boyunca projeksiyon ────────────────────
    base_service = _build_service(scenario.existing_debt_service, 0.0,
                                  scenario.loan_term_months, n)
    loan_service = _build_service(scenario.existing_debt_service, inst,
                                  scenario.loan_term_months, n)
    cash_base = _project_cash(scenario.current_cash, net_op, base_service)
    cash_loan = _project_cash(scenario.current_cash + scenario.loan_amount, net_op, loan_service)

    # ── İşaretli "öteleme" için geniş iç pencere (60 ay) ──────────────────
    BIG = 60
    base_service_big = _build_service(scenario.existing_debt_service, 0.0,
                                      scenario.loan_term_months, BIG)
    loan_service_big = _build_service(scenario.existing_debt_service, inst,
                                      scenario.loan_term_months, BIG)
    d_base_big = _first_default_month(_project_cash(scenario.current_cash, net_op, base_service_big))
    d_loan_big = _first_default_month(
        _project_cash(scenario.current_cash + scenario.loan_amount, net_op, loan_service_big))

    # None -> "60 ay içinde batmadı" kabulü (BIG+1).
    # relief = krediyle yaşanan ay − kredisiz yaşanan ay.
    #   + : kredi zaman KAZANDIRIR (iflası öteler)
    #   − : kredi iflası ÖNE çeker (borç tuzağı)
    w = d_loan_big if d_loan_big is not None else BIG + 1
    wo = d_base_big if d_base_big is not None else BIG + 1
    relief = int(w - wo)

    # ── Özet metrikler ────────────────────────────────────────────────────
    sched = amortization_schedule(
        scenario.loan_amount, scenario.monthly_interest_rate, scenario.loan_term_months
    )
    total_interest = float(sched["interest"].sum()) if not sched.empty else 0.0

    df = pd.DataFrame(
        {
            "month": np.arange(0, n + 1),
            "cash_with_loan": cash_loan,
            "cash_without_loan": cash_base,
        }
    )

    return {
        "df": df,
        "installment": inst,
        "total_interest": total_interest,
        # Grafik işaretleri ufuk içindeki ilk temerrüde göre:
        "default_with_loan": _first_default_month(cash_loan),
        "default_without_loan": _first_default_month(cash_base),
        "relief_months": relief,
        "net_operating": net_op,
    }
