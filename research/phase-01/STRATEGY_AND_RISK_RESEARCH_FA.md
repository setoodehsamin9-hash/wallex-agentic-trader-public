# تحقیق استراتژی و سیستم مستقل ریسک

تاریخ: 2026-10-22. حکم این تحقیق «ارزش آزمایش» است، نه اثبات سود. هیچ Candidate در این فاز بک‌تست یا Shadow اجراشده ندارد. شواهد پژوهشی، وجود implementation و سود خالص Wallex سه چیز متفاوت‌اند. معیار تصمیم، تناسب با سه روز رقابت، هزینه، داده واقعی و امکان توضیح به داور است. [B01]

## مقایسه خانواده‌ها و تصمیم حذف

| خانواده و مسئله | سازوکار ساده و شرایط مناسب/بد | داده و شواهد | هزینه/بک‌تست/overfitting | حکم برای این چالش و نقش AI |
|---|---|---|---|---|
| Trend following / momentum؛ استفاده از ادامه حرکت | وقتی جهت قیمت در چند کندل همسو است، در همان جهت وارد می‌شود؛ در حرکت پایدار بهتر، در رفت‌وبرگشت بی‌جهت زیان‌های تکراری می‌دهد. Momentum یعنی تمایل حرکت اخیر به ادامه | OHLCV بسته‌شده؛ شواهد crypto در Liu–Tsyvinski، اما افق و نمونه آن اثبات معاملات دقیقه‌ای TMN نیست [A01] | تعداد معامله متوسط؛ stopهای کوچک و fee می‌توانند edge را بخورند. با candle قابل‌آزمایش است؛ انتخاب طول میانگین و universe خطر fit دارد | ارزش آزمایش محدود؛ در spot فقط خرید و سپس فروش دارایی، روند نزولی → نگهداری TMN؛ AI می‌تواند تعارض سیگنال با شرایط را رد کند |
| Breakout؛ پیدا کردن شروع حرکت | بسته‌شدن قیمت بالای سقف پنجره قبلی؛ شکست ناموفق وقتی قیمت سریع برمی‌گردد. در گسترش نوسان بهتر، در بازار رنج پرخطا | OHLCV، volume و book برای اجرا؛ implementation چارچوب‌های strategy، نه شاهد سود قطعی [J01] | باید سقف از **کندل‌های قبل** باشد؛ خرید در همان close بدون latency ممنوع در مدل پایه؛ slippage زمان جهش مهم | Candidate C1؛ AI فقط از signalهای معتبر انتخاب می‌کند و می‌تواند WAIT کند |
| Mean reversion؛ استفاده از برگشت نوسان | قیمتِ دورشده از میانگین در بازار آرام ممکن است برگردد؛ خرید پس از نشانه برگشت، خروج نزدیک مرکز. در سقوط رونددار خطرناک است | OHLCV؛ Bollinger controller پیاده‌سازی واقعی این خانواده است، نه شواهد سود Wallex [H05] | سود هر رفت‌وبرگشت معمولاً محدودتر و fee حساس‌تر؛ averaging-down خطرناک؛ طول پنجره/آستانه و regime همگی خطر overfit دارند | Candidate C2 مشروط؛ فقط یک ورود، بدون مارتینگل؛ AI باید از ورود در shock/range نامطمئن خودداری کند |
| Volume confirmation؛ تشخیص حرکت کم‌پشتوانه | حجم اخیر با گذشته همان بازار مقایسه می‌شود؛ تأیید volume می‌تواند شکست کم‌معامله را حذف کند؛ حجم زیاد هم می‌تواند پایان جهش باشد | v کندل یا trades؛ unit و پوشش volume باید بررسی شود [W03][W12] | یک فیلتر، نه strategy مستقل؛ افزایش فیلتر تا سودسازی روی train مجاز نیست | یک variant محدود C1، فقط اگر OOS بهبود دهد؛ AI از نسبت حجم به‌عنوان evidence استفاده کند |
| Volatility adjustment؛ مقیاس کردن حرکت و ریسک | نوسان یعنی اندازه تغییرات قیمت؛ threshold و اندازه با دامنه اخیر متناسب می‌شوند؛ نوسان زیاد لزوماً سودآور نیست | high/low/close؛ ATR میانگین دامنه حرکت با احتساب فاصله از close قبلی است؛ محاسبه deterministic | محاسبه ساده، بک‌تست ممکن؛ floor هزینه و cap exposure لازم چون stop تنگ حجم را بالا می‌برد | لایه sizing و stop برای C1/C2؛ standalone alpha رد؛ AI محاسبه نکند |
| Order-book imbalance؛ فشار سمت خرید/فروش | عدم‌تعادل حجم سفارش‌ها می‌تواند فشار کوتاه‌مدت نشان دهد؛ snapshot ثابت با جریان ثبت/لغو سفارش برابر نیست | book پیوسته و trades با timestamp؛ مقاله OFI در ۵۰ سهم آمریکا است، نه crypto ایران [A02] | داده تاریخچه book رسمی پیدا نشد؛ spoofing، asynchronous sides، latency و fee زیاد؛ candle backtest آن نامعتبر است | standalone alpha **رد در این فاز**؛ book فقط فیلتر نقدینگی/اجرای زنده و پژوهش forward |
| Regime detection؛ انتخاب رفتار متناسب | تشخیص وضعیت رونددار، رنج، شوک یا نامطمئن؛ تغییر ناگهانی/نویز می‌تواند تشخیص را دیر کند | features از OHLCV و هزینه/نقدینگی مشاهده‌شده؛ برچسب قطعی طبیعی ندارد | ارزیابی باید با سود/ریسک سیاست و ablation باشد، نه فقط نام درست وضعیت | Candidate C3 مشروط به زمان؛ ML خوشه‌بندی/HMM جدید فعلاً هزینه بی‌دلیل؛ LLM arbitration محدود ساده‌تر است |
| Ensemble / switching؛ کاهش وابستگی به یک فرضیه | انتخاب بین C1/C2/WAIT؛ ensemble یعنی ترکیب چند روش، نه زیاد کردن تعداد indicator | همان داده مشترک، candidateهای versioned و سجل تصمیم | threshold و switching زیاد خطر overfit و churn می‌سازد؛ همبستگی strategyها باید دیده شود | نسخه کنترل‌شده C3؛ هم‌زمان دو bot روی یک حساب ممنوع در معماری ما، چون reservation/own-cross پیچیده می‌شود |
| Market making/grid؛ گرفتن اختلاف خریدوفروش | سفارش دوطرفه با inventory؛ در روند می‌تواند کوین زیان‌ده انباشته کند | book تاریخی، مدل صف و fill، latency و maker fee؛ Hummingbot زیرساخت دارد [R04] | صف، انتخاب نامطلوب، limitهای پرنشده، رفتار سفارش متقاطع خودی و پایان اجباری | **رد**؛ اندازه سرمایه و سه روز فرصت توجیه ساخت شبیه‌ساز و connector لازم را نمی‌دهد |
| Arbitrage چندصرافی/مثلثی | بهره‌برداری از اختلاف قیمت چند مسیر | موجودی/حساب چند venue یا atomic leg، quoteهای هم‌زمان و تبدیل هزینه‌دار | ریسک ناقص‌ماندن یک leg و داده/مجوز بازارها؛ API trading معمولی atomic بودن را ثابت نمی‌کند | **رد**؛ این چالش یک حساب مشخص دارد؛ ظرفیت لازم احراز نشده |
| DCA/martingale / buy-and-hold | خرید اضافه پس از افت یا نگهداری منفعل | داده ساده ولی ریسک در روند بد متمرکز می‌شود | بازده ممکن است فقط جهت بازار باشد؛ نقدکردن نهایی و عدم ورود با کل پول مهم‌اند | **رد به‌عنوان موتور اصلی**؛ buy-and-hold فقط benchmark با فروش نهایی، نه نشانه تکرارپذیری AI [B01] |
| News/social sentiment | تفسیر خبر برای سیگنال | feed دارای زمان انتشار/دریافت و حق استفاده؛ Wallex چنین feed مستندی ارائه نکرده | نشت تاریخی، خبر جعلی، prompt injection، تأخیر و ساخت data pipeline | **رد فعلی**؛ LLM را به خوراک خبری اثبات‌نشده وابسته نکنید |

