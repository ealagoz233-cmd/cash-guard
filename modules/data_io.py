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


def parse_uploaded(file) -> dict | None:
    """
    Kullanıcı CSV/Excel'ini esnekçe ayrıştırır.
    Başarısız olursa None döner ve uygulama mock veriye devam eder.
    """
    try:
        name = file.name.lower()
        df = pd.read_excel(file) if name.endswith((".xlsx", ".xls")) else pd.read_csv(file)
        df.columns = [str(c).strip().lower() for c in df.columns]
        cols = set(df.columns)

        base = load_mock().copy()
        for k in _MOCK_ONLY_KEYS:
            base.pop(k, None)
        base["company_name"] = "Yüklenen Şirket Verisi"
        base["as_of"] = f"{file.name} (yüklendi)"

        # ── Biçim A: iki sütunlu anahtar-değer ────────────────────────────
        if {"alan", "deger"} <= cols or {"field", "value"} <= cols:
            kcol = "alan" if "alan" in cols else "field"
            vcol = "deger" if "deger" in cols else "value"
            kv = {}
            for k, v in zip(df[kcol], df[vcol]):
                try:
                    kv[str(k).strip()] = float(v)
                except (TypeError, ValueError):
                    continue          # sayıya çevrilemeyen satırı sessizce atla
            for key in ("current_cash", "avg_monthly_revenue", "avg_monthly_collections",
                        "avg_monthly_fixed_expense", "existing_debt",
                        "existing_monthly_debt_service"):
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
    except Exception as e:  # noqa: BLE001
        st.sidebar.error(f"Dosya okunamadı: {e}")
    return None
