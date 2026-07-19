"""
runway.py  —  Nakit ömrü (runway) hesapları
────────────────────────────────────────────
Kasanın ne kadar dayanacağı sorusunun İKİ farklı cevabı vardır ve aradaki fark
bu uygulamanın asıl mesajıdır:

  • STATİK runway  = kasa / bugünkü aylık yakım.
    Herkesin yaptığı hesap. Bugünkü yakım hızının sonsuza dek sabit kalacağını
    varsayar — yani şirketin durumunun BOZULMADIĞINI kabul eder.

  • TREND runway   = geçmiş tablodaki bozulma eğilimi doğrusal olarak uzatılır.
    Tahsilat her ay biraz daha düşüyor ve giderler biraz daha şişiyorsa, yakım
    hızı sabit değil ARTAN bir seridir. Bu, kasayı statik hesabın söylediğinden
    çok daha erken bitirir.

Demo şirketinde statik hesap ~42 ay derken trend hesabı ~14 ay diyor; Monte
Carlo'nun stresli beklentisi ise ~8. ay. Üçü birlikte "statik hesap seni
kandırır" mesajını sayıyla kurar.

Not: Trend doğrusal (birinci derece) uzatılır. Bilinçli olarak basit tutuldu —
12 gözlemle daha yüksek dereceli bir uydurma, sinyal değil gürültü modellemeye
başlar. Eğim en küçük kareler yerine Theil–Sen (ikili eğimlerin medyanı) ile
kestirilir: 12 gözlemde tek bir anormal ay (toplu tahsilat, tek seferlik gider)
en küçük kareleri belirgin biçimde çarpıtıyor ve manşet rakamı o tek ay
belirliyordu. Medyan tabanlı kestirim aykırı değerlere dayanıklıdır.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Doğrusal uydurma için gereken asgari gözlem sayısı. Altında trend konuşulmaz.
MIN_MONTHS_FOR_TREND = 4

# Projeksiyonun aradığı azami ufuk; bunun ötesi "öngörülebilir değil" sayılır.
MAX_PROJECTION_MONTHS = 120


@dataclass
class TrendRunway:
    """Trend tabanlı runway sonucu."""
    months: int | None          # kasanın sıfırlandığı ay (None = ufukta yok)
    slope_per_month: float      # aylık faaliyet nakdindeki değişim (− = bozuluyor)
    latest_net_operating: float # trende göre son ayın faaliyet nakdi


def _theil_sen(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """
    Aykırı değere dayanıklı doğrusal uydurma: tüm nokta çiftlerinin eğimlerinin
    medyanı. Kesişim de artıkların medyanı olarak alınır.

    En küçük karelere göre avantajı: tek bir sıra dışı ay (ör. eski alacakların
    toplu tahsil edildiği bir ay) eğimi tek başına sürükleyemez.
    """
    i, j = np.triu_indices(len(x), k=1)          # tüm i<j çiftleri
    slope = float(np.median((y[j] - y[i]) / (x[j] - x[i])))
    intercept = float(np.median(y - slope * x))
    return slope, intercept


def static_runway(current_cash: float, monthly_net: float) -> float | None:
    """
    Klasik runway: kasa / aylık net dış akış.

    monthly_net ≥ 0 ise kasa erimiyordur -> None.
    """
    if monthly_net >= 0:
        return None
    return current_cash / abs(monthly_net)


def trend_runway(history: list[dict] | pd.DataFrame, current_cash: float,
                 debt_service: float) -> TrendRunway | None:
    """
    Geçmişteki bozulma eğilimini uzatarak kasanın sıfırlanacağı ayı bulur.

    Yöntem:
      1) Her ay için faaliyet nakdi = tahsilat − sabit gider.
      2) Bu seriye doğrusal trend uydur (eğim = aylık bozulma hızı).
      3) Trendi ileri uzat, her ay borç servisini de düşerek kasayı yürüt.
      4) Kasanın sıfırın altına indiği ilk ayı döndür.

    Yeterli veri yoksa (sütun eksik ya da 4 aydan az gözlem) None döner —
    arayüz o durumda yalnız statik runway'i gösterir.
    """
    df = pd.DataFrame(history)
    if df.empty or not {"collections", "fixed_expense"} <= set(df.columns):
        return None
    if len(df) < MIN_MONTHS_FOR_TREND:
        return None

    net_op = (df["collections"] - df["fixed_expense"]).to_numpy(dtype=float)
    x = np.arange(len(net_op), dtype=float)
    slope, intercept = _theil_sen(x, net_op)

    # Son ayın trend üzerindeki değeri (ham son gözlem değil: gürültüye dayanıklı)
    latest = float(slope * x[-1] + intercept)

    cash = float(current_cash)
    for t in range(1, MAX_PROJECTION_MONTHS + 1):
        cash += (latest + slope * t) - debt_service
        if cash <= 0:
            return TrendRunway(months=t, slope_per_month=float(slope),
                               latest_net_operating=latest)
    return TrendRunway(months=None, slope_per_month=float(slope),
                       latest_net_operating=latest)