جدول بالا استنتاج مهندسی متناسب با محدودیت است؛ مقاله momentum دلیل انتخاب قطعی نیست و پژوهش جدیدتر درباره ریسک دنباله آن هشدار دارد. ریسک دنباله یعنی رخدادهای کم‌تکرار با زیان بسیار بزرگ. [A05]

## فقط سه Candidate

همه از یک feed، یک ledger و یک execution/risk kernel استفاده می‌کنند. kernel یعنی بخش مشترکی که ثبت سفارش، موجودی و خروج‌های اجباری را اداره می‌کند. پنجره‌ها و آستانه‌های زیر پارامتر آزمایش‌اند؛ اعداد سودساز آماده ارائه نمی‌شوند.

### C1 — شکست سقف کوتاه‌مدت با تأیید روند + داوری محدود AI؛ اولویت اول

- **فرضیه قابل ابطال:** بخشی از شکست‌های تأییدشده، پس از کل هزینه، ادامه حرکت کافی برای خروج سودده دارند.
- **ورود پیشنهادی:** close کندل کامل از بیشترین high در N کندل قبلی با buffer بزرگ‌تر شود؛ جهت/شیب روند بلندتر مثبت؛ variant حجمی صرفاً نسبت volume به میانه گذشته را می‌سنجد. buffer تابع نوسان و حد هزینه است، نه درصد کپی‌شده.
- **AI واقعی:** از candidateهای هم‌زمان و ویژگی‌های محاسبه‌شده، یک signal_id یا WAIT انتخاب می‌کند؛ اگر signal نامعتبر باشد حق ایجاد آن را ندارد. تصمیم AI واقعاً می‌تواند ورود را حذف یا بازار را عوض کند.
- **خروج:** hard stop، time exit و یک variant از TP ثابت یا trailing؛ همه deterministic. برای جلوگیری از optimizer بی‌حد، TP ثابت و trailing هم‌زمان چندین حالت نشوند.
- **داده/بازار:** کندل ۱دقیقه معتبر، تجمیع آزمایشی به ۵ یا ۱۵دقیقه؛ این بازه‌ها پیشنهاد آزمایش‌اند، نه مزیت اثبات‌شده. یک تا دو بازار TMN با قابلیت خروج تأییدشده؛ BTCTMN/ETHTMN فقط نقطه شروع بررسی هستند. [P01][P02][P04]
- **شکست محتمل:** قیمت جهش کند ولی order دیر برسد؛ fee سود را بخورد؛ AI فرصت‌ها را بی‌دلیل حذف کند.
- **هزینه توسعه:** کم تا متوسط برای strategy پس از آماده‌شدن data/execution؛ ساخت اتصال معمولاً بخش دشوارتر است. با baseline یکسان بدون AI مقایسه شود.
- **شرط کنارگذاشتن:** سود فقط در یک پنجره یا یک تنظیم دیده شود، با هزینه سخت‌تر محو شود، یا AI هیچ تأثیر معتبر و قابل‌اندازه‌گیری نداشته باشد. [F03][F04]

