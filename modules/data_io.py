"""
data_io.py  —  Veri yükleme ve kullanıcı dosyası ayrıştırma
────────────────────────────────────────────────────────────
Mock şirket verisini okur ve kullanıcının yüklediği CSV/Excel'i uygulamanın
beklediği sözlük biçimine çevirir.

app.py'den ayrı bir modülde durmasının sebebi: yükleme yolu uygulamanın en
kırılgan kısmı (eksik sütun = çöken sayfa) ve testten geçirilebilmesi gerekiyor.
app.py bir Streamlit script'i olduğu için import edilemez, bu modül edilebilir.

Üç biçim desteklenir ve **birlikte** yüklenebilirler (çoklu dosya):
  A) 'alan,deger' (key-value)  — çekirdek skalerler + opsiyonel bilanço, gider
     dağılımı ve alacak bakiyesi.
  B) Aylık geçmiş tablosu [month, revenue, fixed_expense, collections, cash_end]
     — ortalamaları ve son kasa değerini bu tablodan türetir.
  C) Alacak yaşlandırma listesi [musteri, tutar, gecikme_gun] — yaşlandırma,
     DSO ve şüpheli alacak hesabını besler.

Neden üç ayrı dosya: bunlar kaynak sistemlerde de ayrı dururlar (mizan, aylık
rapor, yaşlandırma dökümü). Tek dosyaya sıkıştırmak kullanıcıyı elle birleştirme
işine sokardı. Yüklenenler tek bir şirket sözlüğünde birleştirilir; verilmeyen
grup için o özellik kendini gizler — uydurma varsayımla doldurulmaz.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "mock_company_data.json"

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

# Alacak tarafı. Verilmezse yaşlandırma/DSO/şüpheli alacak paneli GİZLENİR;
# tahmini bir bakiye uydurmak, olmayan bir bilgiyi varmış gibi göstermek olurdu.
BICIM_A_ALACAK = {
    "receivables_outstanding": "Toplam alacak bakiyesi (yaşlandırma paneli için)",
    "avg_collection_days": "Beyan ettiğin ortalama tahsilat günü (opsiyonel)",
}

# Altman Z-score bileşenleri. Eksikse skor ÜRETİLMEZ (bkz. modules/zscore.py):
# yarım veriyle hesaplanmış bir iflas skoru, hiç skor olmamasından tehlikelidir.
BICIM_A_BILANCO = {
    "total_assets": "Toplam varlık (Altman Z-score için)",
    "current_assets": "Dönen varlık",
    "current_liabilities": "Kısa vadeli yükümlülük",
    "total_liabilities": "Toplam yükümlülük",
    "retained_earnings": "Geçmiş yıl kârları (birikmiş)",
    "annual_depreciation": "Yıllık amortisman (aylık giderlerin içinde değilse)",
}

# Sayı OLMAYAN alanlar. Bunları da kabul etmek şart: aksi hâlde kullanıcı
# şablona tarih ya da sektör yazdığında "sayıya çevrilemedi" uyarısı alırdı —
# doğru şeyi yapıp uyarı almak, aracın kullanıcıyı cezalandırması demektir.
BICIM_A_METIN = {
    "company_name": "Şirket adı (ekranda ve PDF raporunda görünür)",
    "sector": "Sektör — üretim/imalat yazarsan Altman Z′ modeli seçilir",
    "as_of": "Veri tarihi YYYY-AA-GG (13 haftalık takvim ertesi gün başlar)",
}

# Gider dağılımı düz anahtarlarla verilir: 'gider_personel', 'gider_kira_ve_isletme'…
# 13 haftalık ufuk ödeme günlerini kalem ADINDAN bulduğu için bu isimler önemli;
# dağılım verilmezse haftalık tablo kurulur ama ay-içi bilgi taşımaz.
GIDER_ONEKI = "gider_"

# Biçim C sütun adları — Türkçe ve İngilizce kabul edilir.
_ALACAK_SUTUNLARI = {
    "musteri": ("musteri", "müşteri", "customer", "unvan", "cari"),
    "tutar": ("tutar", "amount", "bakiye", "borc", "borç"),
    "gecikme": ("gecikme_gun", "gecikme", "overdue_days", "gun", "gün", "vade_asimi"),
}


def _sutun_bul(cols: set[str], adaylar) -> str | None:
    return next((a for a in adaylar if a in cols), None)


# Kullanıcı kendi verisini yüklediğinde mock şirkete ait olup TAŞINMAMASI
# gereken alanlar: taşınırsa ekranda başka bir şirketin grafikleri kullanıcının
# rakamlarıymış gibi görünür.
#
# Elle sayılmıyor, YAZILABİLİR ALANLARDAN türetiliyor. "Hangi mock alanı
# sızabilir" sorusunun cevabı zaten "bir biçimin yazabildiği her alan"; ikisini
# ayrı tutmak, yeni bir alan eklendiğinde onu bu listeye eklemeyi unutmak
# demekti — ve unutmanın belirtisi, kullanıcının kendi raporunda örnek şirketin
# rakamını görmesi olurdu.
_MOCK_ONLY_KEYS = tuple({
    "history",              # Biçim B yazar
    "top_receivables",      # Biçim C yazar
    "expense_breakdown",    # Biçim A, gider_ önekiyle yazar
    "balance_sheet",        # Biçim A, bilanço grubuyla yazar
    *BICIM_A_ALACAK,        # alacak bakiyesi / tahsilat günü
    *BICIM_A_METIN,         # şirket adı, sektör, veri tarihi
})


def ornek_sablon() -> bytes:
    """
    İndirilebilir Biçim A şablonu (UTF-8 BOM'lu, Excel Türkçe karakterleri
    bozmasın diye).

    Değerler örnek şirketten alınır: boş bir iskelet yerine dolu bir dosya,
    kullanıcıya beklenen büyüklük mertebesini de gösterir. Bilanço ve gider
    satırları da içindedir — opsiyonel oldukları için silinebilirler ama
    şablonda görünmeselerdi kullanıcı o özelliklerin var olduğunu hiç
    öğrenemezdi.
    """
    ornek = load_mock()
    bilanco = ornek.get("balance_sheet", {})
    satirlar = ["alan,deger,aciklama"]
    for alan, aciklama in BICIM_A_ALANLARI.items():
        satirlar.append(f"{alan},{ornek.get(alan, 0):.0f},{aciklama}")
    satirlar.append("# ── opsiyonel: alacak tarafı ──,,"
                    "Vermezsen yaşlandırma paneli gizlenir")
    for alan, aciklama in BICIM_A_ALACAK.items():
        satirlar.append(f"{alan},{ornek.get(alan, 0):.0f},{aciklama}")
    satirlar.append("# ── opsiyonel: bilanço (Altman Z-score) ──,,"
                    "Eksikse skor üretilmez")
    for alan, aciklama in BICIM_A_BILANCO.items():
        satirlar.append(f"{alan},{bilanco.get(alan, 0):.0f},{aciklama}")
    satirlar.append("# ── opsiyonel: gider dağılımı (13 haftalık takvim) ──,,"
                    "Vermezsen ay-içi çukur görünmez")
    for kalem, tutar in (ornek.get("expense_breakdown") or {}).items():
        satirlar.append(f"{GIDER_ONEKI}{kalem},{tutar:.0f},"
                        f"Gider kalemi — 13 haftalık ödeme takvimi için")
    # Metin alanları BOŞ bırakılır. Sayılarda örnek değer büyüklük mertebesini
    # öğretir ama şirket adını/tarihini doldurmak farklı bir şey olur: kullanıcı
    # satırı silmezse örnek şirketin adı kendi raporuna yazılır.
    satirlar.append("# ── opsiyonel: metin alanları ──,,"
                    "Boş bırakılabilir")
    for alan, aciklama in BICIM_A_METIN.items():
        satirlar.append(f"{alan},,{aciklama}")
    return ("﻿" + "\n".join(satirlar) + "\n").encode("utf-8")


def ornek_alacak_sablonu() -> bytes:
    """İndirilebilir Biçim C şablonu: alacak yaşlandırma listesi."""
    satirlar = ["musteri,tutar,gecikme_gun"]
    for kalem in (load_mock().get("top_receivables") or []):
        satirlar.append(f'"{kalem["customer"]}",{kalem["amount"]:.0f},'
                        f'{kalem["overdue_days"]:.0f}')
    return ("﻿" + "\n".join(satirlar) + "\n").encode("utf-8")


def _bos_taban(etiket: str) -> dict:
    """Mock'tan anlatısal alanları arındırılmış bir başlangıç sözlüğü."""
    base = load_mock().copy()
    for k in _MOCK_ONLY_KEYS:
        base.pop(k, None)
    base["company_name"] = "Yüklenen Şirket Verisi"
    base["as_of"] = etiket
    return base


