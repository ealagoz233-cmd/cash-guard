# 🛡️ Cash Guard

**Kurumsal Nakit Hayatta Kalma & Kredi Stres Testi Motoru**

Şirketler kârsızlıktan değil, **nakitsizlikten ve yanlış borçlanmadan** batar.
Cash Guard, bir patronun/CFO'nun en büyük korkusunu — *"acil nakit için kredi
çekeyim mi?"* sorusunu — matematiksel olarak sınar: kredinin şirketi gerçekten
kurtarıp kurtarmayacağını, yoksa sadece **"zombi şirket"** yapıp batışı birkaç ay
öteleyip ötelemeyeceğini önceden gösterir.

> Bu bir karar-destek **prototipidir (PoC)**, yatırım/finans tavsiyesi değildir.

---

## Neyi çözer?

Şirket kâğıt üstünde kârlı görünebilir ama **fiilen tahsil edilen nakit**
(collections) faturalanan gelirden düşükse, para alacaklarda sıkışır ve kasa
sessizce erir. Cash Guard tam da bu farkı temel alır ve üç açıdan saldırır:

| Modül | Soru | Yöntem |
|-------|------|--------|
| **Şirket Röntgeni** | Yapısal olarak neredeyim? | Son 12 ay gelir/tahsilat/kasa trendi, gider dağılımı, alacak yaşlandırma |
| **1 · Kredi Kurtarır mı?** | Bu krediyi çekersem ne olur? | Deterministik 24 aylık nakit projeksiyonu (annüite itfa) |
| **2 · Monte Carlo Stres Testi** | Bugünkü halimle 12 ayda batma olasılığım ne? | 10.000–50.000 rastgele ekonomik senaryo + "mevcut vs. kredili" karşılaştırma |
| **3 · Acımasız CFO Ajanı** | Ne yapmalıyım? | LLM (varsa) ya da kural tabanlı, sayıya dayalı aksiyon planı + PDF rapor |

---

## Öne çıkan özellikler

- **Nakit ≠ Kâr:** Model faturalanan geliri değil, fiilen tahsil edilen nakdi
  kullanır. Alacak boşluğu ("kârlı görün, nakitsiz bat" riski) baştan görünür.
- **Borç Tuzağı Tespiti:** Kredinin iflası **öteleyip** mi yoksa **öne mi
  çektiğini** işaretli olarak ölçer. Kredili nakit eğrisi sıfırı deldiğinde
  grafik üzerine **☠ İFLAS NOKTASI** düşer.
- **Zamana Yayılan Stres:** Monte Carlo şokları 1. aydan tam güçle binmez;
  kademeli birikir (ramp). Bu hem gerçekçidir hem de batma olasılığını
  sürgülere anlamlı biçimde duyarlı tutar.
- **Devasa Batma Metriği:** "Önümüzdeki 12 ayda kasanın sıfırlanma olasılığı:
  **%94.3**" gibi tek bir gut-punch sayı.
- **Nakit Ömrü Merdiveni:** Aynı soruya üç farklı varsayımla cevap verilir ve
  aradaki uçurum gösterilir — bugünkü yakım sabit kalırsa **~42 ay**, son 12 ayın
  bozulma eğilimi sürerse **~10 ay**, Monte Carlo stresi altında beklenen
  temerrüt **8. ay**. Herkesin yaptığı "kasa ÷ yakım" hesabının neden yanılttığı
  bu merdivenin kendisidir.
- **Her Koşulda Çalışan CFO:** API anahtarı yoksa uygulama boş kalmaz; kural
  tabanlı yerel motor aynı acımasız üslupla, sayıya dayalı somut maddeler üretir.
- **Senaryo Karşılaştırma:** "Mevcut hal vs. kredi çekersen" iki Monte Carlo yan
  yana. Kredinin 12 ayı rahatlatıp uzun vadede iflası öne çekmesini (borç tuzağı)
  sayısal olarak gösterir.
- **Yönetim Kurulu PDF Raporu:** Yönetici özeti + kredi analizi + senaryo
  karşılaştırması + CFO aksiyon planı, tek tıkla Türkçe PDF (reportlab).
- **Kendi Verini Yükle:** Mock şirket dışında CSV/Excel ile kendi finansallarını
  yükleyebilirsin.
- **Opsiyonel Numba Hızlandırma:** Çekirdek NumPy ile zaten hızlı; numba kuruluysa
  sıcak döngü JIT ile derlenir.

---

## Kurulum

```bash
# 1) Bağımlılıkları kur
pip install -r requirements.txt

# 2) Uygulamayı çalıştır
streamlit run app.py
```

Tarayıcıda `http://localhost:8501` açılır. Uygulama, paketle gelen sahte şirket
verisiyle **dolu ekran** olarak açılır — hemen deneyebilirsin.

### (Opsiyonel) Gerçek LLM CFO

Kural tabanlı motor anahtarsız çalışır. Gerçek bir LLM istiyorsan ortam
değişkeni tanımla:

```bash
# OpenAI
export OPENAI_API_KEY="sk-..."
# ya da Gemini
export GOOGLE_API_KEY="AIza..."
```

Anahtar + ilgili SDK varsa CFO otomatik olarak gerçek modeli kullanır; yoksa
sessizce kural tabanlı motora düşer. Rapor kutusundaki "Kaynak" rozeti hangisinin
kullanıldığını gösterir.

---

## Kendi verini yükleme biçimleri

Sidebar'daki yükleyici iki biçim kabul eder:

**Biçim A — Anahtar/Değer (`alan,deger`):** Çekirdek skalerleri ezer.

```csv
alan,deger
current_cash,4200000
avg_monthly_revenue,7200000
avg_monthly_collections,6800000
avg_monthly_fixed_expense,5950000
existing_monthly_debt_service,950000
```