### C2 — برگشت به میانگین در بازار آرام + veto واقعی AI؛ اولویت دوم مشروط

- **فرضیه:** در بازه‌های واقعاً رنج، افت کوتاه و برگشت اولیه می‌تواند سودی بزرگ‌تر از هزینه رفت‌وبرگشت بدهد.
- **ورود:** فاصله منفی قیمت از میانگین rolling، سپس کندل تأیید برگشت؛ ورود صرفاً چون «قیمت پایین است» رد. روند نزولی شدید/نوسان جهشی → حذف candidate.
- **AI:** با evidence محدود تصمیم ورود را تأیید یا WAIT می‌کند؛ شرح ریسک آن هم ذخیره می‌شود، ولی عدد stop/sizing را عوض نمی‌کند.
- **خروج:** بازگشت به مرکز یا time limit و hard stop؛ افزایش اندازه پس از افت ممنوع. اگر فاصله تا هدف پس از fee/slippage اندک است اصلاً candidate نسازید.
- **backtest:** candle test عملی است؛ entry/TP همان کندل و spread ناموجود تاریخی به‌صورت محافظه‌کارانه مدل شود. Bollinger نمونه implementation است؛ استفاده از دو باند شرط سود ندارد. [H05]
- **شکست محتمل:** بازار از رنج به سقوط برود؛ چند برد کوچک با یک افت بزرگ از بین برود. به fee از C1 حساس‌تر است.
- **شرط پذیرش:** رفتار مستقل از C1 و ارزش بعد از هزینه نشان دهد؛ در غیر این‌صورت حذف، نه حفظ صرفاً برای تنوع.

