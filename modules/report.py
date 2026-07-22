"""
report.py  —  Yönetim kuruluna sunulacak PDF rapor üretimi
───────────────────────────────────────────────────────────
Cash Guard analizini (KPI özeti + kredi senaryosu + senaryo karşılaştırması +
Acımasız CFO aksiyon planı) tek tıkla indirilebilir, kurumsal görünümlü bir PDF'e
döker. reportlab (saf Python) kullanır; grafik gömmeye çalışmaz — baskıya ve
imzaya uygun, temiz metin/tablo düzeni hedeflenir.

Türkçe karakterler için Windows'un Arial fontu TrueType olarak kaydedilir
(ç, ğ, ı, ş, İ, ö, ü tam desteklenir). Font bulunamazsa Helvetica'ya düşer.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# ── Marka renkleri (baskı için beyaz zemin + vurgu renkleri) ──────────────
GUARDIAN = colors.HexColor("#0B9E75")
ALARM = colors.HexColor("#D62839")
AMBER = colors.HexColor("#C9820A")
INK = colors.HexColor("#12202E")
MUTED = colors.HexColor("#5A6B7B")
PANEL = colors.HexColor("#F2F6F8")
LINE = colors.HexColor("#D8E0E6")

# ── Türkçe uyumlu font kaydı (Windows Arial) ──────────────────────────────
_FONT, _FONT_B = "Helvetica", "Helvetica-Bold"


def _register_fonts() -> None:
    """
    Türkçe destekli bir TrueType font kaydeder.

    Adaylar Windows + Linux + macOS'u kapsar: uygulama Linux bir sunucuya
    (ör. Streamlit Cloud) kurulduğunda Windows fontları bulunamıyor ve
    Helvetica'ya düşülüyordu. Helvetica'nın WinAnsi kodlaması ç/ö/ü'yü taşır
    ama ğ, ı, ş, İ harflerini TAŞIMAZ — yani rapor sessizce bozuk Türkçe
    basardı. DejaVu ve Liberation neredeyse her Linux dağıtımında bulunur.
    """
    global _FONT, _FONT_B
    candidates = [
        # Windows
        ("CG", "CG-Bold", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("CG", "CG-Bold", "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
        # Linux (Debian/Ubuntu — Streamlit Cloud dahil)
        ("CG", "CG-Bold",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("CG", "CG-Bold",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        # macOS
        ("CG", "CG-Bold", "/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
        ("CG", "CG-Bold",
         "/System/Library/Fonts/Supplemental/Arial.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for reg, regb, path, pathb in candidates:
        try:
            if Path(path).exists() and Path(pathb).exists():
                pdfmetrics.registerFont(TTFont(reg, path))
                pdfmetrics.registerFont(TTFont(regb, pathb))
                _FONT, _FONT_B = reg, regb
                return
        except Exception:
            continue  # kayıt başarısızsa Helvetica ile devam


_register_fonts()


def _money(value, sym: str = "TL") -> str:
    """4200000 -> 'TL 4.200.000'. ₺ glyph riskine girmemek için varsayılan 'TL'."""
    try:
        s = f"{abs(value):,.0f}".replace(",", ".")
    except (ValueError, TypeError):
        return f"{sym} 0"
    return f"{'-' if value < 0 else ''}{sym} {s}"


def _esc(value) -> str:
    """
    reportlab'ın Paragraph'ı mini bir XML ayrıştırıcısıdır: <b>, <i>, <font>
    gibi etiketleri yorumlar. Kaçırılmamış bir '<' iyi ihtimalle bozuk çıktı,
    kötü ihtimalle ValueError demek — nitekim şirket adında kapanmamış bir
    <b> varken PDF üretimi tamamen çöküyordu.

    Bu yüzden Paragraph'a giren HER dış kaynaklı metin buradan geçmeli:
    şirket adı, sektör, veri tarihi, CFO motorunun adı. Hepsi yüklenen
    dosyadan ya da LLM'den geliyor; hiçbiri güvenilir değil.
    """
    return (str(value).replace("&", "&amp;")
                      .replace("<", "&lt;")
                      .replace(">", "&gt;"))


def _font_has(char: str) -> bool:
    """Kayıtlı fontun bu karakter için gerçekten bir glyph'i var mı?"""
    try:
        face = pdfmetrics.getFont(_FONT).face
        return ord(char) in face.charToGlyph
    except Exception:
        # Helvetica gibi gömülü Type1 fontlarda charToGlyph yok. Bu durumda
        # zaten Türkçe de bozuk demektir; güvenli tarafta kal.
        return False


