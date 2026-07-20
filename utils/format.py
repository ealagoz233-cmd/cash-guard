"""
format.py — Arayüzden bağımsız biçimlendirme.

Neden ayrı dosya: bu yardımcılar `utils/theme.py` içindeydi ve theme.py modül
seviyesinde `streamlit` import ediyor. Sonuç: HTTP API (api.py) sırf para birimi
biçimlendirmek için koca bir arayüz çatısını kurmak zorunda kalıyordu — dağıtımı
şişiren ve mantıksız bir bağımlılık.

Buradaki hiçbir şey arayüz bilmez; Streamlit'ten de, FastAPI'den de, testten de
aynı şekilde çağrılır. theme.py bunları yeniden dışa aktarıyor, dolayısıyla
`from utils.theme import money` yazan mevcut kod aynen çalışmaya devam eder.
"""
from __future__ import annotations

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
