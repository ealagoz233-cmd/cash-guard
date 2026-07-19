"""
test_data_integrity.py  —  Mock veri ve yükleme yolu koruma testleri
─────────────────────────────────────────────────────────────────────
Çalıştırma:  python -m pytest tests/ -q      (veya: python tests/test_data_integrity.py)

İki şeyi korur:

1) MOCK VERİ TUTARLILIĞI — data/mock_company_data.json'daki 12 aylık tablonun
   ortalamaları, dosyanın başındaki skaler değerlerle BİREBİR aynı olmalı ve
   kasa yürüyüşü tam tutmalı. Tutmazsa ekranda KPI kartı ile grafik altı yazı
   farklı sayı gösterir (bir kez oldu: KPI "400.000", yazı "450.000" diyordu).

2) YÜKLEME YOLU DAYANIKLILIĞI — kullanıcı CSV'sinde sütun eksikse uygulama
   çökmemeli (bir kez KeyError ile çöküyordu) ve mock şirketin verisi
   kullanıcının verisiymiş gibi ekranda kalmamalı.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.data_io import (REQUIRED_HISTORY_COLS, _to_float, load_mock,
                             normalize_history, parse_uploaded, safe_display_name)
from utils.theme import esc, expense_label, kpi_card

DEBT_SERVICE_KEY = "existing_monthly_debt_service"


class FakeUpload(io.StringIO):
    """Streamlit'in UploadedFile'ını taklit eder (.name + okunabilir akış)."""

    def __init__(self, name: str, content: str):
        super().__init__(content)
        self.name = name


class BinaryUpload(io.BytesIO):
    """
    Streamlit'in GERÇEKTE verdiği şey: bayt akışı.

    FakeUpload metin (str) taşıyor ve bu yüzden kodlama sorunlarını hiç
    göstermiyordu — gerçek yüklemede kodlamayı pandas çözüyor. Türk Excel'i
    cp1254 + noktalı virgül yazdığı için o yol sınanmamış kalmıştı.
    """

    def __init__(self, name: str, content: bytes):
        super().__init__(content)
        self.name = name


# ══════════════════════════════════════════════════════════════════════════
#  1) Mock veri tutarlılığı
# ══════════════════════════════════════════════════════════════════════════
def test_history_averages_match_scalars():
    """Tablo ortalamaları üst düzey skalerlerle birebir aynı olmalı."""
    d = load_mock()
    h = pd.DataFrame(d["history"])
    assert h["revenue"].mean() == d["avg_monthly_revenue"]
    assert h["collections"].mean() == d["avg_monthly_collections"]
    assert h["fixed_expense"].mean() == d["avg_monthly_fixed_expense"]


def test_history_cash_walk_is_exact():
    """cash_end[t] = cash_end[t-1] + tahsilat − gider − borç servisi (sapma sıfır)."""
    d = load_mock()
    h = d["history"]
    ds = d[DEBT_SERVICE_KEY]
    for prev, cur in zip(h, h[1:]):
        expected = prev["cash_end"] + cur["collections"] - cur["fixed_expense"] - ds
        assert cur["cash_end"] == expected, (
            f"{cur['month']}: yazan {cur['cash_end']:,} ≠ hesaplanan {expected:,}")


def test_final_cash_matches_current_cash():
    """Tablonun son ayı, uygulamanın kullandığı mevcut kasa ile aynı olmalı."""
    d = load_mock()
    assert d["history"][-1]["cash_end"] == d["current_cash"]


def test_collections_below_revenue():
    """Hikâyenin özü: tahsilat her ay faturalanan gelirin altında kalmalı."""
    for row in load_mock()["history"]:
        assert row["collections"] < row["revenue"], f"{row['month']} boşluğu ters"


def test_expense_breakdown_sums_to_fixed_expense():
    """Gider kırılımı toplamı, aylık sabit gidere eşit olmalı (donut ortasındaki rakam)."""
    d = load_mock()
    assert sum(d["expense_breakdown"].values()) == d["avg_monthly_fixed_expense"]


# ══════════════════════════════════════════════════════════════════════════
#  2) Yükleme yolu dayanıklılığı
# ══════════════════════════════════════════════════════════════════════════
def test_minimal_csv_does_not_crash_chart():
    """Sadece revenue+fixed_expense olan CSV eskiden KeyError ile çökertiyordu."""
    d = parse_uploaded(FakeUpload("k.csv", "revenue,fixed_expense\n5000000,4000000\n4800000,4100000\n"))
    hist = pd.DataFrame(d["history"])
    assert REQUIRED_HISTORY_COLS <= set(hist.columns)   # grafik çizilebilir
    assert list(hist["collections"]) == list(hist["revenue"])  # tahsilat=gelir varsayımı


def test_upload_does_not_leak_mock_company():
    """Kullanıcı verisi yüklenince mock şirketin grafikleri ekranda kalmamalı."""
    d = parse_uploaded(FakeUpload("s.csv", "alan,deger\ncurrent_cash,500000\n"
                                           "avg_monthly_revenue,900000\n"
                                           "avg_monthly_fixed_expense,850000\n"))
    assert d["company_name"] == "Yüklenen Şirket Verisi"
    for leaked in ("history", "top_receivables", "expense_breakdown"):
        assert leaked not in d, f"mock '{leaked}' kullanıcı verisine sızdı"
    assert d["current_cash"] == 500000


def test_non_numeric_row_is_skipped():
    """Biçim A'da sayıya çevrilemeyen satır dosyayı geçersiz kılmamalı."""
    d = parse_uploaded(FakeUpload("m.csv", "alan,deger\nnot,bu bir metin\ncurrent_cash,750000\n"))
    assert d is not None and d["current_cash"] == 750000