### C3 — انتخاب‌گر AI بین C1 / C2 / WAIT؛ اولویت سوم و افزونه

این Candidate **آلفای مستقل سوم نیست**؛ معماری تصمیم‌گیری جایگزین روی دو خانواده است. آلفا یعنی مزیت بازده پس از هزینه که هنوز در این پروژه اثبات نشده است.

- دو مولد deterministic، فرصت‌های معتبر و محدود را می‌سازند؛ LLM وضعیت بازار و evidence را مقایسه و فقط یکی را انتخاب می‌کند. تغییر strategy با هر tick مجاز نیست؛ حداقل ماندگاری/cooldown نسخه‌دار و قابل تیون داشته باشد.
- ویژگی‌ها: بازده‌های گذشته، شیب روند، دامنه/ATR، فاصله از مرکز/سقف، نسبت حجم، هزینه تخمینی و ریسک/موجودی. هیچ خبر یا داده نادیده ساخته نشود.
- اگر C2 مستقل پذیرفته نشد، C3 به C1/WAIT تقلیل می‌یابد؛ این باید در عنوان، گزارش و ادعای ensemble صریح باشد.
- زیربنای فنی از Condor مناسب است؛ full Condor یا آموزش RL لازم نیست. همه intentها از یک gate ریسک عبور می‌کنند. [C01][C02]
- **شرط پذیرش:** نسبت به selector ساده deterministic یا C1 تنها ارزش قابل‌دفاع در Shadow/OOS داشته باشد؛ در غیر این‌صورت پیچیدگی را حذف کنید. صرف تولید توضیح زیبا استفاده واقعی از AI نیست. [B01]

## نیاز داده به تفکیک Candidate

علامت لازم در جدول به معنی نیاز کل سیستم است؛ حتی strategy کندلی برای execution به book و account نیاز دارد.

| Candidate | OHLCV تاریخی/زنده | trades | book / spread / depth | balances | open orders | execution/fills | داده خارج Wallex |
|---|---|---|---|---|---|---|---|
| C1 | لازم؛ کندل کامل و تاریخچه warmup | اختیاری برای تأیید/بررسی؛ نه شرط نسخه اول | لازم برای اجرای زنده؛ آرشیو تاریخی UNKNOWN | لازم | لازم | لازم با feeAsset | لازم نیست |
| C2 | لازم، میانگین و دامنه گذشته | اختیاری | لازم؛ حساسیت spread بیشتر | لازم | لازم | لازم | لازم نیست |
| C3 | همان C1/C2 + candidate snapshots | اختیاری | خلاصه معتبر هزینه/نقدینگی؛ unavailable بودن صریح | لازم، فقط خلاصه امن به AI | لازم برای kernel | لازم برای attribution | مدل ازپیش‌آموزش‌دیده؛ feed جدید لازم نیست |
| OBI مستقل ردشده | کافی نیست | stream زمان‌دار لازم | تاریخچه book/sequence و latency لازم، فراهم بودن اثبات نشده | لازم | لازم | لازم | dataset آرشیوی معتبر نداریم |

OHLCV یعنی قیمت شروع، بیشترین، کمترین، پایان و حجم در هر بازه. وجود زنده trades/book در API به معنی موجود بودن نسخه تاریخی آن نیست. چون backtest فیلتر book تاریخی ممکن نیست، نتایج candle-only و نتایج forward با فیلتر book باید جدا گزارش شوند؛ ترکیب آن‌ها زیر عنوان «همان بک‌تست کامل live» نادرست است. [W03][W11][W12][W19]

## ریسک به‌عنوان سیستم مستقل

