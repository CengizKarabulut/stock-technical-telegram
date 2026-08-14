# Stock Technical Telegram

GitHub Actions ekranından bir hisse sembolü girerek kapsamlı teknik piyasa durum raporu üretir. PNG ve JSON raporları Actions artifact olarak saklanır; PNG ayrıca Telegram grubunun Genel konusuna gönderilir.

Sistem otomatik AL/SAT kararı veya birleşik puan üretmez. Rejim, yapı, konum, trend, momentum, katılım ve volatilite durumlarını tarafsız biçimde raporlar.

## Telegram hedefi

- Grup kimliği: `-1003502567927`
- Konu: Genel
- Genel konuya gönderimde `message_thread_id` boş bırakılır.

Telegram bağlantısındaki `_1` değeri Bot API konu kimliği değildir. `message_thread_id=1` kullanılması `message thread not found` hatasına neden olur.

## İlk kurulum

1. Telegram'da `@BotFather` üzerinden bir bot oluşturun.
2. Botu hedef gruba ekleyip mesaj ve fotoğraf gönderme yetkisi verin.
3. GitHub reposunda **Settings → Secrets and variables → Actions** bölümünü açın.
4. `TELEGRAM_BOT_TOKEN` adlı repository secret oluşturup BotFather token'ını kaydedin.

Token'ı hiçbir dosyaya, issue'ya veya Actions loguna yazmayın.

## Actions üzerinden çalıştırma

1. **Actions → Hisse Teknik Tarama → Run workflow** yolunu açın.
2. `ticker` alanına `THYAO`, `ASELS`, `TUPRS` veya `AAPL` gibi sembolü girin.
3. BIST hisselerinde `market=BIST`, `provider=AUTO` seçilmesi önerilir.
4. İstenirse `anchor_date` alanına manuel AVWAP başlangıcı `YYYY-MM-DD` biçiminde yazılır. Boşsa yıl başlangıcı kullanılır.
5. SMA/EMA 377 için günlük grafikte en az `period=2y` kullanın.
6. `send_telegram=true` olduğunda rapor Telegram grubunun Genel konusuna gönderilir.

Sağlayıcı seçenekleri:

- `AUTO`: BIST için önce borsapy/TradingView; hata halinde yfinance yedeği.
- `BORSAPY`: BIST için TradingView WebSocket verisini zorunlu kullanır.
- `YFINANCE`: yfinance kaynağını zorunlu kullanır.

## Piyasa durum haritası

Raporun üst özeti puan vermeden şu aileleri gösterir:

- Rejim: yönlü/genişleyen, yönlü/kontrollü, dengeli/sıkışan, yüksek volatilite/yönsüz veya geçiş/karma.
- Yapı: teyitli pivotlardan HH/HL/LH/LL ve son BOS olayı.
- Konum: Value Area, POC, AVWAP, önceki gün ve önceki hafta seviyeleri.
- Trend: fiyatın kaç EMA üzerinde olduğu, yükselen EMA sayısı ve EMA yayılımı.
- Momentum: MACD, RSI, Stochastic RSI ve SMI çizgi ilişkilerinin uyumu.
- Katılım: hacim, RVOL, OBV ve açıkça etiketlenmiş OHLCV delta/CVD tahmini.
- Volatilite: ATR percentile ile Bollinger bandwidth percentile ve genişleme/daralma.

## SMA ve EMA

Tabloda her periyodun kendi SMA ve EMA değeri bulunur:

`5, 8, 10, 13, 20, 21, 34, 50, 55, 89, 100, 144, 200, 233, 377`

- Yeşil: fiyat ortalamanın üzerinde.
- Sarı: fiyat ortalamaya eşit veya `%0,02` tolerans içinde.
- Kırmızı: fiyat ortalamanın altında.

## Momentum ayrıntıları

- MACD level, signal, sıfır çizgisi ve histogramın dört durumlu yorumu.
- RSI ve aynı uzunlukta MA14.
- Stochastic RSI K/D; normal Stochastic özellikle dahil edilmez.
- Kullanıcının verdiği çift EMA yumuşatmalı SMI ve EMA3 sinyali.
- MFI/MA14 ve CCI/MA20.
- Yukarı/aşağı kesişimler, normalize fark ile “kesişime yakın” ayrımı.
- Her ana osilatör için 1 bar değişim, 3 bar değişim ve 5 bar doğrusal eğim.

## Trend, volatilite ve katılım

- ADX/DMI ve ADX tarihsel percentile.
- Supertrend, Ichimoku ve Parabolic SAR.
- Bollinger alt/orta/üst bant konumu, bant genişliği ve percentile.
- ATR, ATR%, ATR percentile ve çok barlı eğim.
- Hacim/SMA20, hacim percentile ve RVOL.
- OBV, OBV EMA20 ve eğim.

## Konum ve hacim profili

- Manuel AVWAP; tarih girilmezse yıl başı anchor.
- Ay, çeyrek ve yıl VWAP değerleri.
- PDH, PDL, PDC; PWH, PWL, PWC ve mevcut hafta açılışı.
- Son 100 bar için yaklaşık POC, VAH, VAL ve `%70` Value Area.
- POC uzaklığı yüzde ve ATR cinsinden.
- Developing POC göçü.
- VAH/VAL kabul, reddedilme ve Value Area rotasyonu.
- Son teyitli MACD, RSI, Stochastic RSI, SMI, Bollinger, Supertrend, BOS ve profil seviye olayları.

### Önemli veri sınırı

GitHub raporu TradingView'dan OHLCV mumlarını alır; TradingView Premium `request.footprint()` verisine erişmez. Bu nedenle:

- POC/VAH/VAL, mum hacminin barın fiyat aralığına dağıtılmasıyla hesaplanan yaklaşık profildir.
- Buy volume, sell volume, delta ve CVD değerleri kapanışın bar aralığındaki konumuna dayanan `OHLCV proxy` olarak açıkça etiketlenir.
- Bu değerler gerçek footprint/order-flow verisi gibi yorumlanmamalıdır.

Gerçek footprint imbalance ve gerçek buy/sell delta ayrı bir TradingView Premium Pine modülü gerektirir. Günlük workflow'da Opening Range kullanılmadığı için rapora eklenmemiştir; intraday aralık desteği açıldığında OR15/OR30/OR60 ayrı modül olarak eklenebilir.

## Yerel çalıştırma

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m src.stock_dashboard --ticker THYAO --market BIST --provider AUTO --period 2y --interval 1d --anchor-date 2026-01-02
```

Telegram gönderimi:

```bash
python -m src.send_telegram
```

`TELEGRAM_MESSAGE_THREAD_ID` boş olduğunda Genel konu kullanılır. Başka bir forum konusu için Bot API'den alınan gerçek konu kimliği verilebilir.

## Veri ve sorumluluk reddi

BIST verisi varsayılan olarak [borsapy](https://github.com/saidsurucu/borsapy) aracılığıyla TradingView WebSocket kaynağından alınır. Kimlik doğrulamasız TradingView verisi yaklaşık 15 dakika gecikmelidir. JSON çıktısındaki `data_provider` alanı o çalışmada fiilen kullanılan kaynağı gösterir.

borsapy kişisel ve eğitim amaçlı kullanım için sunulmaktadır; ticari kullanımda ilgili piyasa veri lisansları gerekir. Veriler gecikmeli veya eksik olabilir. Rapor yalnızca bilgilendirme ve teknik inceleme amaçlıdır; yatırım tavsiyesi değildir.