def _usable_symbol(sym: str) -> str:
    """
    Para birimi simgesini fonta SORARAK seçer.

    Eskiden '₺' koşulsuz 'TL'ye çevriliyordu ("glyph riski" gerekçesiyle).
    Sonuç: tablolarda TL, CFO metninde ₺ yazıyordu — aynı raporda iki farklı
    gösterim. Oysa risk ölçülebilir bir şey: fontta glyph varsa ₺ kullan,
    yoksa TL'ye düş. Karar tek yerde verilir ve CFO metnine de uygulanır,
    böylece belge her koşulda kendi içinde tutarlı kalır.
    """
    return sym if (sym != "₺" or _font_has("₺")) else "TL"


def _align_currency(text: str, sym: str) -> str:
    """
    CFO metnindeki para simgesini raporun kullandığı simgeye hizalar.

    CFO metni PDF'ten ÖNCE üretiliyor ve raporun hangi simgeyi seçeceğini
    bilmiyor. Rapor ₺'yi TL'ye düşürdüyse metin de düşmeli; yoksa tablolarda
    TL, aksiyon planında ₺ yazar — gerçek çıktıda görülen kusur buydu.
    """
    return text if sym == "₺" else text.replace("₺", sym)


def _md_to_rl(text: str) -> str:
    """**kalın** -> <b>kalın</b>; XML özel karakterlerini kaçır."""
    # Sıra önemli: önce kaçır, SONRA kendi etiketimizi ekle. Tersi olsaydı
    # az önce ürettiğimiz <b>'yi de kaçırırdık.
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _esc(text))


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName=_FONT_B,
                                fontSize=22, textColor=INK, spaceAfter=2, leading=26),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontName=_FONT,
                              fontSize=10, textColor=MUTED, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName=_FONT_B,
                            fontSize=13, textColor=GUARDIAN, spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("b", parent=base["Normal"], fontName=_FONT,
                              fontSize=10.5, textColor=INK, leading=15, alignment=TA_LEFT),
        "action": ParagraphStyle("a", parent=base["Normal"], fontName=_FONT,
                                 fontSize=10.5, textColor=INK, leading=15,
                                 leftIndent=6, spaceAfter=5),
        "foot": ParagraphStyle("f", parent=base["Normal"], fontName=_FONT,
                              fontSize=8, textColor=MUTED, leading=11),
        "cellL": ParagraphStyle("cl", fontName=_FONT, fontSize=10, textColor=MUTED),
        "cellV": ParagraphStyle("cv", fontName=_FONT_B, fontSize=11, textColor=INK),
    }