ترتیب پیشنهادی: ورودی داده معتبر → candidate → انتخاب AI → gate ریسک → reservation سرمایه → سفارش → fill → ledger → کنترل خروج. پاسخ AI قبل از submit دوباره از نظر اعتبار زمانی/موجودی بررسی می‌شود. ریسک هیچ‌گاه با پیشنهاد مدل override نمی‌شود. [N01][C01]

### قواعد معماری در برابر پارامترهای قابل تیون

| کنترل | اصل ثابت طراحی | پارامتر قابل آزمایش/تصمیم | خطر محافظه‌کاری افراطی |
|---|---|---|---|
| position sizing | با زیان بالقوه stop و هزینه محاسبه، سپس cap؛ نه «همه پول» | budget هر معامله، stop distance، cap notional | اندازه بسیار کم زیر minNotional؛ صفر معامله |
| max exposure | دارایی پرشده + خریدهای pending/reserved را جمع کنید؛ فروش باز دارایی را دوبار آزاد نمی‌کند | سقف کل/هر نماد، تعداد موقعیت‌ها | سقف خیلی کم بهره‌گیری از فرصت معتبر را حذف می‌کند |
| per-trade risk | ریسک مبلغی ثبت و enforce شود؛ stop تضمین زیان حداکثر نیست | سهم budget و هزینه stress | stopهای بیش‌ازحد نزدیک با fee/نویز فعال می‌شوند |
| stop loss | برای مقدار واقعاً پرشده فوراً حفاظت؛ خروج به fill معتبر وابسته | ضریب volatility و حداکثر فاصله | tight stop در crypto نوسانی turnover زیان‌زا می‌سازد |
| take profit | target پس از هزینه معنی‌دار؛ فروش فقط از موجودی آزاد | هدف ثابت/بازگشت مرکز | TP کوچک برد زیاد ولی P&L منفی می‌دهد |
| trailing | فقط به سمت کاهش ریسک تغییر کند؛ state پایدار روی restart | زمان فعال‌شدن، فاصله از peak | خیلی نزدیک روند سودآور را زود قطع می‌کند |
| time exit | نگهداری نامحدود ممنوع؛ پایان مسابقه مقدم بر زمان strategy | max holding، timeout سفارش ورود | زمان کم فرصت بلوغ signal را می‌گیرد |
| max drawdown | equity واقعی/قابل نقدشدن، high-water mark، هر دو realized و unrealized | سقف pause و سقف emergency | حد خیلی کم یک spread/fee عادی را بحران می‌کند |
| daily/session loss | reset تعریف‌شده؛ restart حد را پاک نمی‌کند؛ حد کل ۳روز باقی بماند | session timezone و بودجه زیان | reset روزانه مجوز بازیابی کل budget نیست |
| loss streak/cooldown | برای زیان‌های متوالی ورود محدود شود، خروج باز مجاز بماند | تعداد، پنجره و زمان قفل | نمونه کم می‌تواند کل سه روز را قفل کند؛ شرط و lock قابل توضیح باشد |
| volatility sizing | stop نوسانی + cap مستقل؛ ATR بسیار کوچک حجم نامحدود نسازد | lookback و کف نوسان/هزینه | scale شدید با دو شوک، سرمایه را بی‌دلیل کوچک می‌کند |
| liquidity | bid/ask تازه، عمق تا حجم سفارش، عدم crossed book و مسیر خروج | سقف spread، سهم عمق، بودجه price impact | فیلتر بسیار تنگ روی همه بازارهای TMN فرصت‌ها را صفر می‌کند |
| slippage | پیش‌برآورد برای اندازه مشخص + سنجش fill واقعی؛ خروج fallback محدود | سقف انحراف عادی/اضطراری و escalation | رد همه خروج‌ها به علت slippage زیان را نگه می‌دارد |
| balance/size | available و locked و fee currency تطبیق؛ minQty/minNotional/precision validate | fee buffer و dust policy | rounding-up برای عبور حداقل ممنوع، اگر ریسک بیشتر شود |
| stale data | age هر feed و coherence دو سمت book ثبت؛ جدید نبودن داده ورود را منع کند | TTL متناسب cadence و latency اندازه‌گیری‌شده | TTL غیرواقعی همه feedها را stale می‌کند |
| duplicate | کلید یکتای intent؛ persist قبل از ارسال؛ timeout=UNKNOWN نه FAILED | پنجره dedupe و حداکثر retry budget | id جدید برای retry می‌تواند خرید تکراری ایجاد کند |
| kill switch | latch پایدار؛ ورودی بسته، سفارش ورودی لغو، خروج دارایی تا تأیید دنبال شود | trigger افت، خطای سریالی، mismatch، outage | توقف ورودی نباید با توقف watcher خروج یکی باشد |
| liquidation پایان | no-new-entry، cancel، reconcile، فروش net holdings به TMN، تأیید fill و باقی‌مانده | حاشیه امن تا cutoff و اندازه chunk | خروج خیلی زود فرصت کم می‌کند؛ خیلی دیر ناتوانی در تسویه |

