"""
performance_utils.py
────────────────────
Performans kritik yardımcılar.

Cash Guard'ın Monte Carlo çekirdeği tamamen NumPy ile vektörize edilmiştir;
yani numba OLMADAN da hızlı çalışır (10.000 iterasyon göz açıp kapayana kadar).
Ancak makinede numba KURULU ise, ağır sıralı (path-dependent) döngü JIT ile
derlenip 50.000+ iterasyonda ciddi hızlanma sağlar.

Bu modül tek bir sözleşme sunar:
    simulate_paths(cash0, revenue, expense, debt_service) -> (paths, ruined, ruin_month)

`revenue` ve `expense` şekli (n_iter, n_months) olan, şoklar UYGULANMIŞ
matrislerdir. Nakit yolu ay-ay kümülatif hesaplanır çünkü "bir kez sıfırın
altına düştüyse battı" mantığı sıralıdır (path-dependent) ve basit bir cumsum
ile yakalanamaz — batıştan sonra toparlanan senaryoyu da doğru saymalıyız.

HAS_NUMBA bayrağı UI'da "hızlandırma açık mı" rozetini göstermek için dışa açılır.
"""
from __future__ import annotations

import numpy as np

# ── numba'yı nazikçe dene; yoksa saf NumPy'a düş ──────────────────────────
try:
    from numba import njit, prange  # type: ignore

    HAS_NUMBA = True
except Exception:  # ImportError dahil her şeyi yut — asla patlama
    HAS_NUMBA = False

    # numba yoksa dekoratörleri no-op yapan yer tutucular
    def njit(*args, **kwargs):  # type: ignore
        def _wrap(fn):
            return fn

        # @njit ve @njit(...) her iki kullanımı da destekle
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        return _wrap

    def prange(*args, **kwargs):  # type: ignore
        return range(*args, **kwargs)


ACCELERATION = "Numba JIT" if HAS_NUMBA else "NumPy (vektörize)"


@njit(cache=True, fastmath=True)
def _simulate_paths_kernel(cash0, revenue, expense, debt_service):
    """
    Sıralı nakit yolu çekirdeği (numba varsa derlenir).

    Girdi:
        cash0        : float                başlangıç kasası
        revenue      : (n, m) float64       şoklu aylık tahsilat/gelir
        expense      : (n, m) float64       şoklu aylık sabit gider
        debt_service : float                sabit aylık borç servisi
    Çıktı:
        paths        : (n, m) float64       her senaryonun ay-sonu kasası
        ruined       : (n,)   bool          senaryo hiç sıfırın altına düştü mü
        ruin_month   : (n,)   int32         ilk temerrüt ayı (0-index), yoksa -1
    """
    n, m = revenue.shape
    paths = np.empty((n, m), dtype=np.float64)
    ruined = np.zeros(n, dtype=np.bool_)
    ruin_month = np.full(n, -1, dtype=np.int32)

    for i in prange(n):
        cash = cash0
        first_ruin = -1
        for t in range(m):
            cash += revenue[i, t] - expense[i, t] - debt_service
            paths[i, t] = cash
            if cash <= 0.0 and first_ruin == -1:
                first_ruin = t
        if first_ruin != -1:
            ruined[i] = True
            ruin_month[i] = first_ruin

    return paths, ruined, ruin_month


def _simulate_paths_numpy(cash0, revenue, expense, debt_service):
    """
    numba yokken kullanılan saf NumPy sürümü.

    Aylık net akış matrisinin kümülatif toplamı yolu verir (vektörize, hızlı).
    Temerrüt tespiti için "kasa <= 0 olan ilk ay"ı argmax hilesiyle buluruz.
    """
    net = revenue - expense - debt_service          # (n, m)
    paths = cash0 + np.cumsum(net, axis=1)          # ay-sonu kasa yolları
    below = paths <= 0.0                            # (n, m) bool maskesi
    ruined = below.any(axis=1)
    # argmax ilk True'nun indeksini verir; hiç yoksa 0 döner -> ruined ile maskele
    ruin_month = np.where(ruined, below.argmax(axis=1), -1).astype(np.int32)
    return paths, ruined, ruin_month


def simulate_paths(cash0, revenue, expense, debt_service):
    """
    Genel giriş noktası: numba varsa derlenmiş çekirdeği, yoksa NumPy'ı çağırır.
    Her iki yol da AYNI çıktı sözleşmesini döndürür.
    """
    revenue = np.ascontiguousarray(revenue, dtype=np.float64)
    expense = np.ascontiguousarray(expense, dtype=np.float64)
    if HAS_NUMBA:
        return _simulate_paths_kernel(cash0, revenue, expense, float(debt_service))
    return _simulate_paths_numpy(cash0, revenue, expense, float(debt_service))
