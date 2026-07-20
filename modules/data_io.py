"""
data_io.py  —  Veri yükleme ve kullanıcı dosyası ayrıştırma
────────────────────────────────────────────────────────────
Mock şirket verisini okur ve kullanıcının yüklediği CSV/Excel'i uygulamanın
beklediği sözlük biçimine çevirir.

app.py'den ayrı bir modülde durmasının sebebi: yükleme yolu uygulamanın en
kırılgan kısmı (eksik sütun = çöken sayfa) ve testten geçirilebilmesi gerekiyor.
app.py bir Streamlit script'i olduğu için import edilemez, bu modül edilebilir.

İki biçim desteklenir:
  A) 'alan,deger' (key-value)  — çekirdek skaler alanları ezer.
  B) Aylık geçmiş tablosu [month, revenue, fixed_expense, collections, cash_end]
     — ortalamaları ve son kasa değerini bu tablodan türetir.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "mock_company_data.json"

# Kullanıcı kendi verisini yüklediğinde mock şirkete ait olup TAŞINMAMASI gereken
# anlatısal alanlar. Taşınırsa ekranda başka bir şirketin grafikleri kullanıcının
# rakamlarıymış gibi görünür.
_MOCK_ONLY_KEYS = ("history", "top_receivables", "expense_breakdown", "sector",
                   "receivables_outstanding", "avg_collection_days")

# Geçmiş trend grafiğinin çizilebilmesi için gereken asgari sütunlar.
REQUIRED_HISTORY_COLS = {"month", "revenue"}

# Dosya adında görüntülemeye izin verilen karakterler. Adı arayüzde gösteriyoruz
# ve orası ham HTML; kaçırma arayüz tarafında da yapılıyor ama veriye zaten
# temiz girsin (savunmanın tek katmana bağlı kalmaması için).
_SAFE_NAME = re.compile(r"[^\w .\-()]+", re.UNICODE)
_MAX_NAME_LEN = 60


def safe_display_name(name: str) -> str:
    """Dosya adını arayüzde gösterilebilir, zararsız bir metne indirger."""
    cleaned = _SAFE_NAME.sub("", str(name)).strip()
    if len(cleaned) > _MAX_NAME_LEN:
        cleaned = cleaned[:_MAX_NAME_LEN] + "…"
    return cleaned or "yüklenen dosya"


@st.cache_data(show_spinner=False)
def load_mock() -> dict:
    """Paketle gelen sahte şirket verisini okur (uygulama boş açılmasın)."""
    with open(DATA_PATH, encoding="utf-8") as f:
        return json.load(f)


def normalize_history(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aylık tabloyu grafiğin beklediği sütunlara tamamlar.

    Eksik sütunlar eskiden KeyError ile sayfayı çökertiyordu. Türetilebilenler
    türetilir (month -> sıra numarası, collections -> revenue); türetilemeyen
    (cash_end) eklenmez, grafik o izi atlayarak çizer.
    """
    df = df.copy()
    if "month" not in df.columns:
        df.insert(0, "month", [f"{i}. ay" for i in range(1, len(df) + 1)])
    if "collections" not in df.columns and "revenue" in df.columns:
        df["collections"] = df["revenue"]      # tahsilat verilmemiş: gelir = tahsilat
    return df


# Türk Excel'i CSV'yi cp1254 kodlamayla ve NOKTALI VİRGÜLLE yazar (virgül
# ondalık ayırıcı olduğu için). Varsayılan pd.read_csv ikisini de kaçırıyordu:
# kodlama hatası dosyayı reddediyor, yanlış ayırıcı ise hata bile atmadan
# tek sütunlu bir tablo üretip sessizce None döndürüyordu.
_ENCODINGS = ("utf-8-sig", "cp1254", "latin-1")   # sıra önemli: BOM'lu UTF-8 önce
_SEPARATORS = (",", ";", "\t")


def _read_table(file) -> pd.DataFrame:
    """CSV/Excel'i kodlama ve ayırıcı kombinasyonlarını deneyerek okur."""
    if file.name.lower().endswith((".xlsx", ".xls")):
        return pd.read_excel(file)

    son_hata = None
    for enc in _ENCODINGS:
        for sep in _SEPARATORS:
            try:
                file.seek(0)
                df = pd.read_csv(file, encoding=enc, sep=sep)
            except Exception as e:      # noqa: BLE001 — sıradaki kombinasyona geç
                son_hata = e
                continue
            # Ayırıcı doğruysa en az iki sütun çıkar; yanlışsa pandas hata
            # atmadan her satırı tek sütuna sıkıştırır.
            if df.shape[1] >= 2:
                return df
    if son_hata:
        raise son_hata
    raise ValueError(
        "Sütunlar ayrıştırılamadı. Ayırıcı virgül, noktalı virgül veya sekme olmalı."
    )


