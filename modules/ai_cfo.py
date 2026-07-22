"""
ai_cfo.py  —  MODÜL 3: LLM Destekli "Acımasız CFO" Ajanı
─────────────────────────────────────────────────────────
Modül 1 ve Modül 2'den çıkan matematiksel sonuçları alır ve patrona doğrudan,
duygusuz, aksiyon odaklı bir kurumsal CFO ağzıyla net bir aksiyon planı yazar.

İki katmanlı tasarım:
  1) GERÇEK LLM  — Ortamda ANTHROPIC_API_KEY, OPENAI_API_KEY ya da
     GOOGLE_API_KEY/GEMINI_API_KEY varsa ve ilgili SDK kuruluysa, sayılar bir
     sistem promptu ile modele beslenir. Birden fazlası varsa sıra:
     Claude → OpenAI → Gemini; biri patlarsa bir sonrakine geçilir.
  2) KURAL TABANLI YEREL MOTOR (fallback) — Anahtar yoksa uygulama ASLA boş
     kalmaz: eşik tabanlı bir motor, aynı "acımasız CFO" üslubuyla sayılara
     dayalı somut aksiyon maddeleri üretir. Bu yol her zaman çalışır, ücretsizdir
     ve internet gerektirmez.

Dışa açılan tek şey: RuthlessCFO sınıfı ve .advise(context) metodu.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

# utils.theme DEĞİL: o modül streamlit'i modül seviyesinde import ediyor ve
# bu dosyayı HTTP API de kullanıyor. Biçimlendirme için arayüz çatısı gerekmez.
from utils.format import expense_label, money

# ── Model kimlikleri ──────────────────────────────────────────────────────
# Sağlayıcılar model adlarını sık değiştiriyor ve kodun içine gömülü bir ad
# birkaç ayda eskiyor. Bu yüzden hepsi ortam değişkeniyle ezilebilir.
#
# Claude: claude-opus-4-8, Anthropic'in güncel Opus modeli. Bu ad doğrulandı.
# OpenAI/Gemini: aşağıdakiler tahmindir — kendi hesabınızın model kataloğuyla
# karşılaştırıp gerekirse CG_OPENAI_MODEL / CG_GEMINI_MODEL ile değiştirin.
CLAUDE_MODEL = os.getenv("CG_CLAUDE_MODEL", "claude-opus-4-8")
OPENAI_MODEL = os.getenv("CG_OPENAI_MODEL", "gpt-5-mini")
GEMINI_MODEL = os.getenv("CG_GEMINI_MODEL", "gemini-2.5-flash")

# ── SDK'ları nazikçe dene; hiçbiri yoksa sorun değil, fallback devrede ────
try:
    import anthropic  # type: ignore
    _HAS_CLAUDE = True
except Exception:
    _HAS_CLAUDE = False

try:
    from openai import OpenAI  # type: ignore
    _HAS_OPENAI = True
except Exception:
    _HAS_OPENAI = False

try:
    import warnings

    with warnings.catch_warnings():
        # Eski google-generativeai paketi import'ta deprecation uyarısı basıyor;
        # kullanıcıyı meşgul etmesin diye susturuyoruz (işlev değişmiyor).
        warnings.simplefilter("ignore")
        import google.generativeai as genai  # type: ignore
    _HAS_GEMINI = True
except Exception:
    _HAS_GEMINI = False


def _secret(name: str) -> str | None:
    """
    API anahtarını önce ortam değişkenlerinde, sonra Streamlit sırlarında arar.

    Neden iki yer: Streamlit Cloud'da secrets.toml'un YALNIZCA kök seviyedeki
    anahtarları ortam değişkeni olarak da yayınlanıyor. Kullanıcı anahtarı bir
    TOML bölümüne ([genel] gibi) koyarsa os.getenv onu göremez ve uygulama
    sessizce kural tabanlı motorda kalır — "anahtarı ekledim ama değişmedi"
    şikâyetinin kaynağı budur. st.secrets ise her iki durumu da görür.

    streamlit import'u bilerek fonksiyon içinde: bu modül Streamlit olmadan da
    çalışabilmeli (testler ve olası bir API sunucusu bunu kullanıyor).
    """
    deger = os.getenv(name)
    if deger:
        return deger
    try:
        import streamlit as st  # noqa: PLC0415 — bilinçli gecikmeli import

        if name in st.secrets:
            return str(st.secrets[name])
        # Bölüm içine konmuşsa da bul (bir seviye derinlik yeter).
        for bolum in st.secrets.values():
            if hasattr(bolum, "get") and bolum.get(name):
                return str(bolum[name])
    except Exception:
        pass  # streamlit yok ya da sır dosyası tanımlı değil — sorun değil
    return None


SYSTEM_PROMPT = """Sen 20 yıllık, sahada yanmış, duygusuz ve acımasız bir kurumsal CFO'sun.
Sadece sayılara, nakit akışına ve net kâra bakarsın. Patrona yağ çekmezsin,
gerçeği yüzüne söylersin. Cümlelerin kısa, net ve aksiyon odaklıdır.