def _uygula_bicim_a(df: pd.DataFrame, base: dict) -> None:
    """Anahtar-değer tablosunu sözlüğe işler (skaler + bilanço + gider)."""
    cols = set(df.columns)
    kcol = "alan" if "alan" in cols else "field"
    vcol = "deger" if "deger" in cols else "value"

    kv, metin, atlanan = {}, {}, []
    for k, v in zip(df[kcol], df[vcol]):
        ad = str(k).strip().lower()
        # Yorum ve boş satırlar sessizce atlanır. Bu bir konfor değil, gereklilik:
        # şablonda ve README'de grupları ayıran '# ── bilanço ──' satırları var ve
        # bunlar "sayıya çevrilemedi" uyarısı üretseydi, dokümandaki örneği
        # kopyalayan kullanıcı doğru şeyi yaptığı hâlde uyarı alırdı.
        if not ad or ad.startswith("#"):
            continue
        if ad in BICIM_A_METIN:
            deger = str(v).strip()
            if deger and deger.lower() != "nan":
                metin[ad] = deger
            continue
        try:
            kv[ad] = _to_float(v)
        except (TypeError, ValueError):
            atlanan.append(f"{ad}={v!r}")
    if atlanan:
        # Sessiz atlamak tehlikeliydi: o alanda mock şirketin rakamı kalıyor
        # ve kullanıcı farkı göremiyordu. Artık söylüyoruz.
        st.sidebar.warning(
            "Sayıya çevrilemeyen satırlar atlandı, bu alanlarda örnek "
            "veri gösteriliyor: " + ", ".join(atlanan[:5])
            + ("…" if len(atlanan) > 5 else "")
        )

    for key in BICIM_A_ALANLARI:
        if key in kv:
            base[key] = kv[key]
    for key in BICIM_A_ALACAK:
        if key in kv:
            base[key] = kv[key]

    # Bilanço iç içe bir sözlükte durur; düz anahtarlardan toplanır. Kısmen
    # doldurulmuşsa da aynen aktarılır — eksik alanı burada tamamlamak yerine
    # zscore.py'nin "skor üretme" kararına bırakmak doğrusu.
    bilanco = {k: kv[k] for k in BICIM_A_BILANCO if k in kv}
    if bilanco:
        base["balance_sheet"] = bilanco

    gider = {k[len(GIDER_ONEKI):]: v for k, v in kv.items()
             if k.startswith(GIDER_ONEKI) and v > 0}
    if gider:
        base["expense_breakdown"] = gider

    # Metin alanları: dosya adından gelen etiketi kullanıcının kendi yazdığı
    # değer ezer. `as_of` gerçek bir tarihse 13 haftalık takvim ondan başlar.
    for ad, deger in metin.items():
        base[ad] = deger

    # Tahsilat verilmediyse faturalanan gelire eşitle (alacak boşluğu = 0).
    if "avg_monthly_collections" not in kv:
        base["avg_monthly_collections"] = base["avg_monthly_revenue"]