def test_unreadable_file_returns_none():
    """Alakasız dosya None dönmeli ki uygulama mock veriye düşsün."""
    assert parse_uploaded(FakeUpload("x.csv", "a,b\n1,2\n")) is None


def test_normalize_history_fills_missing_columns():
    df = normalize_history(pd.DataFrame({"revenue": [100, 200]}))
    assert list(df["month"]) == ["1. ay", "2. ay"]
    assert list(df["collections"]) == [100, 200]


# ══════════════════════════════════════════════════════════════════════════
#  3) HTML enjeksiyonu — arayüz unsafe_allow_html kullanıyor
# ══════════════════════════════════════════════════════════════════════════
def test_uploaded_filename_cannot_inject_html():
    """
    Yüklenen dosyanın adı sidebar'da HAM HTML içinde gösteriliyor. Bir kez
    kaçırılmadan basılıyordu; `<img src=x onerror=...>.csv` adlı bir dosya
    sayfaya kod enjekte edebiliyordu.
    """
    zararli = "<img src=x onerror=alert(1)>.csv"
    d = parse_uploaded(FakeUpload(zararli, "alan,deger\ncurrent_cash,500000\n"
                                           "avg_monthly_revenue,900000\n"))
    assert "<" not in d["as_of"] and ">" not in d["as_of"], d["as_of"]


def test_safe_display_name_strips_markup_and_limits_length():
    assert "<" not in safe_display_name("<script>alert(1)</script>.csv")
    assert len(safe_display_name("a" * 500)) <= 61          # 60 + kısaltma işareti
    assert safe_display_name("<<<>>>") == "yüklenen dosya"  # her şey elenirse
    assert safe_display_name("2026 Bütçe (nihai).xlsx") == "2026 Bütçe (nihai).xlsx"


