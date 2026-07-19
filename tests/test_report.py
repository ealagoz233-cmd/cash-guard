"""
test_report.py — PDF raporunun üretilebildiğini ve Türkçe'yi taşıdığını doğrular.

Neden bu dosya var: font seçimi bir zamanlar SADECE C:/Windows/Fonts/ altına
bakıyordu. Windows'ta her şey güzel görünüyordu, ama Linux'a (Streamlit Cloud)
deploy edilseydi reportlab sessizce Helvetica'ya düşer ve ğ ı ş İ karakterleri
bozulurdu — hiçbir test patlamazdı, çünkü PDF yine "başarıyla" üretiliyordu.

Buradaki testler tam olarak o sessiz bozulmayı yakalar ve CI'da Linux üzerinde
koştuğu için deploy hedefini de kapsar.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reportlab.pdfbase import pdfmetrics

from modules import report

# Türkçe'nin ASCII dışına taşan ve en sık bozulan harfleri.
TURKISH_CHARS = "çÇğĞıİöÖşŞüÜ"


def _ctx():
    """build_report'un beklediği asgari bağlam — Türkçe karakter içerir."""
    return {
        "company_name": "Çağrışım Yazılım A.Ş.",
        "sector": "Bilişim & Mühendislik",
        "as_of": "2026-07",
        "currency_symbol": "TL",
        "current_cash": 4_200_000,
        "ruin_pct": 63.4,
        "expected_ruin_month": 7,
        "monthly_net": -850_000,
        "net_operating": -640_000,
        "debt_service": 950_000,
        "loan_amount": 10_000_000,
        "installment": 620_000,
        "total_interest": 4_880_000,
        "relief_months": 5,
        "default_with_loan": 14,
        "base_ruin_pct": 63.4,
        "loan_ruin_pct": 41.2,
        "runway_months": 5,
        "trend_runway_months": 3,
        "cfo_text": "Kasa eriyor. Şirketin ömrü üç ay.\n1. Gereksiz gideri kes.",
        "cfo_source": "Kural Tabanlı Motor",
    }


def test_turkish_capable_font_is_registered():
    """
    Kayıtlı font Türkçe harfleri GERÇEKTEN çizebilmeli.

    Helvetica'ya düşülmüş olması tek başına yeterli kanıt değil — asıl soru
    fontun glyph tablosunda bu karakterlerin bulunup bulunmadığı. TrueType
    yüzünü açıp charToGlyph eşlemesine bakıyoruz.
    """
    assert report._FONT != "Helvetica", (
        "Türkçe destekli TrueType font bulunamadı, Helvetica'ya düşüldü. "
        "Bu platformda ğ ı ş İ bozulur. Linux'ta çözüm: fonts-dejavu-core "
        "kur (repodaki packages.txt bunu Streamlit Cloud için hallediyor)."
    )
    face = pdfmetrics.getFont(report._FONT).face
    eksik = [c for c in TURKISH_CHARS if ord(c) not in face.charToGlyph]
    assert not eksik, f"kayıtlı fontta glyph'i olmayan harfler: {''.join(eksik)}"


def test_bold_font_is_registered_too():
    """Başlıklar kalın yüzü kullanıyor; o da aynı kapsamda olmalı."""
    assert report._FONT_B != "Helvetica-Bold", "kalın font Helvetica'ya düştü"
    face = pdfmetrics.getFont(report._FONT_B).face
    eksik = [c for c in TURKISH_CHARS if ord(c) not in face.charToGlyph]
    assert not eksik, f"kalın fontta glyph'i olmayan harfler: {''.join(eksik)}"


def test_build_report_returns_a_real_pdf():
    """Çıktı gerçekten PDF olmalı ve boş sayılmayacak kadar dolu."""
    data = report.build_report(_ctx())
    assert isinstance(data, (bytes, bytearray)), "bytes dönmedi"
    assert data[:5] == b"%PDF-", "PDF imzası yok"
    assert data.rstrip().endswith(b"%%EOF"), "PDF düzgün kapanmamış"
    assert len(data) > 3000, f"PDF şüpheli derecede küçük: {len(data)} bayt"


def test_report_survives_missing_optional_fields():
    """
    Sağlıklı şirkette iflas ayı/kredi alanları None gelir. Rapor bu durumda
    da üretilmeli — bir KPI eksik diye yönetim kurulu raporu patlayamaz.
    """
    ctx = _ctx()
    ctx.update(expected_ruin_month=None, default_with_loan=None,
               loan_ruin_pct=None, runway_months=None, trend_runway_months=None,
               sector="", ruin_pct=0.0, monthly_net=2_225_000)
    data = report.build_report(ctx)
    assert data[:5] == b"%PDF-"


def test_markup_in_text_fields_does_not_break_pdf():
    """
    reportlab'ın Paragraph'ı mini-XML ayrıştırıcıdır: kaçırılmamış bir '<'
    ya bozuk çıktı ya ValueError demek.

    Gerçek senaryo: kullanıcı şirket adı 'Acme <b>Tekstil' olan bir CSV
    yükler, "Rapor indir"e basar ve uygulama çöker. Bu test o çökmeyi
    yeniden üretmek için yazıldı — düzeltmeden önce kapanmamış etiket
    ValueError atıyordu.
    """
    tuzaklar = [
        "Acme <b>Tekstil A.Ş.",           # kapanmamış etiket — eskiden ValueError
        "Acme </b> Ortakları",            # eşleşmeyen kapanış
        "Acme <script>alert(1)</script>",
        "Acme < Ortakları > Ltd.",        # ham karşılaştırma işaretleri
        "Acme & Co <font size=99>",
    ]
    for ad in tuzaklar:
        for alan in ("company_name", "sector", "as_of", "cfo_source"):
            ctx = _ctx()
            ctx[alan] = ad
            data = report.build_report(ctx)
            assert data[:5] == b"%PDF-", f"{alan}={ad!r} PDF üretimini bozdu"


def test_markup_in_cfo_text_does_not_break_pdf():
    """CFO metni LLM'den geliyor; içindeki etiket PDF'i patlatmamalı."""
    ctx = _ctx()
    ctx["cfo_text"] = "Kasa eriyor.\n1. <b>Kapanmamış kalın.\n2. <img src=x> ve & işareti."
    assert report.build_report(ctx)[:5] == b"%PDF-"


def test_currency_symbol_falls_back_to_TL():
    """₺ glyph'i her fontta yok; rapor onu TL'ye çevirip riski kesiyor."""
    ctx = _ctx()
    ctx["currency_symbol"] = "₺"
    data = report.build_report(ctx)
    assert data[:5] == b"%PDF-"


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