def _uygula_bicim_b(df: pd.DataFrame, base: dict) -> None:
    """Aylık geçmiş tablosunu sözlüğe işler."""
    cols = set(df.columns)
    base["avg_monthly_revenue"] = float(df["revenue"].mean())
    base["avg_monthly_fixed_expense"] = float(df["fixed_expense"].mean())
    if "cash_end" in cols:
        base["current_cash"] = float(df["cash_end"].iloc[-1])
    base["avg_monthly_collections"] = float(
        df["collections"].mean() if "collections" in cols else df["revenue"].mean())
    base["history"] = normalize_history(df).to_dict("records")


def _uygula_bicim_c(df: pd.DataFrame, base: dict) -> None:
    """
    Alacak yaşlandırma listesini sözlüğe işler.

    Bakiye ayrıca verilmediyse kalemlerin toplamı bakiye sayılır: listelenen
    alacaktan daha azını "toplam" diye kabul etmek defterin bir kısmını yok
    saymak olurdu.
    """
    cols = set(df.columns)
    mcol = _sutun_bul(cols, _ALACAK_SUTUNLARI["musteri"])
    tcol = _sutun_bul(cols, _ALACAK_SUTUNLARI["tutar"])
    gcol = _sutun_bul(cols, _ALACAK_SUTUNLARI["gecikme"])

    # Sütunlar üzerinden zip'lenerek gezilir, `df.iterrows()` ile DEĞİL: iterrows
    # her satır için bir Series nesnesi kurar ve 2000 satırlık bir yaşlandırma
    # dökümünde ~89 ms tutuyordu (zip ile ~7 ms). Biçim A tarafı zaten bu deyimi
    # kullanıyordu; ikisi aynı olmalı.
    n = len(df)
    tutarlar = df[tcol] if tcol else [None] * n
    gunler = df[gcol] if gcol else [None] * n
    musteriler = df[mcol] if mcol else [None] * n

    kalemler, atlanan = [], []
    for musteri, ham_tutar, ham_gun in zip(musteriler, tutarlar, gunler):
        try:
            tutar = _to_float(ham_tutar)
        except (TypeError, ValueError):
            atlanan.append(str(musteri)[:30] if musteri is not None else "?")
            continue
        if tutar <= 0:
            continue
        try:
            gun = _to_float(ham_gun) if gcol else 0.0
        except (TypeError, ValueError):
            gun = 0.0      # gün okunamadıysa "vadesinde" say; uydurma yaş atama
        kalemler.append({
            "customer": safe_display_name(musteri) if mcol else "—",
            "amount": tutar,
            "overdue_days": gun,
        })

    if atlanan:
        st.sidebar.warning(
            "Alacak listesinde tutarı okunamayan satırlar atlandı: "
            + ", ".join(atlanan[:5]) + ("…" if len(atlanan) > 5 else ""))
    if not kalemler:
        return

    kalemler.sort(key=lambda k: k["amount"], reverse=True)
    base["top_receivables"] = kalemler
    base.setdefault("receivables_outstanding", sum(k["amount"] for k in kalemler))