این جدول پیشنهاد طراحی است، با ایده‌های [N01][N02][N03][H01][F01][F02][C04]. سیاست‌های platform liquidation، market availability و partial fills باید از Wallex احراز شوند؛ هیچ threshold منبع دیگری default مسابقه نیست. [W05][W06][P01]

### فرمول قابل‌پیاده‌سازی sizing؛ با واحد روشن

برای خرید spot به تومان:

`budget_TMN = equity_TMN × risk_fraction`

`unit_loss_TMN = entry_price − stop_price + estimated_fee_per_unit + stress_execution_cost_per_unit`

`qty_raw = min(budget_TMN / unit_loss_TMN, per_asset_notional_cap / entry_price, remaining_total_cap / entry_price, spendable_TMN / (entry_price + buy_fee_per_unit), liquidity_cap_qty)`

پس از آن، مقدار **رو به پایین** برحسب precision معتبر گرد شود و minQty/minNotional کنترل شود؛ صفر/NaN/stop نامعتبر → NO_TRADE. ریسک تمام pending buyها هم reserve شود. اگر fee در کوین گرفته شود مقدار قابل فروش از origQty کمتر است؛ خروج از net fills و موجودی واقعی ساخته شود. [W05][W08][N02]

این فرمول تقریب برنامه‌ریزی برای spot است؛ fee سمت خروج و slippage gap باید در unit_loss پوشش داده شوند. در سناریوی gap واقعی ممکن است زیان از budget فراتر رود. از probability لفظی LLM برای Kelly sizing استفاده نکنید؛ احتمال کالیبره‌شده نداریم.

**مثال صرفاً برای منطق بودجه، نه تنظیم پیشنهادی live:** اگر تیم بودجه زیان کل را ۱۵۰هزار تومان و تحمل شش stop پرهزینه را تصویب کند، بودجه اسمی هر stop ۲۵هزار تومان می‌شود؛ اجرای بدتر/هزینه اضافی آن را مصرف می‌کند. این اعداد باید با داده و خواست تیم تعیین شوند، از قانون مسابقه استخراج نشده‌اند. cap مستقل exposure هنوز لازم است، چون stop خیلی نزدیک نباید حجم را نامحدود کند.

### حساب سود و هزینه؛ علت رد scalping

`net_realized_TMN = cash_received_from_sells − cash_spent_on_buys − fees_not_already_net_in_cashflows − conversion_costs`

کارمزد را دوبار کم نکنید؛ اگر fill در cashflow خالص لحاظ شده، fee همان معامله دوباره subtract نشود. دارایی باقی‌مانده سود realized نیست؛ برای کنترل ریسک، ارزش نقدشوندگی آن جداست. [W08][B01]

مثال آموزشی: اگر **فرضاً** fee هر سمت ۰٫۲٪ و کل هزینه spread/slippage رفت‌وبرگشت ۰٫۱۵٪ باشد، حرکت ۰٫۳٪ قبل از هزینه تقریباً منفی ۰٫۲۵٪ می‌شود. این‌ها نرخ والکس نیستند. نرخ واقعی حساب از `/v1/account/fee` و fillها می‌آید. بنابراین «تعداد زیاد معامله» بدون فاصله سود از هزینه راهبرد پذیرفته‌شده نیست. [W13]

