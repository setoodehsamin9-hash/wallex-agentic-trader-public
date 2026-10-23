# بررسی تصمیم‌محور Repositoryها

تاریخ دسترسی همه منابع: 2026-10-22. هشت مخزن الزامی و دو مکمل بررسی شدند. فعالیت با تاریخ آخرین commit روی branch پیش‌فرض ثبت شد؛ این عدد کیفیت، انتشار پایدار یا عبور CI را ثابت نمی‌کند. هیچ مخزن نصب/اجرا نشد و تست upstream اجرا نشده است. متن و اجزای کد زیر بررسی ایستا شدند؛ این گزارش ادعای audit کامل تمام سورس ندارد.

## نسخه‌های مبنا

| Repo | Branch | Commit بررسی‌شده | زمان commit (UTC) | License در همان نسخه |
|---|---|---|---|---|
| [jesse-ai/jesse][R01] | master | `4fcbcdd500b9` | 2026-10-17T14:26:18Z | MIT |
| [freqtrade/freqtrade][R02] | develop | `c051e9065c5b` | 2026-10-22T05:26:11Z | GPL-3.0 |
| [nautechsystems/nautilus_trader][R03] | develop | `9156e8f2d712` | 2026-10-22T06:18:08Z | LGPL-3.0 |
| [hummingbot/hummingbot][R04] | master | `9af100d6822d` | 2026-09-22T08:53:54Z | Apache-2.0 |
| [hummingbot/condor][R05] | main | `8e4860fa3d7e` | 2026-10-19T15:13:28Z | MIT |
| [AI4Finance-Foundation/FinRL-Trading][R06] | master | `4409abe925c9` | 2026-09-18T01:13:20Z | Apache-2.0 |
| [AI4Finance-Foundation/FinRL][R07] | master | `c0018c25de86` | 2026-10-20T08:26:37Z | MIT |
| [polakowo/vectorbt][R08] | master | `dd8eb3a3fe9e` | 2026-10-19T21:31:04Z | Apache-2.0 + Commons Clause |
| [kernc/backtesting.py][R09] | master | `ca2e2611621e` | 2026-08-05T12:39:16Z | AGPL-3.0 |
| [ccxt/ccxt][R10] | master | `92c1eb27f465` | 2026-10-22T06:52:28Z | MIT |

همه در metadata عمومی archived=false بودند. برای معماری بعدی SHA کامل در `evidence/repository_versions.json` ثبت شده است. commit همان روز روی develop لزوماً نسخه مناسب نصب نیست.


## شواهد تکمیلی نگهداری فعلی

برای هشت مخزن الزامی، علاوه بر SHA، پنج commit اخیر و latest-release metadata عمومی دریافت شد. این نمونه کوچک، شمار فعالیت ماهانه یا تعهد پشتیبانی نیست. بازه طولانی‌تر پنج commit در FinRL-Trading نسبت به چند پروژه دیگر نشانه سرعت تغییر کمتر در این نمونه است؛ دلیل abandoned بودن محسوب نمی‌شود. 404 در latest release فقط یعنی از آن endpoint نسخه‌ای تأیید نشد. release در جدول لزوماً با branch/SHA تحقیق یکسان نیست و به معنی انتخاب نصب آن نیست.