def _tani_ve_uygula(df: pd.DataFrame, base: dict) -> bool:
    """Tablonun biçimini tanıyıp uygular; tanınmazsa False döner."""
    cols = set(df.columns)
    if {"alan", "deger"} <= cols or {"field", "value"} <= cols:
        _uygula_bicim_a(df, base)
        return True
    if {"revenue", "fixed_expense"} <= cols:
        _uygula_bicim_b(df, base)
        return True
    if _sutun_bul(cols, _ALACAK_SUTUNLARI["tutar"]) and \
            _sutun_bul(cols, _ALACAK_SUTUNLARI["gecikme"]):
        _uygula_bicim_c(df, base)
        return True
    return False


def _oku_ve_normalize(file) -> pd.DataFrame:
    df = _read_table(file)
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def parse_uploaded(file) -> dict | None:
    """
    Tek bir kullanıcı CSV/Excel'ini esnekçe ayrıştırır.
    Başarısız olursa None döner ve uygulama mock veriye devam eder.
    """
    return parse_uploaded_files([file])


def parse_uploaded_files(files) -> dict | None:
    """
    Birden fazla dosyayı TEK bir şirket sözlüğünde birleştirir.

    Kaynak sistemlerde mizan, aylık rapor ve yaşlandırma dökümü ayrı dosyalardır;
    kullanıcıyı bunları elle birleştirmeye zorlamak yerine hepsi kabul edilir.
    Sıra önemsizdir: her dosya kendi alanlarını yazar, dokunmadığı alan
    diğerinden gelir. Hiçbiri tanınmazsa None döner ve mock veriye devam edilir.
    """
    dosyalar = [f for f in (files or []) if f is not None]
    if not dosyalar:
        return None

    adlar = ", ".join(safe_display_name(f.name) for f in dosyalar[:3])
    base = _bos_taban(f"{adlar} (yüklendi)")

    taninan = 0
    for file in dosyalar:
        try:
            df = _oku_ve_normalize(file)
        except Exception as e:  # noqa: BLE001
            st.sidebar.error(f"{safe_display_name(file.name)} okunamadı: {e}")
            continue

        try:
            if _tani_ve_uygula(df, base):
                taninan += 1
                continue
        except Exception as e:  # noqa: BLE001
            st.sidebar.error(f"{safe_display_name(file.name)} işlenemedi: {e}")
            continue

        # Buraya düşmek "dosya okundu ama biçimi tanınmadı" demek. Eskiden
        # sessizce None dönülüyordu: kullanıcı yükleme yapıyor, hiçbir şey
        # değişmiyor ve hiçbir açıklama görmüyordu.
        st.sidebar.error(
            f"{safe_display_name(file.name)}: sütunlar tanınmadı. "
            "Biçim A: 'alan,deger'. Biçim B: month, revenue, fixed_expense… "
            "Biçim C: musteri, tutar, gecikme_gun. Bulunan sütunlar: "
            f"{', '.join(sorted(df.columns))[:120]}"
        )

    return base if taninan else None