Sana bir şirketin nakit durumu, kredi senaryosu analizi ve Monte Carlo batma
olasılığı verilecek. Görevin:
1) Tek paragraflık sert bir teşhis yaz (durumu tokat gibi özetle).
2) Ardından numaralı, uygulanabilir bir aksiyon planı ver (en fazla 8 madde).
   Her madde SOMUT olsun: hangi kalemden ne kadar kesilecek, hangi alacak
   durdurulacak, kredi çekilmeli mi çekilmemeli mi — rakamla konuş.
Türkçe yaz. Abartılı nezaket yok. 'Bence' gibi zayıf ifadeler kullanma."""


def _sadelestir(mesaj: str) -> str:
    """
    Sağlayıcının hata gövdesinden insanca cümleyi çıkar.

    Ham hali JSON + request_id ile birlikte gelir; olduğu gibi göstermek halka
    açık demoda hem çirkin hem gereksiz. İçindeki 'message' alanı zaten
    kullanıcıya yazılmış tek anlaşılır cümledir.
    """
    for desen in (r"'message':\s*'([^']+)'", r'"message":\s*"([^"]+)"'):
        bulunan = re.search(desen, mesaj)
        if bulunan:
            return bulunan.group(1)
    return mesaj if len(mesaj) <= 180 else mesaj[:180] + "…"


def _anahtari_gizle(metin: str) -> str:
    """
    Hata mesajını göstermeden önce içindeki olası anahtarı sil. Mesaj arayüze
    çıkıyor; sağlayıcı bir gün isteği hata metnine koyarsa anahtar ekrana
    düşmesin.
    """
    return re.sub(r"(sk-[A-Za-z0-9_\-]{8,}|AIza[A-Za-z0-9_\-]{10,})", "«gizlendi»", metin)


@dataclass
class CFOAdvice:
    """CFO çıktısını taşıyan basit kap."""
    text: str            # markdown metin (teşhis + aksiyon planı)
    source: str          # "Claude", "OpenAI", "Gemini" veya "Kural Tabanlı Motor"
    # Kural tabanlı motora DÜŞÜLDÜYSE sebebi. Sessiz fallback en can sıkıcı
    # hata sınıfı: anahtarı ekleyen kişi "olmadı" görür ama nedenini göremez,
    # logda da iz kalmaz. Bu alan o körlüğü kapatır. LLM çalıştıysa None.
    reason: str | None = None


class RuthlessCFO:
    """Acımasız CFO ajanı. Önce gerçek LLM'i dener, olmazsa yerel motora düşer."""

    def __init__(self, prefer: str | None = None):
        """
        prefer: "claude" | "openai" | "gemini" | None (otomatik). None ise
        ortamdaki anahtara göre seçilir; hiçbiri yoksa kural tabanlı motor.
        """
        self.claude_key = _secret("ANTHROPIC_API_KEY")
        self.openai_key = _secret("OPENAI_API_KEY")
        self.gemini_key = _secret("GOOGLE_API_KEY") or _secret("GEMINI_API_KEY")
        self.prefer = prefer

    def available_llms(self) -> list[str]:
        """
        Denenebilecek LLM'ler, tercih sırasıyla: ["claude", "openai", "gemini"]
        alt kümesi.

        Boş liste = kural tabanlı motor devrede. O motor DETERMİNİSTİKtir, yani
        "yeniden çağır" metni değiştirmez; arayüz butonu buna göre gösterir.
        advise() de aynı listeyi kullanır — mantık kopyalanırsa ikisi zamanla
        birbirinden kayar, o yüzden tek kaynak burası.
        """
        engines = []
        if (self.prefer in (None, "claude")) and _HAS_CLAUDE and self.claude_key:
            engines.append("claude")
        if (self.prefer in (None, "openai")) and _HAS_OPENAI and self.openai_key:
            engines.append("openai")
        if (self.prefer in (None, "gemini")) and _HAS_GEMINI and self.gemini_key:
            engines.append("gemini")
        return engines

    # ── Kamuya açık tek metot ─────────────────────────────────────────────
    def advise(self, ctx: dict) -> CFOAdvice:
        """
        ctx: app.py'nin derlediği analiz bağlamı. Beklenen anahtarlar:
            company_name, currency_symbol, current_cash, net_operating,
            runway_months, ruin_probability (0–1), expected_ruin_month,
            loan_amount, installment, total_interest, relief_months,
            default_with_loan, default_without_loan,
            top_receivables (liste), expense_breakdown (sözlük)
        """
        # Sırayla dene; biri patlarsa diğerine, hepsi patlarsa yerel motora düş.
        askers = {"claude": (self._ask_claude, "Claude"),
                  "openai": (self._ask_openai, "OpenAI"),
                  "gemini": (self._ask_gemini, "Gemini")}
        hatalar = []
        for engine in self.available_llms():
            ask, label = askers[engine]
            try:
                return CFOAdvice(ask(ctx), label)
            except Exception as e:
                hatalar.append(f"{label}: {_sadelestir(str(e))}")
                continue
        # Her koşulda çalışan güvenli liman:
        return CFOAdvice(self._rule_based(ctx), "Kural Tabanlı Motor",
                         self.neden_yerel_motor(hatalar))

    def neden_yerel_motor(self, hatalar: list[str] | None = None) -> str | None:
        """
        Kural tabanlı motora neden düşüldüğü — tek cümle, anahtarsız.

        HİÇBİR anahtar tanımlı değilse None döner. Sebebi: anahtarsız kurulum
        bir arıza değil, demonun BİLİNÇLİ varsayılanı. Orada uyarı göstermek
        halka açık demoyu bozukmuş gibi gösterirdi.

        Anahtar varsa kullanıcı gerçekten LLM istemiştir; o zaman sessiz kalmak
        zarar verir. Üç durum dışarıdan aynı görünür, çözümleri farklıdır:
        paket yok (requirements), çağrı patladı (geçersiz anahtar / kota / ağ),
        anahtar başka sağlayıcıya ait.
        """
        saglayicilar = (
            ("ANTHROPIC_API_KEY", self.claude_key, _HAS_CLAUDE, "anthropic"),
            ("OPENAI_API_KEY", self.openai_key, _HAS_OPENAI, "openai"),
            ("GOOGLE_API_KEY", self.gemini_key, _HAS_GEMINI, "google-generativeai"),
        )
        if not any(anahtar for _, anahtar, _, _ in saglayicilar):
            return None

        parcalar = []
        if hatalar:
            parcalar.append("çağrı başarısız — " + "; ".join(hatalar))

        # Paket eksiği, BİR BAŞKA sağlayıcı hata verse bile raporlanmalı. Önceki
        # sürüm hata varsa buraya hiç bakmıyordu; sonuç: "Claude patladı" yazıp
        # "Gemini'nin paketi yok"u yutuyordu — yani çözümü gizliyordu.
        for ad, anahtar, kurulu, paket in saglayicilar:
            if anahtar and not kurulu:
                parcalar.append(f"{ad} tanımlı ama {paket} paketi kurulu değil")

        # Hangi anahtarların OKUNDUĞU (isim, değer değil). Bir anahtarı Secrets'a
        # yazıp burada göremiyorsan sorun anahtarın kendisinde değil, nereye
        # yazıldığındadır — bu satır olmadan o ayrımı yapmak imkânsızdı.
        gorulen = [ad for ad, anahtar, _, _ in saglayicilar if anahtar]
        parcalar.append("görülen anahtarlar: " + (", ".join(gorulen) or "yok"))

        return _anahtari_gizle(" · ".join(parcalar))

    # ── Gerçek LLM yolları ────────────────────────────────────────────────
    def _user_payload(self, ctx: dict) -> str:
        """LLM'e verilecek sayısal özet (sistem promptundan ayrı)."""
        sym = ctx.get("currency_symbol", "₺")
        recv = ctx.get("top_receivables", [])
        recv_txt = "\n".join(
            f"  - {r['customer']}: {money(r['amount'], sym)}, {r['overdue_days']} gün gecikmiş"
            for r in recv
        ) or "  (veri yok)"
        return f"""ŞİRKET: {ctx.get('company_name', 'Bilinmiyor')}
Mevcut kasa: {money(ctx['current_cash'], sym)}
Aylık faaliyet net akışı: {money(ctx['net_operating'], sym)}
Kasa ömrü (bugünkü yakım sabit kalırsa): {ctx.get('runway_months', 'belirsiz')} ay
Kasa ömrü (son 12 ayın bozulma trendi sürerse): {ctx.get('trend_runway_months') or 'belirsiz'} ay

KREDİ SENARYOSU:
  Çekilecek kredi: {money(ctx.get('loan_amount', 0), sym)}
  Aylık taksit: {money(ctx.get('installment', 0), sym)}
  Vade sonu toplam faiz: {money(ctx.get('total_interest', 0), sym)}
  Kredisiz iflas ayı: {ctx.get('default_without_loan') or 'ufukta yok'}
  Kredili iflas ayı: {ctx.get('default_with_loan') or 'ufukta yok'}
  Kredinin öteleme etkisi: {ctx.get('relief_months', 0)} ay

MONTE CARLO STRES TESTİ:
  12 ay içinde batma olasılığı: %{ctx['ruin_probability'] * 100:.1f}
  Beklenen iflas ayı: {ctx.get('expected_ruin_month') or 'ufukta yok'}

EN BÜYÜK ALACAKLAR (gecikme durumu):
{recv_txt}

Bu tabloya göre patrona teşhis + numaralı aksiyon planı yaz."""

    def _ask_claude(self, ctx: dict) -> str:
        """
        Anthropic Messages API.

        İki ayrıntı bilerek böyle:
          • temperature YOK — Opus 4.8 bu parametreyi kabul etmiyor, gönderilirse
            istek 400 döner. Üslup sistem promptuyla ayarlanıyor.
          • effort="low" — bu iş "verilen sayılardan kısa bir aksiyon planı yaz";
            derin muhakeme gerektirmiyor ve arayüzü bekletmemesi gerekiyor.
        """
        client = anthropic.Anthropic(api_key=self.claude_key)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": self._user_payload(ctx)}],
        )
        # content bir blok listesi; metin dışı bloklar (thinking) da olabilir.
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def _ask_openai(self, ctx: dict) -> str:
        client = OpenAI(api_key=self.openai_key)
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._user_payload(ctx)},
            ],
        )
        return resp.choices[0].message.content.strip()

    def _ask_gemini(self, ctx: dict) -> str:
        genai.configure(api_key=self.gemini_key)
        model = genai.GenerativeModel(GEMINI_MODEL, system_instruction=SYSTEM_PROMPT)
        resp = model.generate_content(self._user_payload(ctx))
        return resp.text.strip()

    # ── Kural tabanlı yerel motor (fallback) ──────────────────────────────
    def _rule_based(self, ctx: dict) -> str:
        """
        Eşik tabanlı 'acımasız CFO' üretimi. Sayılara bakıp somut, rakamlı
        maddeler kurar. Tamamen deterministik ve çevrimdışıdır.
        """
        sym = ctx.get("currency_symbol", "₺")
        ruin = ctx["ruin_probability"] * 100
        net_op = ctx["net_operating"]
        cash = ctx["current_cash"]
        loan = ctx.get("loan_amount", 0)
        relief = ctx.get("relief_months", 0)
        d_loan = ctx.get("default_with_loan")
        exp_ruin = ctx.get("expected_ruin_month")
        recv = sorted(ctx.get("top_receivables", []),
                      key=lambda r: r.get("overdue_days", 0), reverse=True)
        breakdown = ctx.get("expense_breakdown", {})

        # ── 1) Sert teşhis paragrafı ──────────────────────────────────────
        if ruin >= 60:
            tone = (f"Sayın Yönetici, tablonuz ortada ve iç açıcı değil. Monte Carlo "
                    f"stres testi önümüzdeki 12 ayda **%{ruin:.1f} olasılıkla kasanın "
                    f"sıfırlandığını** söylüyor. Bu bir ihtimal değil, bir uyarı ateşidir.")
        elif ruin >= 30:
            tone = (f"Sayın Yönetici, durum kırmızı değil ama sarı. Batma olasılığınız "
                    f"**%{ruin:.1f}** — yani her üç senaryodan birinde kasa dibi görüyor. "
                    f"Şansa değil, disipline oynayacağız.")
        else:
            # Trendin yönünü VERİDEN oku. Sabit "trendiniz aşağı eğimli" cümlesi,
            # nakit üretimi artan sağlıklı bir şirkette düpedüz yanlış oluyordu.
            slope = ctx.get("trend_slope")
            if slope is not None and slope > 0:
                trend_cumle = ("üstelik faaliyet nakdiniz aylık "
                               f"{money(slope, sym)} İYİLEŞİYOR. Bu ivmeyi kalıcı "
                               f"kılacak yatırımlar dışında yeni sabit gider almayın")
            elif slope is not None and slope < 0:
                trend_cumle = ("ama rehavete gerek yok: faaliyet nakdiniz aylık "
                               f"{money(abs(slope), sym)} geriliyor, gevşerseniz "
                               f"bu oran hızla tırmanır")
            else:
                trend_cumle = ("yine de nakit trendini yakından izleyin; bu oran "
                               "hızla tırmanabilir")
            tone = (f"Sayın Yönetici, kasa şimdilik ayakta: batma olasılığı "
                    f"**%{ruin:.1f}** — {trend_cumle}.")

        # Kredi hükmü (relief İŞARETLİ: + zaman kazandırır, − iflası öne çeker)
        if loan > 0:
            if relief < 0:
                verdict = (f" Bankadan {money(loan, sym)} kredi çekme fikri şu tabloda "
                           f"**intihardır**. İlk aylarda kasada gördüğünüz para sizi kandırır; "
                           f"gerçekte bu kredi iflasınızı **{abs(relief)} ay ÖNE çekiyor**"
                           + (f" — kredili senaryoda kasa {d_loan}. ayda sıfırı deliyor." if d_loan else ".")
                           + " Yani çözüm değil, hızlandırıcı.")
            elif relief <= 6:
                verdict = (f" Düşündüğünüz {money(loan, sym)} kredi kalıcı çözüm değil, "
                           f"sadece {relief} aylık bir morfin iğnesi. Yapısal nakit sorununu "
                           f"çözmeden bu parayı almak borcu büyütmekten başka işe yaramaz.")
            else:
                verdict = (f" {money(loan, sym)} kredi size {relief} ay kazandırıyor; "
                           f"ama bu süreyi nakit üretimini onarmak için kullanmazsanız "
                           f"o da erir. Kredi bir araçtır, kurtarıcı değil.")
        else:
            verdict = " Henüz masaya bir kredi senaryosu koymadınız; iyi. Önce içerideki kanamayı durduralım."

        diagnosis = tone + verdict

        # ── 2) Numaralı, somut aksiyon planı ──────────────────────────────
        actions: list[str] = []

        # a) En yaşlı alacak(lar)ı durdur
        worst = [r for r in recv if r.get("overdue_days", 0) >= 60]
        if worst:
            w = worst[0]
            actions.append(
                f"**{w['customer']}** {w['overdue_days']} gündür {money(w['amount'], sym)} "
                f"ödemiyor. Sevkiyatı BUGÜN durdurun; tahsilat olmadan tek kutu daha mal çıkmasın."
            )
            if len(worst) > 1:
                extra = sum(r["amount"] for r in worst[1:])
                actions.append(
                    f"Diğer {len(worst) - 1} geciken bayiden toplam {money(extra, sym)} alacağınız "
                    f"var. Hepsine 7 günlük son ödeme protokolü gönderin, ödemeyene faktoring uygulayın."
                )

        # a2) Şüpheli alacak: "geç geliyor" ile "hiç gelmeyecek" aynı şey değil
        supheli = ctx.get("expected_uncollectible")
        dso = ctx.get("dso_days")
        if supheli and supheli > 0:
            dso_cumle = (f" Alacak devir gününüz {dso:.0f}; her gün kısaltma "
                         f"doğrudan kasaya para demek." if dso else "")
            actions.append(
                f"Yaşlandırmaya göre bu alacakların **{money(supheli, sym)}'i "
                f"muhtemelen hiç gelmeyecek** — bu bir gecikme değil, kayıp. Bu tutarı "
                f"bugün karşılık ayırıp planlarınızdan DÜŞÜN; gelmeyecek parayı bütçede "
                f"tutmak, olmayan nakde göre karar vermektir.{dso_cumle}"
            )

        # a3) Bilanço "iyi" derken nakit "batıyor" diyorsa, bu bir tuzaktır
        z_skor = ctx.get("z_score")
        z_bolge = ctx.get("z_zone")
        if z_skor is not None and z_bolge == "Güvenli" and ruin >= 60:
            actions.append(
                f"Bilançonuz sizi rahatlatmasın: Altman skoru **{z_skor:.2f}** ile güvenli "
                f"bölgede, yani kâğıt üstünde kârlı ve sermayeniz sağlam görünüyor. O "
                f"model yıllık bir fotoğraf çeker, paranın NE ZAMAN geldiğini görmez. "
                f"Şirketler tam olarak böyle batar — bankaya bu skoru göstererek değil, "
                f"nakit takvimini düzelterek kurtulursunuz."
            )
        elif z_skor is not None and z_bolge == "Tehlike":
            actions.append(
                f"Altman skorunuz **{z_skor:.2f}** — tarihsel olarak batan şirketlerle aynı "
                f"bölgedesiniz. Bu, nakit sıkışıklığından ayrı ve daha derin bir sorun: "
                f"sermaye yapınız borcu taşıyamıyor. Nakit önlemleri kanamayı yavaşlatır "
                f"ama yapıyı onarmaz; sermaye artırımı ya da borç yeniden yapılandırması "
                f"masaya gelmeli."
            )

        # b) En şişkin gider kaleminden kes
        if breakdown:
            # pazarlama gibi 'kısılabilir' kalemi öne al, yoksa en büyüğü
            soft = {k: v for k, v in breakdown.items()
                    if any(t in k.lower() for t in ("pazarlama", "market", "reklam"))}
            target_key, target_val = (max(soft.items(), key=lambda kv: kv[1])
                                      if soft else max(breakdown.items(), key=lambda kv: kv[1]))
            cut = target_val * 0.20
            actions.append(
                f"**{expense_label(target_key)}** bütçesi aylık {money(target_val, sym)}. "
                f"Buradan %20 ({money(cut, sym)}/ay) kesin — ölmek üzere olan şirket şov yapmaz."
            )

        # c) Kredi kararı — nette
        if loan > 0 and relief <= 6 and ruin >= 45:
            actions.append(
                f"O {money(loan, sym)} krediyi ÇEKMEYİN. Aynı nakdi tedarikçi vadesini "
                f"30 gün uzatarak faizsiz yaratın; bankaya faiz ödemek yerine içeride tutun."
            )
        elif loan > 0:
            actions.append(
                f"Krediyi çekecekseniz vadeyi UZUN, taksiti düşük tutun ve parayı gidere "
                f"değil yalnızca tahsilatı hızlandıran işlere (faktoring, teminat) harcayın."
            )

        # d) Burn / nakit ömrü uyarısı — operasyon mu, borç servisi mi öldürüyor?
        debt_service = ctx.get("debt_service")
        monthly_net = ctx.get("monthly_net")  # operasyon − borç servisi
        if net_op < 0:
            actions.append(
                f"Faaliyetiniz daha borç servisine gelmeden ayda {money(abs(net_op), sym)} "
                f"nakit YAKIYOR. Bu açığı kapatmadan hiçbir senaryo sizi kurtarmaz; "
                f"hedef en geç 3 ayda operasyonel başabaş."
            )
        elif debt_service is not None and monthly_net is not None and monthly_net < 0:
            # Operasyon pozitif ama borç servisi onu yiyip nakdi eksiye çekiyor
            actions.append(
                f"Dikkat: operasyonunuz aslında ayda {money(net_op, sym)} ÜRETİYOR; sizi "
                f"eksiye ({money(monthly_net, sym)}/ay) düşüren {money(debt_service, sym)} "
                f"borç servisi. Sorun satış değil, borç yükü — çözüm de yeni kredi değil, "
                f"mevcut borcu yeniden yapılandırmak. Bankayla vade/faiz masasına oturun."
            )
        else:
            actions.append(
                f"Nakit akışınız pozitif ({money(monthly_net or net_op, sym)}/ay) — tek can "
                f"simidiniz bu. Bunu bozacak her yeni sabit gideri reddedin."
            )

        # d2) Statik runway'in yanılttığını sayıyla söyle
        static_rw = ctx.get("runway_months")
        trend_rw = ctx.get("trend_runway_months")
        trend_slope = ctx.get("trend_slope")
        if static_rw and trend_rw and trend_rw < static_rw:
            # Eğim opsiyonel: app.py hep dolduruyor ama bu motor son savunma
            # hattı — eksik bir alan yüzünden çökerse kullanıcı bomboş rapor
            # görür. Eğim yoksa cümleyi rakamsız ama anlamlı kur.
            egim = (f"Faaliyet nakdiniz aylık {money(abs(trend_slope), sym)} geriliyor; bu "
                    f"eğilim sürerse" if trend_slope else "Son 12 ayın bozulma eğilimi sürerse")
            actions.append(
                f"Kasanızın {static_rw:.0f} ay dayanacağı hesabı bir yanılsamadır: o rakam "
                f"bugünkü yakımın hiç kötüleşmeyeceğini varsayar. {egim} gerçek süreniz "
                f"**~{trend_rw} ay**. Planlarınızı {static_rw:.0f} aya değil {trend_rw} aya kurun."
            )

        # e) Zaman baskısı
        if exp_ruin:
            actions.append(
                f"Saat işliyor: mevcut gidişle beklenen iflas penceresi ~{exp_ruin:.0f}. ay. "
                f"Yukarıdaki maddeleri hafta sonuna değil, bu hafta içine yayın."
            )

        # Tavan 6'dan 8'e çıkarıldı: yaşlandırma ve Altman maddeleri eklenince
        # 6'lık kesim, listenin SONUNDAKİ nakit ömrü ve zaman baskısı
        # maddelerini sessizce düşürüyordu — plan kısalmıyor, kuyruğu kayboluyordu.
        actions = actions[:8]
        plan = "\n".join(f"{i}. {a}" for i, a in enumerate(actions, 1))

        return f"{diagnosis}\n\n**AKSİYON PLANI:**\n\n{plan}"