def _to_float(value) -> float:
    """
    Türkçe biçimli sayıyı float'a çevirir: '₺5.000.000,50' -> 5000000.5

    Eskiden düz float() çağrılıyordu; '5.000.000' gibi bir değer patlıyor,
    satır sessizce atlanıyor ve o alanda MOCK ŞİRKETİN rakamı ekranda
    kalıyordu. Kullanıcı kendi verisini yüklediğini sanırken başkasının
    sayısına bakıyordu — bu uygulamanın uyardığı hatanın ta kendisi.
    """
    if isinstance(value, bool):
        raise ValueError("mantıksal değer sayı değil")
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    for kirpilacak in ("₺", "TL", "tl", "$", "€", "%", " ", " "):
        s = s.replace(kirpilacak, "")
    if not s:
        raise ValueError("boş değer")

    if "," in s and "." in s:
        # En sağdaki ayırıcı ondalıktır: '1.234,56' (TR) / '1,234.56' (EN)
        s = (s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".")
             else s.replace(",", ""))
    elif "," in s:
        s = s.replace(",", ".")                       # TR ondalık: '3,5'
    elif s.count(".") > 1:
        s = s.replace(".", "")                        # '5.000.000' binlik
    elif "." in s:
        tam, _, kesir = s.rpartition(".")
        # '5.000' TR'de binliktir, EN'de 5.0 — bu uygulama TR odaklı olduğu
        # için tam 3 haneli son grup binlik sayılır.
        if len(kesir) == 3 and tam.lstrip("-+").isdigit():
            s = s.replace(".", "")
    return float(s)


# Biçim A'da okunan alanlar ve şablondaki açıklamaları. Tek kaynak: hem
# ayrıştırıcı hem indirilen örnek şablon buradan beslenir. Ayrı ayrı yazılsalar
# kod değiştiğinde şablon sessizce yanlışa döner ve kullanıcı, uygulamanın
# görmediği bir alanı doldurup neden değişmediğini anlamaz.
BICIM_A_ALANLARI = {
    "current_cash": "Bugünkü kasa / banka toplamı",
    "avg_monthly_revenue": "Aylık ortalama FATURALANAN gelir",
    "avg_monthly_collections": "Aylık ortalama TAHSİL EDİLEN nakit",
    "avg_monthly_fixed_expense": "Aylık ortalama sabit gider",
    "existing_debt": "Mevcut toplam borç stoku",
    "existing_monthly_debt_service": "Aylık borç servisi (taksit)",
}


def ornek_sablon() -> bytes:
    """
    İndirilebilir Biçim A şablonu (UTF-8 BOM'lu, Excel Türkçe karakterleri
    bozmasın diye).

    Değerler örnek şirketten alınır: boş bir iskelet yerine dolu bir dosya,
    kullanıcıya beklenen büyüklük mertebesini de gösterir.
    """
    ornek = load_mock()
    satirlar = ["alan,deger,aciklama"]
    for alan, aciklama in BICIM_A_ALANLARI.items():
        satirlar.append(f"{alan},{ornek.get(alan, 0):.0f},{aciklama}")
    return ("﻿" + "\n".join(satirlar) + "\n").encode("utf-8")


def parse_uploaded(file) -> dict | None:
    """
    Kullanıcı CSV/Excel'ini esnekçe ayrıştırır.
    Başarısız olursa None döner ve uygulama mock veriye devam eder.
    """
    try:
        df = _read_table(file)
        df.columns = [str(c).strip().lower() for c in df.columns]
        cols = set(df.columns)

        base = load_mock().copy()
        for k in _MOCK_ONLY_KEYS:
            base.pop(k, None)
        base["company_name"] = "Yüklenen Şirket Verisi"
        base["as_of"] = f"{safe_display_name(file.name)} (yüklendi)"

        # ── Biçim A: iki sütunlu anahtar-değer ────────────────────────────
        if {"alan", "deger"} <= cols or {"field", "value"} <= cols:
            kcol = "alan" if "alan" in cols else "field"
            vcol = "deger" if "deger" in cols else "value"
            kv, atlanan = {}, []
            for k, v in zip(df[kcol], df[vcol]):
                ad = str(k).strip()
                try:
                    kv[ad] = _to_float(v)
                except (TypeError, ValueError):
                    atlanan.append(f"{ad}={v!r}")
            if atlanan:
                # Sessiz atlamak tehlikeliydi: o alanda mock şirketin rakamı
                # kalıyor ve kullanıcı farkı göremiyordu. Artık söylüyoruz.
                st.sidebar.warning(
                    "Sayıya çevrilemeyen satırlar atlandı, bu alanlarda örnek "
                    "veri gösteriliyor: " + ", ".join(atlanan[:5])
                    + ("…" if len(atlanan) > 5 else "")
                )
            for key in BICIM_A_ALANLARI:
                if key in kv:
                    base[key] = kv[key]
            # Tahsilat verilmediyse faturalanan gelire eşitle (alacak boşluğu = 0).
            base["avg_monthly_collections"] = kv.get(
                "avg_monthly_collections", base["avg_monthly_revenue"])
            return base

        # ── Biçim B: aylık geçmiş ─────────────────────────────────────────
        if {"revenue", "fixed_expense"} <= cols:
            base["avg_monthly_revenue"] = float(df["revenue"].mean())
            base["avg_monthly_fixed_expense"] = float(df["fixed_expense"].mean())
            if "cash_end" in cols:
                base["current_cash"] = float(df["cash_end"].iloc[-1])
            base["avg_monthly_collections"] = float(
                df["collections"].mean() if "collections" in cols else df["revenue"].mean())
            base["history"] = normalize_history(df).to_dict("records")
            return base

        # Buraya düşmek "dosya okundu ama biçimi tanınmadı" demek. Eskiden
        # sessizce None dönülüyordu: kullanıcı yükleme yapıyor, hiçbir şey
        # değişmiyor ve hiçbir açıklama görmüyordu.
        st.sidebar.error(
            "Dosya okundu ama sütunlar tanınmadı. Biçim A: 'alan,deger'. "
            f"Biçim B: month, revenue, fixed_expense… Bulunan sütunlar: "
            f"{', '.join(sorted(cols))[:120]}"
        )
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(f"Dosya okunamadı: {e}")
    return None
