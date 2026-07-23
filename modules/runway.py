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

Demo şirketinde statik hesap ~42 ay derken trend hesabı ~10 ay diyor; Monte
Carlo'nun stresli beklentisi ise ~8. ay. Üçü birlikte "statik hesap seni
kandırır" mesajını sayıyla kurar.

Bu üç sayı `tests/test_runway.py::test_module_docstring_quotes_the_real_demo_ladder`
ile demo verisine bağlıdır. Serbest bırakılınca bayatladılar: veri yeniden
kalibre edildi, buradaki cümle eski rakamı söylemeye devam etti. Kendi hakkında
yanlış konuşan bir modül, kullanıcıya yanlış konuşan bir uygulamanın provasıdır.

Not: Trend doğrusal (birinci derece) uzatılır. Bilinçli olarak basit tutuldu —
12 gözlemle daha yüksek dereceli bir uydurma, sinyal değil gürültü modellemeye
başlar. Eğim en küçük kareler yerine Theil–Sen (ikili eğimlerin medyanı) ile
kestirilir: 12 gözlemde tek bir anormal ay (toplu tahsilat, tek seferlik gider)
en küçük kareleri belirgin biçimde çarpıtıyor ve manşet rakamı o tek ay
belirliyordu. Medyan tabanlı kestirim aykırı değerlere dayanıklıdır.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Doğrusal uydurma için gereken asgari gözlem sayısı. Altında trend konuşulmaz.
MIN_MONTHS_FOR_TREND = 4

# Projeksiyonun aradığı azami ufuk; bunun ötesi "öngörülebilir değil" sayılır.
MAX_PROJECTION_MONTHS = 120


@dataclass
class TrendRunway:
    """
    Trend tabanlı runway sonucu.

    `available=False` "trend konuşacak kadar geçmiş yok" demek; `months=None`
    ise "trend hesaplandı, ama kasa ufuk içinde sıfırlanmıyor". Bu ikisi eskiden
    aynı sinyale biniyordu (fonksiyonun kendisi `None` dönüyordu) ve çağıranın
    ayırt etmesi ancak belgeyi okumasıyla mümkündü — üstelik `months=None` da
    aynı `if` içinde eleniyordu, yani "veri yok" ile "batmıyorsun" ekranda tek
    ve sessiz bir boşluğa dönüşüyordu.

    Alan adları diğer motorlarla ortak (bkz. utils/sufficiency.py).
    """
    months: int | None          # kasanın sıfırlandığı ay (None = ufukta yok)
    slope_per_month: float      # aylık faaliyet nakdindeki değişim (− = bozuluyor)
    latest_net_operating: float # trende göre son ayın faaliyet nakdi
    available: bool = True
    missing_fields: list[str] = field(default_factory=list)


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


# Trendin okuduğu geçmiş sütunları — eksik olduğunda kullanıcıya bu adlar söylenir.
TREND_FIELDS = ("collections", "fixed_expense")


def _yetersiz(eksik: list[str]) -> TrendRunway:
    """Trend konuşulamıyor: sonuç yine döner, ama 'yok' diyen bir sonuç olarak."""
    return TrendRunway(months=None, slope_per_month=0.0, latest_net_operating=0.0,
                       available=False, missing_fields=eksik)


def trend_runway(history: list[dict] | pd.DataFrame, current_cash: float,
                 debt_service: float) -> TrendRunway:
    """
    Geçmişteki bozulma eğilimini uzatarak kasanın sıfırlanacağı ayı bulur.

    Yöntem:
      1) Her ay için faaliyet nakdi = tahsilat − sabit gider.
      2) Bu seriye doğrusal trend uydur (eğim = aylık bozulma hızı).
      3) Trendi ileri uzat, her ay borç servisini de düşerek kasayı yürüt.
      4) Kasanın sıfırın altına indiği ilk ayı döndür.

    Yeterli veri yoksa (sütun eksik ya da 4 aydan az gözlem) `available=False`
    olan bir sonuç döner — arayüz o durumda yalnız statik runway'i gösterir ve
    eksik alanları adıyla söyleyebilir.
    """
    df = pd.DataFrame(history)
    eksik = [s for s in TREND_FIELDS if s not in df.columns]
    if df.empty or eksik:
        return _yetersiz(eksik or list(TREND_FIELDS))
    if len(df) < MIN_MONTHS_FOR_TREND:
        # Sütunlar var ama gözlem az: eksik olan alan değil, geçmişin kendisi.
        return _yetersiz(["history"])

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