**Biçim B — Aylık geçmiş tablosu:** Ortalamalar ve son kasa buradan türetilir.

```csv
month,revenue,fixed_expense,collections,cash_end
2026-01,6540000,5900000,5700000,5720000
...
```

---

## Proje mimarisi

```
cash-guard/
├── app.py                       # Ana Streamlit arayüzü (dashboard)
├── modules/
│   ├── loan_simulator.py        # Modül 1: deterministik kredi/borç projeksiyonu
│   ├── monte_carlo.py           # Modül 2: 10.000+ iterasyon stres testi
│   ├── ai_cfo.py                # Modül 3: LLM + kural tabanlı CFO ajanı
│   ├── runway.py                # Nakit ömrü: statik + trend (Theil–Sen) hesabı
│   ├── data_io.py               # Veri yükleme ve kullanıcı CSV/Excel ayrıştırma
│   └── report.py                # Yönetim kurulu PDF raporu (reportlab)
├── utils/
│   ├── theme.py                 # "War-room" karanlık tema, KPI kartları, CSS
│   └── performance_utils.py     # Numba/NumPy nakit yolu çekirdeği
├── tests/
│   ├── test_finance_math.py     # Kredi matematiği + Monte Carlo değişmezleri
│   ├── test_runway.py           # Statik/trend runway, Theil–Sen dayanıklılığı
│   └── test_data_integrity.py   # Mock veri tutarlılığı + yükleme dayanıklılığı
├── data/
│   └── mock_company_data.json   # Uygulama boş açılmasın diye sahte şirket
├── .streamlit/config.toml       # Karanlık tema temel ayarları
├── requirements.txt
└── README.md
```

> `data_io.py` bilerek `app.py`'den ayrıdır: `app.py` bir Streamlit script'i
> olduğu için import edilemez, dolayısıyla içindeki kod test edilemezdi.

### Model notları (metodoloji)

- **Nakit girişi = tahsilat (collections)**, faturalanan gelir değil. Aradaki
  fark alacaklarda birikir.
- **Kredi taksidi:** Eşit taksitli (annüite) formül
  `A = P·r·(1+r)ⁿ / ((1+r)ⁿ−1)`; ek taksit yalnızca vade boyunca eklenir.
- **Temerrüt tanımı:** Nakit yolu herhangi bir ayda sıfırın altına düşerse o
  senaryo "batmış" sayılır (path-dependent — sonradan toparlanma da doğru
  işlenir).
- **Stres değişkenleri:** Gelir düşüşü, tahsilat gecikmesi (nakit kayması),
  gider artışı ve piyasa oynaklığı; hepsi zamana yayılan rampayla uygulanır.
- **Nakit ömrü:** Statik runway = kasa ÷ aylık yakım. Trend runway ise geçmiş
  faaliyet nakdine doğrusal bir eğilim uydurup ileri uzatır. Eğim, en küçük
  kareler yerine **Theil–Sen** (ikili eğimlerin medyanı) ile kestirilir: 12
  gözlemde tek bir anormal ay (toplu tahsilat, tek seferlik gider) en küçük
  kareleri belirgin biçimde çarpıtıyor — demo verisinde gerçek eğilim
  −28.100 ₺/ay iken en küçük kareler −40.552 ₺/ay veriyor, Theil–Sen ise
  −28.211 ₺/ay ile doğru sonucu buluyor.

---

## Testler

```bash
python -m pytest tests/ -q          # pytest ile
python tests/test_finance_math.py   # ya da tek tek, bağımlılıksız
```

**39 test**, üç dosyada:

| Dosya | Neyi korur |
|-------|-----------|
| `test_finance_math.py` (19) | Annüite formülü elle hesaplanmış değerle; itfa tablosunda anapara toplamı = kredi ve vade sonu bakiye = 0; Monte Carlo değişmezleri (olasılık aralığı, tohum tekrarlanabilirliği, yüzdelik bantların sıralaması, "stres artınca batma olasılığı düşemez"); **vektörize çekirdeğin naif referans döngüyle birebir eşitliği** |
| `test_runway.py` (8) | Statik/trend runway ayrışması, Theil–Sen'in aykırı değere dayanıklılığı, yetersiz veride güvenli geri çekilme |
| `test_data_integrity.py` (12) | Mock verinin ortalamalarının skalerlerle birebir tutması, kasa yürüyüşünün sapmasız olması, eksik sütunlu CSV'nin uygulamayı çökertmemesi, Türkçe etiketler |

En kritik test `test_vectorized_kernel_matches_reference`: Monte Carlo çekirdeği
performans için `cumsum`/`argmax` hilesi kullanır ve bu hile sessizce yanlış
olabilir. Test onu okunması kolay, optimize edilmemiş bir referans döngüyle
karşılaştırır ve veri kümesinin **batıp sonradan toparlanan** senaryolar
içerdiğini ayrıca doğrular — optimizasyonun kırılabileceği tek yer orasıdır.

> ⚠️ Sayısal varsayımlar örnektir; kararlarını yalnızca bu prototipe dayandırma.
> Amaç, riski görünür kılıp doğru soruları sordurmaktır.

---

## Yol haritası (gelecek)

- Muhasebe/ERP entegrasyonu (API ile canlı veri)
- Kullanıcı kimlik doğrulama ve şirket bazlı veri izolasyonu
- Ağır simülasyonlar için arka plan işçisi (Celery/Redis) ve senaryo kaydetme
- Duyarlılık (tornado) analizi: hangi sürgü batma olasılığını en çok oynatıyor
- Alacak yaşlandırmasının tahsilat gecikme olasılığını doğrudan beslemesi
  (şu an gecikme tek bir sürgüyle giriliyor, müşteri bazlı değil)