| Repo | بازه زمانی پنج commit نمونه (UTC) | latest release مشاهده‌شده |
|---|---|---|
| [jesse-ai/jesse][M01] | 2026-10-16 تا 2026-10-17 | latest-release endpoint: 404؛ نسخه release تأیید نشد |
| [freqtrade/freqtrade][M02] | 2026-10-22 تا 2026-10-22 | [2026.9](https://github.com/freqtrade/freqtrade/releases/tag/2026.9)؛ 2026-09-29 |
| [nautechsystems/nautilus_trader][M03] | 2026-10-22 تا 2026-10-22 | [v1.231.0](https://github.com/nautechsystems/nautilus_trader/releases/tag/v1.231.0)؛ 2026-08-02 |
| [hummingbot/hummingbot][M04] | 2026-09-18 تا 2026-09-22 | [v2.17.0](https://github.com/hummingbot/hummingbot/releases/tag/v2.17.0)؛ 2026-09-22 |
| [hummingbot/condor][M05] | 2026-10-18 تا 2026-10-19 | latest-release endpoint: 404؛ نسخه release تأیید نشد |
| [AI4Finance-Foundation/FinRL-Trading][M06] | 2026-05-02 تا 2026-09-18 | [v1.0.0](https://github.com/AI4Finance-Foundation/FinRL-Trading/releases/tag/v1.0.0)؛ 2026-03-25 |
| [AI4Finance-Foundation/FinRL][M07] | 2026-10-13 تا 2026-10-20 | [v0.3.8](https://github.com/AI4Finance-Foundation/FinRL/releases/tag/v0.3.8)؛ 2026-03-20 |
| [polakowo/vectorbt][M08] | 2026-10-19 تا 2026-10-19 | [v1.2.0](https://github.com/polakowo/vectorbt/releases/tag/v1.2.0)؛ 2026-10-19 |

## ۱. Jesse — الگوی مناسب برای قرارداد Strategy

**حکم: Reimplement concept؛ برای موتور live مرجع، dependency مستقیم فعلاً نه.** [R01]

| محور | یافته و کاربرد برای Wallex |
|---|---|
| هدف/معماری | چارچوب Python برای تحقیق، بک‌تست و اجرای استراتژی؛ Strategy parent و state معاملات، با زیرساخت و ابزارهای اضافی |
| Strategy framework | hookهای `should_long/short`، `go_long/short` و `update_position` مرز سیگنال و مدیریت موقعیت را نشان می‌دهند [J01] |
| sizing | `risk_to_qty` و تبدیل اندازه به مقدار دارایی؛ ایده sizing با فاصله stop مفید است، واحدها/fee/precision خودمان لازم است [J02] |
| SL/TP/trailing/time | stop_loss و take_profit در Strategy؛ تغییر stop در update_position قابل طراحی است؛ سقف زمان مستقل برای Wallex باید صریح اضافه شود [J01] |
| drawdown | داشتن metrics یا sizing جای kill switch حساب را نمی‌گیرد؛ در اجزای بررسی‌شده حفاظت کامل equity حساب والکس تأیید نشد |
| lifecycle/live | مدل Order و smart ordering دارد؛ connector Wallex در نام فایل‌های snapshot پیدا نشد؛ semantics اعتبار، partial fills و restart هنوز کار ماست |
| data/backtest | candle-centric با چند بازار/بازه؛ موتور backtest و تست price-gap موجود است؛ دقت واقعی اجرای Wallex از آن نتیجه نمی‌شود |
| paper/shadow | README sessionهای backtest/paper/live را مطرح می‌کند؛ بدون اتصال Wallex، shadow اختصاصی لازم است |
| AI | در نسخه بررسی‌شده ML features/export/predict و MCP وجود دارد؛ RL در README «coming soon» است؛ Jesse MCP همان Wallex MCP نیست [J01] |
| testing/reproducibility | `tests/test_price_gap_backtest.py` و backtest tests در tree؛ ثبت config/dataset/seed و خروجی AI همچنان مسئولیت تیم است |
| License/integration | core MIT؛ شرایط افزونه‌های live/تجاری جداگانه باید در صورت انتخاب بررسی شود. افزودن adapter و درک چرخه داخلی برای زمان کم هزینه دارد |

از این پروژه **interface کم‌حجم Strategy و sizing قابل‌فهم** می‌گیریم. پکیج کامل، UI و optimizer چندپردازشی برای دو فرضیه محدود لازم نیست. وجود ML جدید هم دلیل کافی برای انتخاب چارچوب نیست؛ نیاز اصلی ما قرارداد داده و اجرای درست است.

## ۲. Freqtrade — بهترین مرجع حفاظت و محدودیت‌های بک‌تست

**حکم: Reimplement concept؛ در صورت انطباق عمدی پروژه با GPL، Adapt قابل بازبینی است.** [R02]

| محور | یافته و تصمیم |
|---|---|
| هدف/معماری | بات crypto کندل‌محور؛ strategy callbacks، persistence، exchange abstraction و optimize |
| strategy/sizing | callbackهای ورود/خروج، `custom_stake_amount` و `custom_stoploss`؛ تقسیم سرمایه بین max_open_trades راه ساده کنترل exposure است [F05] |
| SL/TP/trailing/time | stoploss/trailing، ROI زمان‌وابسته و custom_exit؛ قابلیت نرم‌افزار با سفارش بومی روی هر صرافی یکسان نیست |
| drawdown/cooldown | MaxDrawdown، StoplossGuard و CooldownPeriod مستقیم به مسئله حفظ سرمایه می‌خورند [F01][F02] |
| نکته کد مهم | MaxDrawdown معاملات **بسته‌شده** را با `is_open=False` می‌خواند؛ حتی mode موسوم به equity جای افت ارزش موقعیت‌های باز را پوشش نمی‌دهد. برای Wallex equity زنده و موجودی قفل‌شده را مستقل بسنجید [F01] |
| order lifecycle | وضعیت و fill در persistence/exchange و حلقه bot؛ cancel/retry و stop-on-exchange به connector وابسته‌اند |
| backtesting | fee دارد؛ اما مستند پیش‌فرض fill در قیمت درخواست در دامنه high/low و نبود slippage را اعلام می‌کند. نتیجه بدون stress اجرا مبنای سود واقعی نیست [F03] |
| dry-run/shadow/live | Dry-run چارچوب نمونه مفیدی است؛ Paper fill وابسته به شبیه‌ساز است و live از لایه exchange می‌گذرد |
| data/AI | OHLCV/pandas؛ FreqAI زیرساخت آموزش/بازآموزی ML است، LLM اجباری یا edge آماده نیست [F06] |
| tests/reproducibility | تست protections، lookahead-analysis و recursive-analysis موجود؛ universe و startup history ثابت برای replay لازم‌اند [F04][F07] |
| License | GPL-3.0؛ انتقال کد به پروژه ترکیبی می‌تواند الزامات انتشار متناظر ایجاد کند. انتشار سورس مسابقه خودبه‌خود همه الزامات GPL یا سازگاری dependencies را حل نمی‌کند |
| integration | Wallex در tree و CCXT snapshot پیدا نشد. انتخاب full framework یعنی ساخت connector در کنار یادگیری قوانین آن؛ برای هکاتون توصیه اول نیست |

از روی توصیف رفتاری عمومی، **قفل ورود بعد از زیان، cooldown و کشف نشت آینده** را مستقل طراحی کنید؛ بازنویسی خط‌به‌خط کد GPL با اسم متفاوت انتخاب مجوز را دور نمی‌زند.

## ۳. NautilusTrader — مرجع معماری قابل‌اعتماد، هزینه بالای اتصال

**حکم: Reference only / Reimplement concept؛ dependency اصلی رد در زمان کوتاه.** [R03]

| محور | یافته و تصمیم |
|---|---|
| معماری | event-driven با Rust core، Python interface، data/execution/risk/portfolio و cache/message bus؛ event-driven یعنی واکنش به رویداد مشخص به‌جای تغییر state پراکنده |
| strategy/data | داده typed شامل bar، quote، trade و book؛ strategy رویداد مصرف می‌کند؛ نیازمند ساخت instrument و adapter دقیق Wallex |
| risk/sizing | RiskEngine قبل از execution سفارش را کنترل می‌کند؛ balance، max notional، trading state، throttler و reservations در کد دیده شد [N01] |
| sizing جزئی | fixed-risk با entry/stop/equity، سقف مقدار، batch و precision؛ از ایده استفاده شود، مدل fee و واحد ریسک برای Wallex از نو بررسی شود [N02] |
| SL/TP/trailing/time | مدل سفارش و execution از شرط‌ها/expiry پشتیبانی می‌کند؛ بومی یا شبیه‌سازی‌شده بودن تابع adapter است، وجود فریم‌ورک تضمین Wallex نیست |
| drawdown | pretrade risk engine مساوی سیاست max-drawdown خودکار مسابقه نیست؛ high-water mark و session policy باید تنظیم/اضافه شوند |
| lifecycle/live | startup/inflight/continuous reconciliation دقیقاً برای سفارش گم‌شده، restart و fill دیررس آموزنده است [N03] |
| backtest | fill model، book level و bar matching قابل تنظیم؛ حتی book تاریخی immutable می‌تواند نقدینگی را چندبار مصرف کند مگر consumption مدل شود [N04][N05] |
| paper/shadow | adapter sandbox در tree و شبیه‌ساز دارد؛ sandbox به معنی testnet والکس نیست |
| AI | زیرساخت اجرای الگوریتم است؛ edge AI یا LLM آماده برای Wallex تأیید نشده |
| tests/reproducibility | risk engine tests و seed مدل fill؛ زمان، ترتیب داده و config باید ثابت باشند؛ تست‌ها اینجا اجرا نشدند |
| License/integration | LGPL-3.0؛ link/modify/distribute الزامات متفاوت دارد. Rust build، typed instrument و data/execution adapter هزینه یادگیری بالاتر دارند |

**چرخه intent → risk → execution → reconciliation** ارزشمند است. وارد کردن کل زیرساخت برای دو بازار ممکن است وقت موردنیاز برای Shadow و Demo را مصرف کند.

## ۴. Hummingbot — مدیریت یک موقعیت و سه شرط خروج

**حکم: Adapt محدود با حفظ Apache notices یا Reimplement concept؛ full connector migration فعلاً رد.** [R04]

| محور | یافته و تصمیم |
|---|---|
| هدف/معماری | اجرای crypto از طریق connector، controller و executor؛ Strategy V2 منطق تصمیم را از عملیات معامله جدا می‌کند |
| Strategy framework | controllerهای directional و market making؛ BollingerV1 نمونه واقعی mean reversion است، نه مدرک سود آن [H05] |
| sizing/risk | PositionExecutor amount صریح، balance و trading rules را بررسی می‌کند؛ portfolio exposure باید بالاتر enforce شود |
| خروج | TripleBarrierConfig شامل stop_loss، take_profit، time_limit و trailing_stop است؛ time limit جلوگیری از ماندن بی‌پایان سرمایه است [H01] |
| lifecycle | InFlightOrder و رویداد created/filled/completed/canceled/failed؛ مقدار واقعی باز و بسته باید تطبیق داده شود [H02][H04] |
| نکته کد ۱ | در `control_barriers`، SL/trailing/TP به شرط open-order fully filled و min size اجرا می‌شوند؛ partial fill فوری باید در طراحی خودمان مستقل محافظت شود [H02] |
| نکته کد ۲ | `place_close_order_and_cancel_open_orders` از MARKET استفاده می‌کند؛ برای نمادهای Market-disabled والکس قابل کپی نیست [H02][P02] |
| drawdown | executor تک‌موقعیت جای kill switch کل حساب نیست؛ آن را مستقل بسازید |
| backtest | PositionExecutorSimulator روی close و هزینه ثابت کار می‌کند؛ SL از high/low هم استفاده می‌کند. queue، latency و book واقعی مدل کامل نشده‌اند [H03] |
| paper/live/data | paper connector و live connectors، candle/orderbook/trade models؛ Wallex در tree نام adapter ندارد |
| AI | core executor deterministic است؛ agent harness در Condor جداست |
| tests/reproducibility | تست PositionExecutor و in-flight order موجود؛ pin نسخه و fixtures Wallex لازم است |
| License/integration | Apache-2.0؛ نگهداری LICENSE/NOTICE و اعلام تغییرات. آداپت محدود ایده خروج ارزان‌تر از ساخت connector و candles feed کامل است |

پارامترهای پیش‌فرض (طول Bollinger، آستانه و فرکانس) به Wallex منتقل نمی‌شوند. داشتن هر سه SL/TP/time در یک config به‌تنهایی برتری استراتژی را ثابت نمی‌کند.

## ۵. Condor — بهترین مرجع جداسازی AI و اجرا

**حکم: Reimplement concept؛ full deployment برای این هکاتون رد.** [R05]

| محور | یافته و تصمیم |
|---|---|
| هدف/معماری | harness برای agentهای معاملاتی؛ reasoning در Condor و اجرای deterministic از مسیر Hummingbot API |
| strategy/data | tick observation → decision → ابزار؛ snapshot و journal؛ وابستگی به API و مدل‌های executor/position خودش [C02][C03] |
| risk/sizing | `RiskLimits` سقف quote exposure و تعداد executor؛ کنترل هر tool mutation. تست تجمع exposure در همان tick نشان می‌دهد دو درخواست نباید یک موجودی را دوبار مصرف کنند [C01][C04] |
| نکته کد | max_drawdown_pct، shutdown_drawdown_pct، max_drift_quote و max_leverage در defaults مقدار -1 و غیرفعال دارند؛ defaultها تنظیم امن حساب ما محسوب نمی‌شوند [C01] |
| SL/TP/trailing/time | عمدتاً به executor لایه Hummingbot تفویض می‌شود؛ خود LLM نباید مسئول زمان‌بندی stop باشد |
| drawdown | soft pause و hard shutdown از هم جدا هستند؛ ایده مناسبی برای رفتار «ورود ممنوع، خروج مجاز» [C01] |
| lifecycle/live | ابزارهای مجاز و ownership/session scope؛ raw place_order در risk callback بررسی‌شده رد می‌شود، نه دسترسی نامحدود AI [C01] |
| backtest/paper/shadow | orchestration بک‌تست و dry-run guard دارد؛ dry-run در همین کد mutation را منع می‌کند. این مشاهده محدود تضمین کل stack نیست |
| AI | LLM بر انتخاب اثر واقعی می‌گذارد؛ journal/snapshot برای اثبات چرایی مفید است، ولی ذخیره پاسخ معادل تضمین تکرار inference نیست |
| testing/reproducibility | risk-gate، drawdown و journal tests در tree؛ CI اجرا نشده؛ کد مهار ابزار از README مهم‌تر است |
| License/integration | MIT؛ اما زیرساخت execution مجوزها و dependencies خودش را دارد. راه‌اندازی Hummingbot API، سرویس‌ها و connector Wallex هزینه اضافی دارد |

یک agent با tool محدود **انتخاب signal_id یا WAIT** برای ما کافی‌تر از fleet چندagent و Telegram/dashboard است. از توسعه خودکار strategy حین live و raw code execution استفاده نشود.

## ۶. FinRL-Trading / FinRL-X — معماری ماژولار ولی افق نامناسب

**حکم: Reference only؛ strategy و execution موجود برای این پروژه Reject.** [R06]

| محور | یافته و تصمیم |
|---|---|
| هدف/معماری | لایه‌های data → selection/allocation/timing/risk → portfolio weights → bt backtest → Alpaca execution |
| strategy/data | سهام، fundamentals، چرخش هفتگی دارایی و نمونه‌های تخصیص؛ نیازمند data/calendar/broker ناسازگار با Crypto دقیقه‌ای |
| sizing | weight-centric؛ هدف وزن پرتفوی به order تبدیل می‌شود، با مقدار سفارش spot و موجودی locked ما فرق دارد |
| stop/trailing/time | risk_manager stop مطلق، peak/trailing و cooldown دارد؛ cooldown برحسب **هفته** است. TP/time intraday قرارداد آماده مناسب ما تأیید نشد [T02] |
| drawdown/risk | pretrade و portfolio overlay مطرح‌اند؛ نیازمند طراحی مستقل equity حساب چالش |
| lifecycle/live | TradeExecutor صریحاً Alpaca را می‌گیرد؛ market-hours/TIF/OPG و rebalance دارد؛ جایگزین آداپتر Wallex نیست [T03] |
| backtest | BacktestConfig پیش‌فرض `rebalance_freq='Q'`، integer_positions و benchmark SPY/QQQ؛ bt با commission/cost model دارد، نه replay book Wallex [T01] |
| paper/shadow | Alpaca paper و dry-run plan با Shadow والکس یکی نیست |
| AI | ML، DRL و LLM-ready در معماری/نمونه‌ها؛ عنوان AI-native اثبات اجرای LLM مؤثر در هر مسیر نیست |
| testing/reproducibility | walk_forward.py و no-lookahead هدف طراحی است؛ در tree این snapshot پوشه تست مستقل رایج مشاهده نشد؛ ادعای کیفیت را با README قطعی نکنید |
| License/integration | Apache-2.0؛ ترجمه calendar، universe، broker و مدل هزینه بخش عمده پروژه را عوض می‌کند |

ایده **لایه ریسک جدا از سیگنال و تبدیل تصمیم به intent استاندارد** را می‌گیریم؛ backtest سال‌های سهام را شاهد سود سه‌روزه والکس نمی‌دانیم.

## ۷. FinRL — آموزش RL بدون شبیه‌ساز execution مناسب

**حکم: Reference only برای شناخت RL؛ dependency و training فعلی Reject.** [R07]

| محور | یافته و تصمیم |
|---|---|
| هدف/نگهداری | پژوهش/آموزش RL؛ README فعلی این نقش را حفظ و برای معماری جدید به FinRL-Trading ارجاع می‌دهد؛ به معنی abandoned بودن نیست |
| معماری/strategy | محیط، agent RL و application؛ state/action/reward نیاز به طراحی و dataset دارد |
| data/sizing | CryptoEnv از price_array و tech_array استفاده می‌کند؛ action به مقدار خرید/فروش مقیاس می‌شود و cash محدود می‌کند [L01] |
| stop/TP/trailing/time | در CryptoEnv بررسی‌شده این‌ها سیاست مستقل اجرایی نیستند؛ پایان episode همان stop موقعیت نیست |
| drawdown/risk | محدودیت cash/holding و هزینه؛ reward یا turbulence سایر محیط‌ها جای risk gate زنده نیست |
| lifecycle | در step، معامله با قیمت یک گام و درصد هزینه ثابت شبیه‌سازی می‌شود؛ order ack/cancel/partial fill/latency/book وجود ندارد [L01] |
| backtest | ارزیابی policy در محیط تاریخی؛ gap شبیه‌سازی به واقعیت برای Wallex بزرگ است |
| paper/live | نمونه broker paper در پروژه وجود دارد، نه adapter Wallex آماده |
| AI | RL واقعی است ولی نیاز به چند seed، reward design، آموزش و OOS دارد؛ embedding نام RL ارزشی اضافه نمی‌کند |
| tests/reproducibility | unit environment tests در tree؛ seed/config/data split و model artifact لازم؛ خود این تست‌ها edge اقتصادی را ثابت نمی‌کنند |
| License/integration | MIT؛ مانع اصلی زمان و فرض fill است، نه مجوز |

آموزش policy روی چند روز داده یا انتقال policy سهام/صرافی دیگر را رد می‌کنیم. برای بهتر شدن RL ابتدا باید شبیه‌ساز را معتبر کرد؛ این هزینه از ساخت دو strategy ساده بیشتر است.

## ۸. vectorbt — ابزار سریع تحقیق با نکته مجوز مهم

**حکم: Reference only؛ Direct dependency تحقیق فقط پس از تصمیم آگاهانه مجوز.** [R08]

| محور | یافته و تصمیم |
|---|---|
| هدف/معماری | محاسبات آرایه‌ای pandas/NumPy با Numba و engine اختیاری Rust؛ sweep بسیار سریع ایده‌ها |
| strategy/data | from_signals/from_orders/from_order_func و آرایه OHLCV؛ framework اتصال live نیست [V01][V02] |
| sizing | مقدار/value/percent، cash sharing، محدودیت اندازه و partial simulation؛ ترتیب رقابت بر سر سرمایه باید مشخص باشد |
| SL/TP/trailing/time | SL/TP/trailing در signals، time exit قابل تعریف؛ هم‌زمانی stop و signal همان bar قرارداد مشخص می‌خواهد |
| drawdown | محاسبه drawdown/returns؛ الزاماً circuit breaker اجرایی مستقل نیست |
| lifecycle/backtest | هزینه و slippage قابل تنظیم؛ مدل آرایه‌ای، صف و تأخیر Wallex را خودبه‌خود بازنمایی نمی‌کند |
| paper/live/AI | هسته research است؛ Paper/Live و LLM تصمیم‌گیر نیاز به سیستم بیرونی دارند؛ ابزارهای agent-compatible به معنی strategy AI نیست |
| tests/reproducibility | test_portfolio و order simulations؛ ترتیب ستون‌ها/cash و داده/seed باید pin شوند. Float در nb.py محدودیت rounding اعلام می‌کند |
| License | نسخه فعلی **Apache-2.0 + Commons Clause** است؛ آن را Apache خالص یا OSI-open-source بدون قید معرفی نکنید. شرط منع Sell شامل برخی سرویس‌های ارزش‌گرفته از نرم‌افزار است؛ اثر دقیق هکاتون/تجاری‌سازی نیاز تصمیم مجوز دارد |
| integration | وارد کردن OHLCV ساده، ولی research نتیجه مدل fill است؛ optimizer وسیع ریسک انتخاب بهترین نویز را بیشتر می‌کند |

سرعت در sweep هزاران پارامتر، مزیت لازم این پروژه نیست. دو فرضیه و چند تنظیم ازپیش‌تعیین‌شده را می‌توان با ابزار سبک بررسی کرد؛ مجوز و پرهیز از overfitting اولویت بیشتری دارند.

## دو منبع مکمل و نقطه توقف جست‌وجو

| Repo | چرا اضافه شد؟ | قابلیت‌ها/محدودیت‌ها | مجوز و تصمیم |
|---|---|---|---|
| kernc/backtesting.py | مقایسه گزینه research ساده‌تر | Strategy.next و OHLCV؛ sizing/SL/TP، trailing helper و خروج دستی در strategy؛ bar broker و commission؛ time limit باید تعریف شود؛ live/reconciliation/Wallex/LLM و drawdown kill اجرایی آماده نیست. تست‌ها در tree؛ data/config برای replay لازم [K01] | AGPL-3.0؛ Reference only، dependency پیش‌فرض رد؛ سادگی فنی الزامات مجوز را حذف نمی‌کند [R09] |
| ccxt/ccxt | آزمودن فرض «Freqtrade آماده به Wallex وصل می‌شود» | هدف unified exchange REST/WS، داده/سفارش/precision؛ framework strategy، backtest، sizing، risk policy و AI نیست. tree snapshot هیچ فایل wallex ندارد؛ این جست‌وجو اثبات نبودن همه راه‌حل‌های شخص ثالث نیست | MIT؛ Reference only، dependency فقط برای connector موجود و آزموده؛ ساخت یک shim کامل برای همین هکاتون انتخاب اول نیست [R10] |

پس از این دو مکمل، شکاف‌های اصلی تکرار شدند: Wallex adapter، داده معتبر، fill model و ریسک مستقل. مخزن بیشتر مسئله تصمیم را حل نمی‌کرد؛ جست‌وجوی Repo متوقف شد. wrapperهای ناشناس Wallex یا MCP غیررسمی جای قرارداد رسمی و بررسی scope را نگرفتند. [X01]

## تصمیم integration برای Agent بعدی

۱. موتور سبک Wallex با interfaceهای DataFeed، SignalCandidate، AgentDecision، RiskVerdict و ExecutionEvent؛ این‌ها **طرح پیشنهادی** هستند، کد آماده تولید نیستند.
۲. از Freqtrade قواعد رفتاری حفاظت و آزمایش leakage؛ از Hummingbot سه شرط خروج؛ از Nautilus reconciliation و reservation؛ از Condor مهار ابزار و journal.
۳. فرمول و state machine را بر نیاز خود بنویسید؛ در صورت انتقال واقعی کد، مجوز همان فایل، NOTICE و dependencyها را ثبت کنید.
۴. برآورد کیفی: data import برای research کم‌پیچیدگی‌تر است؛ full live integration همه چارچوب‌های بررسی‌شده زیاد است چون adapter رسمی Wallex در snapshot تأیید نشد. برآورد ساعت قطعی بدون تیم/زیرساخت موجود UNKNOWN است.
۵. dependency نهایی باید بعد از spike اتصال، نسخه stable/pin و آزمون واحدهای حساس انتخاب شود. هیچ پروژه‌ای صرفاً براساس شهرت یا نمودار سود README انتخاب نشده است.

## ارجاع‌ها

[C01]: https://github.com/hummingbot/condor/blob/8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c/condor/agents/risk.py "Agent risk gate؛ دسترسی 2026-10-22"
[C02]: https://github.com/hummingbot/condor/blob/8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c/condor/agents/engine.py "Agent engine؛ دسترسی 2026-10-22"
[C03]: https://github.com/hummingbot/condor/blob/8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c/condor/agents/journal.py "JournalManager؛ دسترسی 2026-10-22"
[C04]: https://github.com/hummingbot/condor/blob/8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c/tests/test_risk_gate.py "Risk gate tests؛ دسترسی 2026-10-22"
[F01]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/freqtrade/plugins/protections/max_drawdown_protection.py "Drawdown protection؛ دسترسی 2026-10-22"
[F02]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/freqtrade/plugins/protections/stoploss_guard.py "Stoploss guard؛ دسترسی 2026-10-22"
[F03]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/docs/backtesting.md "Backtesting assumptions؛ دسترسی 2026-10-22"
[F04]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/docs/lookahead-analysis.md "Lookahead analysis؛ دسترسی 2026-10-22"
[F05]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/docs/strategy-callbacks.md "Sizing/exit callbacks؛ دسترسی 2026-10-22"
[F06]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/docs/freqai.md "FreqAI؛ دسترسی 2026-10-22"
[F07]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/tests/plugins/test_protections.py "Protection tests؛ دسترسی 2026-10-22"
[H01]: https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/hummingbot/strategy_v2/executors/position_executor/data_types.py "TripleBarrierConfig؛ دسترسی 2026-10-22"
[H02]: https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/hummingbot/strategy_v2/executors/position_executor/position_executor.py "PositionExecutor؛ دسترسی 2026-10-22"
[H03]: https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/hummingbot/strategy_v2/backtesting/executors_simulator/position_executor_simulator.py "PositionExecutorSimulator؛ دسترسی 2026-10-22"
[H04]: https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/hummingbot/core/data_type/in_flight_order.py "InFlightOrder؛ دسترسی 2026-10-22"
[H05]: https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/controllers/directional_trading/bollinger_v1.py "Bollinger controller؛ دسترسی 2026-10-22"
[J01]: https://github.com/jesse-ai/jesse/blob/4fcbcdd500b9cd14ca9bdb142e4f46a34884a1ae/jesse/strategies/Strategy.py "Strategy hooks و ML features؛ دسترسی 2026-10-22"
[J02]: https://github.com/jesse-ai/jesse/blob/4fcbcdd500b9cd14ca9bdb142e4f46a34884a1ae/jesse/utils.py "Sizing helpers؛ دسترسی 2026-10-22"
[K01]: https://github.com/kernc/backtesting.py/blob/ca2e2611621e472542ba90f7243a1fa06a7d7108/backtesting/backtesting.py "Bar broker simulator؛ دسترسی 2026-10-22"
[L01]: https://github.com/AI4Finance-Foundation/FinRL/blob/c0018c25de864157dc8470f0047b0d10ec721715/finrl/meta/env_cryptocurrency_trading/env_multiple_crypto.py "CryptoEnv؛ دسترسی 2026-10-22"
[N01]: https://github.com/nautechsystems/nautilus_trader/blob/9156e8f2d7126236269b6fcd2e6402b7a8e4e6d8/crates/risk/src/engine/mod.rs "RiskEngine؛ دسترسی 2026-10-22"
[N02]: https://github.com/nautechsystems/nautilus_trader/blob/9156e8f2d7126236269b6fcd2e6402b7a8e4e6d8/crates/risk/src/sizing.rs "Fixed-risk sizing؛ دسترسی 2026-10-22"
[N03]: https://github.com/nautechsystems/nautilus_trader/blob/9156e8f2d7126236269b6fcd2e6402b7a8e4e6d8/docs/concepts/execution/reconciliation.md "Reconciliation؛ دسترسی 2026-10-22"
[N04]: https://github.com/nautechsystems/nautilus_trader/blob/9156e8f2d7126236269b6fcd2e6402b7a8e4e6d8/docs/concepts/backtesting/fill-models.md "Fill models؛ دسترسی 2026-10-22"
[N05]: https://github.com/nautechsystems/nautilus_trader/blob/9156e8f2d7126236269b6fcd2e6402b7a8e4e6d8/docs/concepts/backtesting/bar-execution.md "Bar execution؛ دسترسی 2026-10-22"
[P02]: https://api.wallex.ir/hector/web/v1/markets "مارکت‌های اسپات: پاسخ عمومی واقعی؛ دسترسی 2026-10-22"
[R01]: https://github.com/jesse-ai/jesse/tree/4fcbcdd500b9cd14ca9bdb142e4f46a34884a1ae "jesse-ai/jesse؛ دسترسی 2026-10-22"
[R02]: https://github.com/freqtrade/freqtrade/tree/c051e9065c5b677f03817dd1f83cb980af1ed684 "freqtrade/freqtrade؛ دسترسی 2026-10-22"
[R03]: https://github.com/nautechsystems/nautilus_trader/tree/9156e8f2d7126236269b6fcd2e6402b7a8e4e6d8 "nautechsystems/nautilus_trader؛ دسترسی 2026-10-22"
[R04]: https://github.com/hummingbot/hummingbot/tree/9af100d6822da7d2d0291a906c730ef172284ee2 "hummingbot/hummingbot؛ دسترسی 2026-10-22"
[R05]: https://github.com/hummingbot/condor/tree/8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c "hummingbot/condor؛ دسترسی 2026-10-22"
[R06]: https://github.com/AI4Finance-Foundation/FinRL-Trading/tree/4409abe925c904e570be78ebfb5e77ac3491dff8 "AI4Finance-Foundation/FinRL-Trading؛ دسترسی 2026-10-22"
[R07]: https://github.com/AI4Finance-Foundation/FinRL/tree/c0018c25de864157dc8470f0047b0d10ec721715 "AI4Finance-Foundation/FinRL؛ دسترسی 2026-10-22"
[R08]: https://github.com/polakowo/vectorbt/tree/dd8eb3a3fe9ed918b1cdaf177455759ae4ac68cb "polakowo/vectorbt؛ دسترسی 2026-10-22"
[R09]: https://github.com/kernc/backtesting.py/tree/ca2e2611621e472542ba90f7243a1fa06a7d7108 "kernc/backtesting.py؛ دسترسی 2026-10-22"
[R10]: https://github.com/ccxt/ccxt/tree/92c1eb27f4652735258468adb4e53beb0c6b3cd0 "ccxt/ccxt؛ دسترسی 2026-10-22"
[T01]: https://github.com/AI4Finance-Foundation/FinRL-Trading/blob/4409abe925c904e570be78ebfb5e77ac3491dff8/src/backtest/backtest_engine.py "bt portfolio engine؛ دسترسی 2026-10-22"
[T02]: https://github.com/AI4Finance-Foundation/FinRL-Trading/blob/4409abe925c904e570be78ebfb5e77ac3491dff8/src/strategies/adaptive_rotation/risk_manager.py "Rotation risk manager؛ دسترسی 2026-10-22"
[T03]: https://github.com/AI4Finance-Foundation/FinRL-Trading/blob/4409abe925c904e570be78ebfb5e77ac3491dff8/src/trading/trade_executor.py "Alpaca executor؛ دسترسی 2026-10-22"
[V01]: https://github.com/polakowo/vectorbt/blob/dd8eb3a3fe9ed918b1cdaf177455759ae4ac68cb/vectorbt/portfolio/base.py "Portfolio؛ دسترسی 2026-10-22"
[V02]: https://github.com/polakowo/vectorbt/blob/dd8eb3a3fe9ed918b1cdaf177455759ae4ac68cb/vectorbt/portfolio/nb.py "Numba order simulation؛ دسترسی 2026-10-22"
[X01]: https://github.com/mhsenpc/wallex_mcp_server "mhsenpc/wallex_mcp_server؛ دسترسی 2026-10-22"

[M01]: https://api.github.com/repos/jesse-ai/jesse/commits?per_page=5 "نمونه فعالیت jesse-ai/jesse؛ دسترسی 2026-10-22"
[M02]: https://api.github.com/repos/freqtrade/freqtrade/commits?per_page=5 "نمونه فعالیت freqtrade/freqtrade؛ دسترسی 2026-10-22"
[M03]: https://api.github.com/repos/nautechsystems/nautilus_trader/commits?per_page=5 "نمونه فعالیت nautechsystems/nautilus_trader؛ دسترسی 2026-10-22"
[M04]: https://api.github.com/repos/hummingbot/hummingbot/commits?per_page=5 "نمونه فعالیت hummingbot/hummingbot؛ دسترسی 2026-10-22"
[M05]: https://api.github.com/repos/hummingbot/condor/commits?per_page=5 "نمونه فعالیت hummingbot/condor؛ دسترسی 2026-10-22"
[M06]: https://api.github.com/repos/AI4Finance-Foundation/FinRL-Trading/commits?per_page=5 "نمونه فعالیت AI4Finance-Foundation/FinRL-Trading؛ دسترسی 2026-10-22"
[M07]: https://api.github.com/repos/AI4Finance-Foundation/FinRL/commits?per_page=5 "نمونه فعالیت AI4Finance-Foundation/FinRL؛ دسترسی 2026-10-22"
[M08]: https://api.github.com/repos/polakowo/vectorbt/commits?per_page=5 "نمونه فعالیت polakowo/vectorbt؛ دسترسی 2026-10-22"