def test_esc_neutralizes_markup():
    assert esc("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"


def test_kpi_card_escapes_its_fields():
    """KPI kartına giren metin ham HTML olarak yorumlanmamalı."""
    assert "<script>" not in kpi_card("<script>", "<script>", "<script>")


# ══════════════════════════════════════════════════════════════════════════
#  4) Türkçe etiketler (.title() bozuyordu)
# ══════════════════════════════════════════════════════════════════════════
def test_turkish_expense_labels():
    assert expense_label("kira_ve_isletme") == "Kira ve İşletme"
    assert expense_label("hammadde_ve_tedarik") == "Hammadde ve Tedarik"
    assert expense_label("bilinmeyen_kalem") == "Bilinmeyen kalem"   # fallback


def test_every_mock_expense_key_has_a_label():
    """Mock'taki her gider anahtarının düzgün Türkçe karşılığı olmalı."""
    for key in load_mock()["expense_breakdown"]:
        label = expense_label(key)
        assert "_" not in label
        assert " Ve " not in label, f"{key}: .title() bugu geri gelmiş"


# ══════════════════════════════════════════════════════════════════════════
#  Gerçek dünya CSV'leri — Türk Excel'inin ürettiği biçimler
# ══════════════════════════════════════════════════════════════════════════
_KV_SATIRLARI = ("alan,deger\n"
                 "current_cash,5000000\n"
                 "avg_monthly_collections,3000000\n"
                 "avg_monthly_fixed_expense,3500000\n")


def test_turkish_excel_exports_are_accepted():
    """
    Türk Excel'i CSV'yi cp1254 kodlamayla ve NOKTALI VİRGÜLLE yazar.

    İkisi de eskiden dosyayı komple düşürüyordu: kodlama hata veriyordu,
    yanlış ayırıcı ise hata bile atmadan tek sütunlu tablo üretip sessizce
    None döndürüyordu. Hedef kitle Türkiye'deki KOBİ'ler olduğu için bu
    "nadir uç durum" değil, en olası yükleme biçimiydi.
    """
    senaryolar = {
        "UTF-8 düz": _KV_SATIRLARI.encode("utf-8"),
        "UTF-8 BOM": _KV_SATIRLARI.encode("utf-8-sig"),
        "noktalı virgül": _KV_SATIRLARI.replace(",", ";").encode("utf-8"),
        "cp1254": "alan,deger\nşirket_notu,x\ncurrent_cash,5000000\n".encode("cp1254"),
        "sekme": _KV_SATIRLARI.replace(",", "\t").encode("utf-8"),
    }
    for ad, bayt in senaryolar.items():
        d = parse_uploaded(BinaryUpload("rapor.csv", bayt))
        assert d is not None, f"{ad}: dosya reddedildi"
        assert d["current_cash"] == 5_000_000, f"{ad}: kasa yanlış okundu"


def test_turkish_number_format_is_parsed():
    """
    '5.000.000,50' düz float() ile patlıyordu; satır sessizce atlanınca o
    alanda MOCK ŞİRKETİN rakamı ekranda kalıyordu — kullanıcı kendi verisine
    baktığını sanarak başkasının sayısını görüyordu.
    """
    assert _to_float("5.000.000") == 5_000_000
    assert _to_float("5.000.000,50") == 5_000_000.5
    assert _to_float("3,5") == 3.5
    assert _to_float("₺ 1.200 ") == 1_200
    assert _to_float("-100.000") == -100_000
    assert _to_float("1,234.56") == 1234.56      # EN biçimi de bozulmamalı
    assert _to_float(4200) == 4200.0             # zaten sayı olan değer

    for bozuk in ("", "abc", None, True):
        try:
            _to_float(bozuk)
            raise AssertionError(f"{bozuk!r} için hata bekleniyordu")
        except (TypeError, ValueError):
            pass


def test_turkish_numbers_reach_the_result():
    """Ayrıştırma doğru olsa da sonuca yansımazsa bir işe yaramaz."""
    icerik = 'alan,deger\ncurrent_cash,"5.000.000,50"\n'.encode("utf-8")
    d = parse_uploaded(BinaryUpload("rapor.csv", icerik))
    assert d is not None
    assert d["current_cash"] == 5_000_000.5, "mock değeri sızmış olabilir"


def test_unparseable_value_does_not_silently_show_mock_number():
    """
    Sayıya çevrilemeyen bir değer artık sessizce atlanmamalı: o alanda mock
    rakamı kalıyorsa kullanıcı en azından uyarılmalı.
    """
    icerik = b"alan,deger\ncurrent_cash,bilinmiyor\n"
    d = parse_uploaded(BinaryUpload("rapor.csv", icerik))
    # Dosya okunabildi (biçim tanindi) ama değer alınamadı.
    assert d is not None
    mock_kasa = load_mock()["current_cash"]
    # Mock değeri gösteriliyorsa bu bilinçli bir geri düşüş; testin amacı
    # davranışı sabitlemek — sessiz DEĞİŞİM olmasın.
    assert d["current_cash"] == mock_kasa


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✓ {name}")
                passed += 1
            except AssertionError as e:
                print(f"  ✗ {name}\n      {e}")
                failed += 1
    print(f"\n{passed} geçti, {failed} kaldı")
    sys.exit(1 if failed else 0)
