"""
ai_cfo.py  —  MODÜL 3: LLM Destekli "Acımasız CFO" Ajanı
─────────────────────────────────────────────────────────
Modül 1 ve Modül 2'den çıkan matematiksel sonuçları alır ve patrona doğrudan,
duygusuz, aksiyon odaklı bir kurumsal CFO ağzıyla net bir aksiyon planı yazar.

İki katmanlı tasarım:
  1) GERÇEK LLM  — Ortamda OPENAI_API_KEY ya da GOOGLE_API_KEY/GEMINI_API_KEY
     varsa ve ilgili SDK kuruluysa, sayılar bir sistem promptu ile modele
     beslenir. (LangChain kuruluysa onun sohbet arayüzü, değilse SDK doğrudan.)
  2) KURAL TABANLI YEREL MOTOR (fallback) — Anahtar yoksa uygulama ASLA boş
     kalmaz: eşik tabanlı bir motor, aynı "acımasız CFO" üslubuyla sayılara
     dayalı somut aksiyon maddeleri üretir. Bu yol her zaman çalışır, ücretsizdir
     ve internet gerektirmez.

Dışa açılan tek şey: RuthlessCFO sınıfı ve .advise(context) metodu.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from utils.theme import expense_label, money

# ── SDK'ları nazikçe dene; hiçbiri yoksa sorun değil, fallback devrede ────
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


SYSTEM_PROMPT = """Sen 20 yıllık, sahada yanmış, duygusuz ve acımasız bir kurumsal CFO'sun.
Sadece sayılara, nakit akışına ve net kâra bakarsın. Patrona yağ çekmezsin,
gerçeği yüzüne söylersin. Cümlelerin kısa, net ve aksiyon odaklıdır.

Sana bir şirketin nakit durumu, kredi senaryosu analizi ve Monte Carlo batma
olasılığı verilecek. Görevin:
1) Tek paragraflık sert bir teşhis yaz (durumu tokat gibi özetle).
2) Ardından numaralı, uygulanabilir bir aksiyon planı ver (en fazla 6 madde).
   Her madde SOMUT olsun: hangi kalemden ne kadar kesilecek, hangi alacak
   durdurulacak, kredi çekilmeli mi çekilmemeli mi — rakamla konuş.
Türkçe yaz. Abartılı nezaket yok. 'Bence' gibi zayıf ifadeler kullanma."""


@dataclass
class CFOAdvice:
    """CFO çıktısını taşıyan basit kap."""
    text: str            # markdown metin (teşhis + aksiyon planı)
    source: str          # "OpenAI", "Gemini" veya "Kural Tabanlı Motor"


class RuthlessCFO:
    """Acımasız CFO ajanı. Önce gerçek LLM'i dener, olmazsa yerel motora düşer."""

    def __init__(self, prefer: str | None = None):
        """
        prefer: "openai" | "gemini" | None (otomatik). None ise ortamdaki
        anahtara göre seçilir; hiçbiri yoksa kural tabanlı motor kullanılır.
        """
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.gemini_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.prefer = prefer

    def available_llms(self) -> list[str]:
        """
        Denenebilecek LLM'ler, tercih sırasıyla: ["openai", "gemini"] alt kümesi.

        Boş liste = kural tabanlı motor devrede. O motor DETERMİNİSTİKtir, yani
        "yeniden çağır" metni değiştirmez; arayüz butonu buna göre gösterir.
        advise() de aynı listeyi kullanır — mantık kopyalanırsa ikisi zamanla
        birbirinden kayar, o yüzden tek kaynak burası.
        """
        engines = []
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
        askers = {"openai": (self._ask_openai, "OpenAI"),
                  "gemini": (self._ask_gemini, "Gemini")}
        for engine in self.available_llms():
            ask, label = askers[engine]
            try:
                return CFOAdvice(ask(ctx), label)
            except Exception:
                continue
        # Her koşulda çalışan güvenli liman:
        return CFOAdvice(self._rule_based(ctx), "Kural Tabanlı Motor")

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

    def _ask_openai(self, ctx: dict) -> str:
        client = OpenAI(api_key=self.openai_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._user_payload(ctx)},
            ],
            temperature=0.5,
        )
        return resp.choices[0].message.content.strip()

    def _ask_gemini(self, ctx: dict) -> str:
        genai.configure(api_key=self.gemini_key)
        model = genai.GenerativeModel("gemini-1.5-flash", system_instruction=SYSTEM_PROMPT)
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
            tone = (f"Sayın Yönetici, kasa şimdilik ayakta: batma olasılığı **%{ruin:.1f}**. "
                    f"Ama rehavete gerek yok; nakit trendiniz aşağı eğimli, gevşerseniz "
                    f"bu oran hızla tırmanır.")

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
            actions.append(
                f"Kasanızın {static_rw:.0f} ay dayanacağı hesabı bir yanılsamadır: o rakam "
                f"bugünkü yakımın hiç kötüleşmeyeceğini varsayar. Faaliyet nakdiniz aylık "
                f"{money(abs(trend_slope), sym)} geriliyor; bu eğilim sürerse gerçek süreniz "
                f"**~{trend_rw} ay**. Planlarınızı {static_rw:.0f} aya değil {trend_rw} aya kurun."
            )

        # e) Zaman baskısı
        if exp_ruin:
            actions.append(
                f"Saat işliyor: mevcut gidişle beklenen iflas penceresi ~{exp_ruin:.0f}. ay. "
                f"Yukarıdaki maddeleri hafta sonuna değil, bu hafta içine yayın."
            )

        actions = actions[:6]  # en fazla 6 madde
        plan = "\n".join(f"{i}. {a}" for i, a in enumerate(actions, 1))

        return f"{diagnosis}\n\n**AKSİYON PLANI:**\n\n{plan}"