### ماشین حالت اجرا و خروج

حالت‌های زیر **داخلی پیشنهادی** هستند، enum رسمی Wallex نیستند:

`INTENT → RESERVED → SUBMITTING → ACK/UNKNOWN → OPEN/PARTIAL → FILLED یا CANCEL_PENDING → CANCELED_WITH_RESIDUAL → EXIT_PENDING → FLAT`

- پیش از ارسال، intent و client_id ثبت و سرمایه reserve شود. پاسخ گم شد: وضعیت با همان شناسه از Wallex خوانده شود؛ سفارش با id تازه کورکورانه تکرار نشود. عمر uniqueness سرور و تضمین idempotency پس از لغو UNKNOWN است. [W05][W08]
- fill جزئی ریسک واقعی است؛ برای همان مقدار net حفاظت ایجاد کنید. تأخیر در حفاظت تا FILLED شدن کامل را از executor مرجع کپی نکنید. [H02]
- cancel → تأیید + reconcile؛ fill هم‌زمان با cancel محتمل است. موجودی و fill حقیقت عملی‌اند، ack به معنی پرشدن نیست. [W07][W08]
- یک coordinator برای exitها؛ stop و TP مستقل ممکن است موجودی یکدیگر را lock کنند. OCO بومی تأییدنشده است؛ double-sell و own-cross پیش از submit بررسی شوند. [W05][W06]
- خروج ریسک‌کاهنده اولویت دارد، ولی gate اعتبار حساب/مقدار و جلوگیری از فروش تکراری را دور نمی‌زند. تحت book stale یا outage، کاهش exposure ممکن است فوری ممکن نباشد؛ watcher و مسیر escalation زنده بماند.
- Market-disabled: پیش از ورود روش limit خروج با timeout/reprice محدود و آثار ریسک احراز شود؛ برای نسخه اول حذف این نمادها ساده‌تر است. Market-enabled هم تضمین fill نیست. [P02][W06]
- پایان: clock رسمی، no-entry buffer، لغو خریدهای باز، reconcile، بستن همه ارزها به TMN، دنبال‌کردن partial/reject، گزارش dust و سفارش unresolved. به auto-sell طرح تکیه نکنید. [B01][P01]

## دروازه پذیرش Candidate

قبل از انتخاب نهایی، یک بودجه محدود آزمایش و ترتیب تصمیم ثبت شود: B0 baseline → C1 → C2 فقط در صورت زمان → C3 فقط پس از معتبر شدن زیرمدل‌ها. همه با سرمایه، بازار، دوره و هزینه مشترک مقایسه شوند. نبود نتیجه کافی، «مزیت اثبات نشد» است؛ برای فعالیت ۱۵امتیازی معامله بی‌دلیل تولید نکنید. [B01][A03]

## ارجاع‌ها