def _kv_table(rows, S, value_colors=None):
    """[(etiket, değer)] -> iki sütunlu şık tablo."""
    value_colors = value_colors or {}
    data = []
    for i, (label, value) in enumerate(rows):
        vstyle = ParagraphStyle(f"v{i}", parent=S["cellV"],
                                textColor=value_colors.get(i, INK))
        data.append([Paragraph(label, S["cellL"]), Paragraph(str(value), vstyle)])
    t = Table(data, colWidths=[70 * mm, 100 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return t


def build_report(ctx: dict) -> bytes:
    """
    Analiz bağlamından PDF üretir ve bytes döndürür (st.download_button için).

    Beklenen ctx anahtarları app.py tarafından doldurulur:
        company_name, sector, as_of, currency_symbol,
        current_cash, ruin_pct, expected_ruin_month, monthly_net, net_operating,
        debt_service, loan_amount, installment, total_interest, relief_months,
        default_with_loan, base_ruin_pct, loan_ruin_pct (None olabilir),
        cfo_text, cfo_source
    """
    sym = _usable_symbol(ctx.get("currency_symbol", "TL"))
    S = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
        leftMargin=18 * mm, rightMargin=18 * mm,
        title="Cash Guard Raporu", author="Cash Guard",
    )
    story = []

    # ── Başlık ────────────────────────────────────────────────────────────
    story.append(Paragraph("CASH GUARD", S["title"]))
    story.append(Paragraph("Kurumsal Nakit Hayatta Kalma &amp; Kredi Stres Testi Raporu",
                           S["sub"]))
    story.append(Spacer(1, 4))
    meta = _esc(ctx.get("company_name", "—"))
    if ctx.get("sector"):
        meta += f"  ·  {_esc(ctx['sector'])}"
    meta += f"  ·  Veri tarihi: {_esc(ctx.get('as_of', '—'))}"
    story.append(Paragraph(meta, S["sub"]))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.2, color=GUARDIAN))

    # ── Özet KPI ──────────────────────────────────────────────────────────
    story.append(Paragraph("1 · Yönetici Özeti", S["h2"]))
    ruin = ctx["ruin_pct"]
    ruin_col = ALARM if ruin >= 60 else AMBER if ruin >= 30 else GUARDIAN
    em = ctx.get("expected_ruin_month")
    kpi_rows = [
        ("Mevcut Kasa", _money(ctx["current_cash"], sym)),
        ("12 Ay İçinde Batma Olasılığı", f"%{ruin:.1f}"),
        ("Beklenen İflas Ayı (stresli)", f"{em:.0f}. ay" if em else "Ufukta yok"),
        ("Aylık Net Nakit Akışı (baz)", _money(ctx["monthly_net"], sym)),
    ]
    kpi_colors = {1: ruin_col,
                  3: ALARM if ctx["monthly_net"] < 0 else GUARDIAN}

    # Nakit ömrü: statik hesap ile trend hesabı yan yana. İkisi arasındaki fark
    # yönetim kurulunun görmesi gereken asıl bilgi.
    static_rw, trend_rw = ctx.get("runway_months"), ctx.get("trend_runway_months")
    if static_rw and trend_rw:
        kpi_rows.append(("Nakit Ömrü (sabit gidiş / bozulma trendi)",
                         f"~{static_rw:.0f} ay  /  ~{trend_rw} ay"))
        kpi_colors[len(kpi_rows) - 1] = ALARM if trend_rw <= 12 else AMBER
    elif static_rw:
        kpi_rows.append(("Nakit Ömrü (sabit gidişle)", f"~{static_rw:.0f} ay"))

    # Yapısal hüküm: bilanço ne diyor? Nakit hükmüyle çeliştiğinde yönetim
    # kurulunun ilk soracağı soru bu olur, cevabı raporda hazır dursun.
    z_skor, z_bolge = ctx.get("z_score"), ctx.get("z_zone")
    if z_skor is not None and z_bolge:
        kpi_rows.append((f"Altman Z-Skoru ({_esc(z_bolge)} bölge)", f"{z_skor:.2f}"))
        kpi_colors[len(kpi_rows) - 1] = (
            GUARDIAN if z_bolge == "Güvenli" else AMBER if z_bolge == "Gri" else ALARM)

    # Alacak kalitesi: geciken para ile hiç gelmeyecek parayı ayır.
    supheli = ctx.get("expected_uncollectible")
    if supheli:
        dso = ctx.get("dso_days")
        etiket = ("Şüpheli Alacak (tahsil edilemeyebilir)"
                  + (f" · DSO {dso:.0f} gün" if dso else ""))
        kpi_rows.append((etiket, _money(supheli, sym)))
        kpi_colors[len(kpi_rows) - 1] = ALARM

    # Ay-içi dip: aylık tabloların hiçbirinde görünmeyen an. Yönetim kurulu
    # kredi limitini ay sonuna göre değil buna göre ayarlamalı.
    dip_kasa = ctx.get("weekly_min_cash")
    if dip_kasa is not None:
        dip_hafta = ctx.get("weekly_min_week")
        kpi_rows.append((f"13 Hafta İçindeki En Dip Kasa"
                         + (f" ({_esc(dip_hafta)})" if dip_hafta else ""),
                         _money(dip_kasa, sym)))
        kpi_colors[len(kpi_rows) - 1] = ALARM if dip_kasa < 0 else AMBER

    # En büyük kaldıraç: "neyi düzeltirsen ne kazanırsın" tek satırda.
    kaldirac = ctx.get("top_driver")
    if kaldirac:
        kpi_rows.append(("Batma Riskini En Çok Oynatan Değişken",
                         f"{_esc(kaldirac)}  ({ctx.get('top_driver_swing', 0):.1f} puan)"))
        kpi_colors[len(kpi_rows) - 1] = AMBER

    story.append(_kv_table(kpi_rows, S, kpi_colors))

    # Ay-içi çukurun derinliği tabloya sığmaz; asıl mesaj cümlede.
    bosluk = ctx.get("weekly_intramonth_gap")
    if dip_kasa is not None and bosluk:
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"<b>Ay-içi uyarı:</b> 13 haftalık takvimde dönem sonu kasası "
            f"{_money(ctx.get('weekly_end_cash', 0), sym)} görünürken en dip hafta "
            f"{_money(dip_kasa, sym)}'ye iniyor — aradaki {_money(bosluk, sym)} "
            f"aylık ortalamanın içinde kaybolan gerçek bir daralmadır. Kredi "
            f"limiti, teminat ve tedarikçi vadesi pazarlıkları ay sonuna değil "
            f"o haftaya göre kurulmalıdır.", S["body"]))

    # İki modelin çelişmesi raporun en değerli cümlesi; tabloya sığmaz, yazıyla.
    if z_skor is not None and z_bolge == "Güvenli" and ruin >= 60:
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"<b>Dikkat:</b> Bilanço temelli Altman skoru ({z_skor:.2f}) şirketi "
            f"<b>güvenli</b> bölgede gösterirken nakit modeli 12 ayda "
            f"<b>%{ruin:.1f}</b> batma olasılığı veriyor. Çelişki değil, yöntem "
            f"farkı: Altman tahakkuk esaslı yıllık bir fotoğraf çeker ve nakdin "
            f"<b>ne zaman</b> geldiğini görmez. Şirketler tam olarak böyle batar — "
            f"kârlı görünerek, nakitsiz.", S["body"]))

    # ── Kredi senaryosu ───────────────────────────────────────────────────
    story.append(Paragraph("2 · Kredi Senaryosu Analizi", S["h2"]))
    relief = ctx.get("relief_months", 0)
    loan_amt = ctx.get("loan_amount", 0)
    if loan_amt > 0:
        if relief < 0:
            effect = f"BORÇ TUZAĞI — iflası {abs(relief)} ay öne çekiyor"
            eff_col = ALARM
        elif relief <= 6:
            effect = f"+{relief} ay (sadece morfin)"
            eff_col = AMBER
        else:
            effect = f"+{relief} ay nefes aralığı"
            eff_col = GUARDIAN
        loan_rows = [
            ("Çekilmesi Düşünülen Kredi", _money(loan_amt, sym)),
            ("Yeni Aylık Taksit", _money(ctx.get("installment", 0), sym)),
            ("Vade Sonu Toplam Faiz", _money(ctx.get("total_interest", 0), sym)),
            ("Kredinin İflasa Etkisi", effect),
        ]
        story.append(_kv_table(loan_rows, S, {3: eff_col}))
    else:
        story.append(Paragraph("Bu raporda aktif bir kredi senaryosu tanımlanmadı.",
                               S["body"]))

    # ── Senaryo karşılaştırması ───────────────────────────────────────────
    base_p = ctx.get("base_ruin_pct")
    loan_p = ctx.get("loan_ruin_pct")
    if base_p is not None and loan_p is not None:
        story.append(Paragraph("3 · Senaryo Karşılaştırması (12 ay batma olasılığı)", S["h2"]))
        cmp_rows = [
            ("Mevcut Hal (kredisiz)", f"%{base_p:.1f}"),
            (f"{_money(loan_amt, sym)} Kredi Çekersen", f"%{loan_p:.1f}"),
        ]
        story.append(_kv_table(cmp_rows, S,
                               {0: (ALARM if base_p >= 60 else AMBER),
                                1: (ALARM if loan_p >= 60 else AMBER if loan_p >= 30 else GUARDIAN)}))
        if loan_p + 1.5 < base_p and relief < 0:
            story.append(Spacer(1, 4))
            story.append(Paragraph(
                "<b>Uyarı:</b> Kredi 12 aylık riski düşürüp rahatlatıyormuş gibi görünse de, "
                "uzun vadede iflası öne çeken bir borç tuzağıdır. Kısa vadeli rahatlamaya "
                "aldanmayın.", S["body"]))

    # ── CFO aksiyon planı ─────────────────────────────────────────────────
    story.append(Paragraph(
        f"4 · Acımasız CFO — Aksiyon Planı  ({_esc(ctx.get('cfo_source', ''))})",
        S["h2"]))
    for block in _align_currency(ctx.get("cfo_text", "") or "", sym).split("\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("**AKSİYON") or block.upper().startswith("AKSİYON"):
            story.append(Spacer(1, 2))
            story.append(Paragraph(_md_to_rl(block), S["h2"]))
        elif re.match(r"^\d+\.", block):
            story.append(Paragraph(_md_to_rl(block), S["action"]))
        else:
            story.append(Paragraph(_md_to_rl(block), S["body"]))
            story.append(Spacer(1, 4))

    # ── Dipnot ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.6, color=LINE))
    story.append(Spacer(1, 3))
    story.append(Paragraph(
        f"Bu rapor Cash Guard karar-destek prototipi tarafından "
        f"{datetime.now():%d.%m.%Y %H:%M} tarihinde üretilmiştir. Sayısal varsayımlar "
        f"örnektir; yatırım veya finans tavsiyesi niteliği taşımaz.", S["foot"]))

    doc.build(story)
    return buf.getvalue()
