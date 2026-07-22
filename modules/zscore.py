"""
zscore.py  —  Altman Z-score: yapısal (bilanço) iflas riski
────────────────────────────────────────────────────────────
Cash Guard'ın bütün hesapları nakit akışına bakar: "önümüzdeki 12 ayda kasa
sıfırlanır mı?" Bu iyi bir soru ama tek soru değil. Bir de yapı sorusu var:
"bilançom, sektörde batan şirketlerin bilançosuna ne kadar benziyor?"

Altman Z-score tam olarak bunu ölçer ve muhasebe temelli iflas tahmin
modellerinin en çok test edilmişidir (1968 makalesi 26.000'den fazla atıf
almıştır). Cash Guard'a eklenmesinin sebebi ikisinin AYNI ŞEYİ söylemesi değil,
tam tersi: sık sık farklı şey söylerler ve aradaki fark bilgi taşır.

Demo şirketinde tam da bu oluyor. Z-score **güvenli bölge** diyor (kâğıt üstünde
kârlı, özkaynağı sağlam, varlık devri yüksek) ama Monte Carlo 12 ayda **%94
batma** diyor. İkisi de doğru: Altman tahakkuk esaslı yıllık bir fotoğraf
çeker, nakdin NE ZAMAN geldiğini görmez. Şirket kârlıdır ve nakitsiz batar —
uygulamanın kurduğu tezin ta kendisi. Bu yüzden skor tek başına değil, batma
olasılığının yanında gösterilmelidir.

İki varyant:
  • **Z′ (1983, özel imalatçı)** — borsada işlem görmeyen üretici şirketler.
    Piyasa değeri gerektirmez; özkaynağın DEFTER değerini kullanır. Demo şirketi
    (tekstil üreticisi, halka açık değil) tam olarak bu modelin hedef kitlesi.
  • **Z″ (imalat dışı / gelişmekte olan piyasa)** — varlık devir oranı (X5)
    çıkarılmıştır, çünkü sektörler arasında çok değişir ve hizmet şirketlerini
    haksız biçimde cezalandırır.

Kullanılmayan varyant: orijinal 1968 Z-score halka açık şirketler içindir ve X4
olarak özkaynağın PİYASA değerini ister. Bu uygulamanın kullanıcısı özel şirket
olduğu için hiç uygulanmadı — piyasa değeri yerine defter değeri koyup "Z-score
hesapladım" demek, modelin kalibre edildiği büyüklüğü değiştirmek olurdu.

> Skor bir olasılık değildir; şirketi tarihsel olarak batanlarla aynı bölgeye
> düşürüp düşürmediğini söyler. Bölge sınırları modelin kendi eşikleridir.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# X4 (özkaynak ÷ toplam yükümlülük) borcu olmayan şirkette tanımsızdır. Sonsuz
# yerine sonlu ve yüksek bir tavan konuyor: borçsuzluk sağlıklıdır ama skoru
# sınırsız şişirip diğer üç bileşeni anlamsızlaştırmamalı.
MAX_EQUITY_TO_LIABILITIES = 10.0


@dataclass(frozen=True)
class Component:
    """Skorun tek bir bileşeni (Altman'ın X1…X5'i)."""
    key: str
    label: str
    weight: float
    explain: str


@dataclass(frozen=True)
class Model:
    """Bir Z-score varyantı: katsayılar + bölge eşikleri."""
    key: str
    name: str
    fits: str                       # kimin için kalibre edildiği
    components: tuple[Component, ...]
    safe_above: float
    distress_below: float


_X1 = Component("x1", "İşletme sermayesi ÷ toplam varlık", 0.0,
                "Kısa vadeli likidite yastığı")
_X2 = Component("x2", "Geçmiş yıl kârları ÷ toplam varlık", 0.0,
                "Şirketin kendi kendini finanse etme birikimi")
_X3 = Component("x3", "FVÖK ÷ toplam varlık", 0.0,
                "Varlıkların faaliyet kârı üretme gücü")
_X4 = Component("x4", "Özkaynak (defter) ÷ toplam yükümlülük", 0.0,
                "Borca karşı sermaye tamponu")
_X5 = Component("x5", "Yıllık satış ÷ toplam varlık", 0.0,
                "Varlık devir hızı")


def _with(component: Component, weight: float) -> Component:
    return Component(component.key, component.label, weight, component.explain)


# Katsayılar Altman'ın yayımlanmış modellerinden birebir alınmıştır; burada
# yeniden kestirilmiş ya da "iyileştirilmiş" bir sürüm YOKTUR. Eşikler de
# modellerin kendi bölge sınırlarıdır.
Z_PRIME = Model(
    key="zprime",
    name="Altman Z′ (özel imalatçı, 1983)",
    fits="Borsada işlem görmeyen üretici şirketler",
    components=(_with(_X1, 0.717), _with(_X2, 0.847), _with(_X3, 3.107),
                _with(_X4, 0.420), _with(_X5, 0.998)),
    safe_above=2.90,
    distress_below=1.23,
)

Z_DOUBLE_PRIME = Model(
    key="zdouble",
    name="Altman Z″ (imalat dışı / gelişmekte olan piyasa)",
    fits="Hizmet, ticaret ve gelişmekte olan piyasa şirketleri",
    components=(_with(_X1, 6.56), _with(_X2, 3.26), _with(_X3, 6.72),
                _with(_X4, 1.05)),
    safe_above=2.60,
    distress_below=1.10,
)

MODELS = {m.key: m for m in (Z_PRIME, Z_DOUBLE_PRIME)}

# Skorun hesaplanabilmesi için gereken bilanço kalemleri.
REQUIRED_FIELDS = ("total_assets", "current_assets", "current_liabilities",
                   "total_liabilities", "retained_earnings", "ebit_annual")

ZONE_SAFE = "Güvenli"
ZONE_GREY = "Gri"
ZONE_DISTRESS = "Tehlike"


@dataclass
class ComponentValue:
    """Hesaplanmış tek bileşen — hangi oranın skora ne kattığı görünsün diye."""
    key: str
    label: str
    ratio: float
    weight: float
    explain: str

    @property
    def contribution(self) -> float:
        return self.ratio * self.weight


@dataclass
class ZScoreResult:
    """Z-score sonucu. `score is None` ise veri yetersizdir; uydurma yapılmaz."""
    model_key: str
    model_name: str
    model_fits: str
    score: float | None = None
    zone: str | None = None
    components: list[ComponentValue] = field(default_factory=list)
    safe_above: float = 0.0
    distress_below: float = 0.0
    missing_fields: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.score is not None

    @property
    def distance_to_safe(self) -> float | None:
        """Güvenli bölgeye kaç puan kaldı (negatifse zaten içeride)."""
        return None if self.score is None else self.safe_above - self.score


def _as_float(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out       # NaN


def _missing(bs: dict) -> list[str]:
    return [f for f in REQUIRED_FIELDS if _as_float(bs.get(f)) is None]


def pick_model(sector: str | None) -> Model:
    """
    Sektöre göre varyant seçer.

    İmalat/üretim ibaresi varsa Z′, aksi hâlde Z″. Bilinmiyorsa Z″ seçilir:
    varlık devrini (X5) hesaba katmayan varyant, sektörü bilmediğimiz bir
    şirket için daha az varsayım yapar.
    """
    if not sector:
        return Z_DOUBLE_PRIME
    metin = str(sector).lower()
    uretim = ("üretim", "uretim", "imalat", "sanayi", "fabrika",
              "manufact", "industr")
    return Z_PRIME if any(k in metin for k in uretim) else Z_DOUBLE_PRIME


def zone_of(score: float, model: Model) -> str:
    if score > model.safe_above:
        return ZONE_SAFE
    if score < model.distress_below:
        return ZONE_DISTRESS
    return ZONE_GREY


def compute(balance_sheet: dict, model: Model = Z_DOUBLE_PRIME) -> ZScoreResult:
    """
    Bilanço kalemlerinden Z-score hesaplar.

    Eksik ya da anlamsız veride skor **üretilmez**; `missing_fields` dolu bir
    sonuç döner. Yarım veriyle hesaplanmış bir iflas skoru, hiç skor
    olmamasından daha tehlikelidir — kullanıcı sayıya bakar, arkasındaki boşluğu
    görmez.
    """
    bos = ZScoreResult(model_key=model.key, model_name=model.name,
                       model_fits=model.fits, safe_above=model.safe_above,
                       distress_below=model.distress_below)

    bs = balance_sheet if isinstance(balance_sheet, dict) else {}
    eksik = _missing(bs)
    # X5 kullanan varyant yıllık satışı da ister.
    ister_satis = any(c.key == "x5" for c in model.components)
    if ister_satis and _as_float(bs.get("annual_sales")) is None:
        eksik = eksik + ["annual_sales"]
    if eksik:
        bos.missing_fields = eksik
        return bos

    toplam_varlik = _as_float(bs["total_assets"])
    if not toplam_varlik or toplam_varlik <= 0:
        # Toplam varlık sıfır/negatifse bütün oranlar tanımsız.
        bos.missing_fields = ["total_assets"]
        return bos

    isletme_sermayesi = (_as_float(bs["current_assets"])
                         - _as_float(bs["current_liabilities"]))
    toplam_yukumluluk = _as_float(bs["total_liabilities"])
    ozkaynak = toplam_varlik - toplam_yukumluluk

    oranlar = {
        "x1": isletme_sermayesi / toplam_varlik,
        "x2": _as_float(bs["retained_earnings"]) / toplam_varlik,
        "x3": _as_float(bs["ebit_annual"]) / toplam_varlik,
        "x4": (min(ozkaynak / toplam_yukumluluk, MAX_EQUITY_TO_LIABILITIES)
               if toplam_yukumluluk > 0 else MAX_EQUITY_TO_LIABILITIES),
    }
    if ister_satis:
        oranlar["x5"] = _as_float(bs["annual_sales"]) / toplam_varlik

    bilesenler = [
        ComponentValue(c.key, c.label, oranlar[c.key], c.weight, c.explain)
        for c in model.components
    ]
    skor = sum(b.contribution for b in bilesenler)

    bos.score = skor
    bos.zone = zone_of(skor, model)
    bos.components = bilesenler
    return bos


def from_company(data: dict) -> ZScoreResult:
    """
    Uygulamanın şirket sözlüğünden Z-score üretir.

    Yıllık satış ve FVÖK, ayrı ayrı saklanan sayılar olmak yerine mevcut aylık
    skalerlerden TÜRETİLİR: aynı büyüklüğü iki yere yazmak, er geç ikisinin
    ayrışması demektir. Amortisman bilançoyla birlikte verilir çünkü aylık gider
    kalemlerinin içinde yoktur (hepsi nakit çıkışıdır).
    """
    bs = dict(data.get("balance_sheet") or {})
    if not bs:
        model = pick_model(data.get("sector"))
        return ZScoreResult(model_key=model.key, model_name=model.name,
                            model_fits=model.fits, safe_above=model.safe_above,
                            distress_below=model.distress_below,
                            missing_fields=list(REQUIRED_FIELDS))

    aylik_gelir = _as_float(data.get("avg_monthly_revenue")) or 0.0
    aylik_gider = _as_float(data.get("avg_monthly_fixed_expense")) or 0.0
    amortisman = _as_float(bs.get("annual_depreciation")) or 0.0

    bs.setdefault("annual_sales", aylik_gelir * 12)
    bs.setdefault("ebit_annual", (aylik_gelir - aylik_gider) * 12 - amortisman)

    return compute(bs, pick_model(data.get("sector")))