[A01]: https://www.nber.org/papers/w24877 "Risks and Returns of Cryptocurrency — Liu & Tsyvinski؛ دسترسی 2026-10-22"
[A02]: https://arxiv.org/abs/1011.6402 "The Price Impact of Order Book Events — Cont, Kukanov, Stoikov؛ دسترسی 2026-10-22"
[A03]: https://escholarship.org/uc/item/4w1110bb "The Probability of Backtest Overfitting — Bailey et al.؛ دسترسی 2026-10-22"
[A05]: https://onlinelibrary.wiley.com/doi/10.1002/ijfe.70036 "Cryptocurrency Momentum: Is It an Illusion?؛ دسترسی 2026-10-22"
[B01]: evidence/BRIEF_PROVIDED_FA.md "Brief رسمی ارائه‌شده توسط صاحب پروژه؛ دسترسی 2026-10-22"
[C01]: https://github.com/hummingbot/condor/blob/8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c/condor/agents/risk.py "Agent risk gate؛ دسترسی 2026-10-22"
[C02]: https://github.com/hummingbot/condor/blob/8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c/condor/agents/engine.py "Agent engine؛ دسترسی 2026-10-22"
[C04]: https://github.com/hummingbot/condor/blob/8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c/tests/test_risk_gate.py "Risk gate tests؛ دسترسی 2026-10-22"
[F01]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/freqtrade/plugins/protections/max_drawdown_protection.py "Drawdown protection؛ دسترسی 2026-10-22"
[F02]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/freqtrade/plugins/protections/stoploss_guard.py "Stoploss guard؛ دسترسی 2026-10-22"
[F03]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/docs/backtesting.md "Backtesting assumptions؛ دسترسی 2026-10-22"
[F04]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/docs/lookahead-analysis.md "Lookahead analysis؛ دسترسی 2026-10-22"
[H01]: https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/hummingbot/strategy_v2/executors/position_executor/data_types.py "TripleBarrierConfig؛ دسترسی 2026-10-22"
[H02]: https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/hummingbot/strategy_v2/executors/position_executor/position_executor.py "PositionExecutor؛ دسترسی 2026-10-22"
[H05]: https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/controllers/directional_trading/bollinger_v1.py "Bollinger controller؛ دسترسی 2026-10-22"
[J01]: https://github.com/jesse-ai/jesse/blob/4fcbcdd500b9cd14ca9bdb142e4f46a34884a1ae/jesse/strategies/Strategy.py "Strategy hooks و ML features؛ دسترسی 2026-10-22"
[N01]: https://github.com/nautechsystems/nautilus_trader/blob/9156e8f2d7126236269b6fcd2e6402b7a8e4e6d8/crates/risk/src/engine/mod.rs "RiskEngine؛ دسترسی 2026-10-22"
[N02]: https://github.com/nautechsystems/nautilus_trader/blob/9156e8f2d7126236269b6fcd2e6402b7a8e4e6d8/crates/risk/src/sizing.rs "Fixed-risk sizing؛ دسترسی 2026-10-22"
[N03]: https://github.com/nautechsystems/nautilus_trader/blob/9156e8f2d7126236269b6fcd2e6402b7a8e4e6d8/docs/concepts/execution/reconciliation.md "Reconciliation؛ دسترسی 2026-10-22"
[P01]: https://api.wallex.ir/v1/prop/packages/183 "طرح ۱۸۳: پاسخ عمومی واقعی؛ دسترسی 2026-10-22"
[P02]: https://api.wallex.ir/hector/web/v1/markets "مارکت‌های اسپات: پاسخ عمومی واقعی؛ دسترسی 2026-10-22"
[P04]: https://api.wallex.ir/v1/udf/history?symbol=BTCTMN&resolution=1&from=1792566950&to=1792653350 "تاریخچه ۱دقیقه BTCTMN: پاسخ واقعی؛ دسترسی 2026-10-22"
[R04]: https://github.com/hummingbot/hummingbot/tree/9af100d6822da7d2d0291a906c730ef172284ee2 "hummingbot/hummingbot؛ دسترسی 2026-10-22"
[W03]: https://developers.wallex.ir/docs/basic-services-candles "کندل‌ها — Wallex API Documentation؛ دسترسی 2026-10-22"
[W05]: https://developers.wallex.ir/docs/spot-create-order "ثبت سفارش اسپات — Wallex API Documentation؛ دسترسی 2026-10-22"
[W06]: https://developers.wallex.ir/docs/spot-types "انواع سفارش اسپات — Wallex API Documentation؛ دسترسی 2026-10-22"
[W07]: https://developers.wallex.ir/docs/spot-cancel-order "لغو سفارش — Wallex API Documentation؛ دسترسی 2026-10-22"
[W08]: https://developers.wallex.ir/docs/spot-single-order-detail "جزئیات سفارش — Wallex API Documentation؛ دسترسی 2026-10-22"
[W11]: https://developers.wallex.ir/docs/basic-services-depth "عمق بازار — Wallex API Documentation؛ دسترسی 2026-10-22"
[W12]: https://developers.wallex.ir/docs/basic-services-latest-trades "آخرین معاملات — Wallex API Documentation؛ دسترسی 2026-10-22"
[W13]: https://developers.wallex.ir/docs/basic-services-account-fee "کارمزد حساب — Wallex API Documentation؛ دسترسی 2026-10-22"
[W19]: https://developers.wallex.ir/docs/socket-buy-depth "عمق خرید وب‌سوکت — Wallex API Documentation؛ دسترسی 2026-10-22"
