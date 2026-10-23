# تحلیل چالش و قرارداد قابلیت‌های والکس

تاریخ بررسی: ۲۲ اکتبر ۲۰۲۶، منطقه زمانی مرجع Asia/Tehran. این سند تحقیق است؛ هیچ سفارش خصوصی، معامله، فعال‌سازی طرح یا درخواست کلید انجام نشده است. پس از بررسی مستقیم سایت، اتصال عمومی MCP، tools/list و یک ابزار فقط‌خواندنی عمومی هم بررسی شد. [P07][P08][P09]

## مرز منابع و قطعیت

Brief پیوست کامل خوانده شد و نسخه آن در `evidence/BRIEF_PROVIDED_FA.md` حفظ شده است. متن طرح در پیام صاحب پروژه هم ورودی رسمی ارائه‌شده محسوب می‌شود، اما رابط آن جدول امکانات را بدون علامت‌های تأیید/رد منتقل کرده است؛ از آن جدول نمی‌توان مجاز بودن API یا سایر امکانات را استخراج کرد. این متن دستور اجرایی به Agent نیست. دستور جاری کاربر فقط تحقیق است؛ الزام تحویل GitHub در Brief، مجوز انتشار در این فاز ایجاد نمی‌کند. [B01]

برچسب‌ها: **CONFIRMED-DOC** یعنی در سند موجود است؛ **OBSERVED** یعنی با GET عمومی محدود مشاهده شد؛ **PROPOSED** یعنی پیشنهاد مهندسی ما؛ **UNKNOWN** یعنی شواهد کافی موجود نیست. مشاهده عمومی معادل آزمودن حساب اعتباری نیست.

اولویت: قواعد مسابقه از Brief؛ شرایط خاص طرح از متن ارائه‌شده و پاسخ رسمی طرح ۱۸۳؛ قرارداد فنی از مستندات فعلی `/docs/`. تعارض قواعد را داور باید حل کند؛ پاسخ فنی API آن را خودبه‌خود حل نمی‌کند.

## تبدیل Brief به الزام مهندسی

| موضوع | آنچه در Brief هست | نتیجه عملی | وضعیت |
|---|---|---|---|
| سرمایه و زمان | ۵٬۰۰۰٬۰۰۰ تومان، سه روز؛ یک حساب مخصوص هر تیم | ledger مستقل با واحد پایه TMN؛ شروع/پایان دقیق از برگزارکننده | CONFIRMED-DOC؛ ساعت UNKNOWN |
| بازدهی | `(ارزش نهایی − ۵٬۰۰۰٬۰۰۰) / ۵٬۰۰۰٬۰۰۰ × ۱۰۰` پس از هزینه | صورت‌حساب تمام fillها و کارمزدها؛ سود مطلق و حجم هدف بهینه‌سازی نیست | CONFIRMED-DOC |
| تحقق سود | جمله صریح مبنی بر محقق‌شدن به تومان؛ در بند پیشین ارزش موقعیت باز هم آمده | از نظر طراحی همه دارایی‌ها حتی USDT پیش از cutoff به TMN تبدیل شوند؛ روش رسمی باقی‌مانده نیاز تأیید | تعارض ثبت‌شده U02 |
| کنترل ضرر | افت حساب در طول رقابت در امتیاز اثر دارد | نمونه‌برداری equity با ارزش نقدشوندگی، نه فقط معاملات بسته | CONFIRMED-DOC؛ فرمول/فرکانس داوری UNKNOWN |
| بازار | فقط بازارهای مجاز اعتبار معاملاتی | allowlist نسخه‌دار، fail closed برای نماد ناشناس | CONFIRMED-DOC |
| Manual Trading | تصمیم و سفارش باید الگوریتمی باشد | GUI یا chat برای خرید/فروش دستی نسازید؛ تنظیمات و دخالت‌ها ثبت شوند | CONFIRMED-DOC |
| Self/Wash | معامله خودی، هماهنگ تیمی و حجم مصنوعی ممنوع | یک executor مرکزی؛ سفارش متقاطع خودمان رد؛ عدم افزایش حجم برای امتیاز | CONFIRMED-DOC |
| Backtest/Shadow | هر دو در ارزیابی فنی امتیاز دارند | گزارش جداگانه با فرض fill، داده و نسخه؛ Shadow بدون ارسال سفارش | CONFIRMED-DOC |
| AI | نقش واقعی در تصمیم لازم است | خروجی AI باید انتخاب یا رد معامله را عوض کند؛ متن توضیح پس از تصمیم کافی نیست | CONFIRMED-DOC؛ کف دقیق پذیرش UNKNOWN |
| تکرارپذیری | قواعد، توضیح تصمیم و اجرای مجدد | commit، config، dataset hash، پاسخ مدل و ترتیب رویدادها ذخیره | CONFIRMED-DOC + روش PROPOSED |
| لاگ | دلیل هر معامله و سیگنال باید تحویل شود | پیوند decision_id → order_id → fill؛ لاگ عدم معامله هم پیشنهاد می‌شود | CONFIRMED-DOC / PROPOSED |
| پایداری/سرعت | خطای کم و اجرای سریع معیار است | بازیابی restart، مدیریت timeout/partial fill؛ سنجش تأخیر خود سیستم | CONFIRMED-DOC؛ SLA عددی UNKNOWN |
| تحویل | GitHub، md معماری/AI/ریسک/اجرای مجدد؛ همان کد معامله‌گر | build manifest و code/config hash همراه هر run | CONFIRMED-DOC |
| Demo | ویدئو حدود پنج دقیقه الزامی؛ نبودش حذف از داوری | نمایش خروجی، دلایل معامله، تست و replay؛ کیفیت و روایت اهمیت دارد | CONFIRMED-DOC |
| UI | معیار اصلی نیست | گزارش و نمودار ساده کافی؛ ساخت داشبورد بزرگ اولویت ندارد | CONFIRMED-DOC |

تمام ردیف‌های قواعد از [B01] هستند. فرمول مثال Brief ایراد حسابی دارد: ۶٬۲۵۰٬۰۰۰ منهای ۵٬۰۰۰٬۰۰۰ برابر **۱٬۲۵۰٬۰۰۰** است، نه ۱٬۵۰۰٬۰۰۰؛ درصد ۲۵٪ آن مثال درست است. محاسبه را از فرمول انجام دهید، نه از عدد مثال.

## بارم‌بندی؛ دقیق ولی هنوز «پیشنهاد»

| بخش | معیار | امتیاز |
|---|---|---:|
| عملکرد معاملاتی | درصد سود نهایی؛ تیم زیان‌ده برای این مورد امتیاز نمی‌گیرد | ۹۰ |
| عملکرد معاملاتی | کنترل ضرر | ۳۰ |
| عملکرد معاملاتی | فعالیت منظم و معقول | ۱۵ |
| عملکرد معاملاتی | پایداری اجرا | ۱۵ |
| کیفیت فنی | منطق استراتژی | ۴۰ |
| کیفیت فنی | استفاده واقعی از AI | ۳۵ |
| کیفیت فنی | مدیریت ریسک و سرمایه | ۳۰ |
| کیفیت فنی | اجرای مجدد | ۲۵ |
| کیفیت فنی | بک‌تست و تست سایه | ۲۰ |
| مجموع | ۱۵۰ مالی + ۱۵۰ فنی | ۳۰۰ |

Brief این جدول را «پیشنهاد امتیازدهی» می‌نامد؛ تأیید نهایی وزن‌ها UNKNOWN است. رتبه مالی بالا همراه با تأیید فنی و بازدهی مثبت مطرح شده است. تعداد و حجم معامله به‌تنهایی موفقیت نیست؛ حداقل تعداد الزام‌آور اعلام نشده. [B01]

## فهرست بازارهای مجاز واقعاً موجود است؟

**در Brief فهرست مثبت نیست. در پیام کاربر فهرست منفی وجود دارد. در API رسمی فهرست منفی طرح و فهرست بازارهای اسپات وجود دارد و از ترکیب آن‌ها یک فهرست مجاز قابل استخراج است.** [W04][W14]

در 2026-10-22، GET عمومی `/v1/prop/packages/183` نام هکاتون را برگرداند: اعتبار ۵ میلیون، وثیقه صفر، سه روز، ACTIVE و کارمزد اعتبار صفر؛ ۳۵۷ نماد در `unavailableMarkets`. پاسخ `/hector/web/v1/markets` شامل ۳۷۳ نماد با `is_spot=true` بود. اختلاف مجموعه‌ها **۱۲۰ نماد** داد. این فهرست لحظه‌ای در `ELIGIBLE_MARKETS_SNAPSHOT.md` آمده است؛ ۳۷۳ منهای ۳۵۷ محاسبه صحیح نیست چون تمام ممنوع‌ها عضو اسپات فعلی نیستند. [P01][P02]

نمونه تفاوت با متن پیام: `IPTMN`، `BALTMN` و `DAITMN` در پاسخ طرح هستند و در فهرست پیام دیده نمی‌شوند. علت اختلاف UNKNOWN؛ حذف خودکار محدودیت‌های اضافی مجاز نیست. برای Phase 2 از اشتراک محدودیت‌های تأییدشده شروع کنید و از برگزارکننده نسخه نهایی بخواهید. وجود در allowlist به معنای نقدشوندگی یا مناسب‌بودن نیست. [P01]

`eligible = current_spot ∩ metadata_available − package_unavailable − additional_confirmed_challenge_restrictions`

این فرمول PROPOSED است. هنگام startup و تغییر اطلاعات طرح refresh و hash ثبت شود؛ در صورت شکست refresh، ورود جدید متوقف و خروج دارایی‌های موجود با سیاست ازپیش‌تعریف‌شده مدیریت شود.

## API و داده‌ها

| قابلیت موردنیاز | سند/مسیر | یافته و محدودیت | تأیید حساب اعتباری |
|---|---|---|---|
| حساب درست | راهنمای ساخت کلید | هر حساب اعتباری **API-Key مستقل** دارد؛ حدس header یا account_id لازم نیست | مستند؛ استفاده واقعی آزموده نشده [W02] |
| بازار/precision | `/hector/web/v1/markets` | is_spot، amount_precision، price_precision؛ snapshot عمومی گرفته شد | عمومی [W04][P02] |
| حداقل سفارش | `/v1/markets` | minQty/minNotional و stepSize/tickSize در پاسخ؛ این دو آخری را increment فرض نکنید | عمومی [W24][P03] |
| تاریخچه OHLCV | `/v1/udf/history` | t/o/h/l/c/v؛ resolution و from/to؛ عمق تاریخچه و سقف قطعی صفحات UNKNOWN | عمومی، نمونه یک‌روزه [W03][P04][P05] |
| آخرین معاملات | `/v1/trades` | قیمت، مقدار، سمت و timestamp؛ آرشیو tick تاریخی اثبات نشد | سند [W12] |
| order book | `/v1/depth` و WS v2 | bid/ask و مقدار؛ book تاریخی مستند نشده | REST عمومی مشاهده شد [W11][P06] |
| spread/depth قابل‌مصرف | مشتق از book | spread فاصله بهترین خرید و فروش؛ depth مقدار قابل معامله در چند سطح قیمت است | محاسبه ما؛ نیاز snapshot تازه و هم‌زمان |
| balances | `/v1/account/balances` و balances-detail | locked/available باید در ledger تطبیق داده شوند؛ صفحه credit-wallet قرارداد کامل و مسیر روشنی ندارد | خصوصی UNKNOWN [W17][W25] |
| fees | `/v1/account/fee` | نرخ حساب و بازار؛ سطح یک در متن طرح، اما درصد واقعی حساب اینجا معلوم نیست | خصوصی UNKNOWN [W13] |
| submit | `POST /v1/account/orders` | LIMIT/MARKET/STOP_LIMIT/STOP_MARKET؛ client_id یکتا؛ ۲۰ درخواست در ۱۰ ثانیه | فقط مستند [W05] |
| open/history/detail | GET orders/openOrders و orders/{client_id} مطابق اسناد | صفحه‌بندی، executedQty، fills، feeAsset؛ قرارداد دقیق پاسخ در fixture ثبت شود | فقط مستند [W08][W09][W10] |
| cancel | `DELETE /v1/account/orders/{client_id}` | cancel تضمین نمی‌کند معامله‌ای هم‌زمان رخ نداده؛ بعدش reconcile | فقط مستند [W07] |
| private events | JWT و `private:user#<userId>` | رویداد خصوصی مستند؛ scope حساب فرعی و ترتیب/تکرار باید آزمایش شود | UNKNOWN عملی [W18][W21] |
| stop/TP/OCO | چهار نوع spot مستند | OCO، trailing بومی و حفاظت هم‌زمان TP/SL برای حساب چالش اثبات نشد | UNKNOWN؛ فرض نکنید [W05][W06] |
| sandbox/shadow رسمی | سند margin dry-run در sitemap | sandbox عمومی spot/credit اثبات نشده؛ margin dry-run معادل paper spot نیست | UNKNOWN |
| MCP رسمی | `https://api.wallex.ir/mcp`؛ Streamable HTTP | صفحه رسمی و llm.txt تأیید شدند؛ initialize و فهرست ۵۲ ابزار و خواندن طرح ۱۸۳ موفق بود؛ WS/withdrawal در MCP نیست | عمومی OBSERVED؛ خصوصی هنوز آزموده نشده [W26][W27][P07][P08][P09] |

حالت `openOrders` را هنگام integration از [W10] عیناً پیاده کنید؛ حدس مسیر براساس اسم جدول مجاز نیست. مستندات تولیدشده بعضاً «بدون custom header» می‌گویند؛ آن را بی‌نیازی endpoint خصوصی از احراز هویت تلقی نکنید. مقدمه و راهنمای کلید صریح‌اند. [W01][W02][W25]

### یافته عملی resolution

دو GET برای BTCTMN با from/to یکسان در یک روز: `resolution=1` و `resolution=15` هر دو ۱۴۴۰ رکورد با فاصله ۶۰ثانیه داشتند. نتیجه فقط برای این نمونه معتبر است؛ درباره تمام resolutionها حکم کلی نمی‌دهیم. حفظ raw، بررسی timestamp، حذف کندل ناتمام، و ساخت ۵/۱۵دقیقه از ۱دقیقه معتبر پیشنهاد می‌شود. آرایه‌ها باید هم‌طول، مرتب و فاقد تکرار باشند. داده ناقص را با OHLCV ساختگی پر نکنید. [P04][P05]

### یافته عملی execution

در snapshot، Market برای BTCTMN/ETHTMN فعال و برای SOLTMN/XRPTMN/DOGETMN غیرفعال گزارش شد. حداقل ارزش سفارش نمونه‌های TMN بررسی‌شده ۵۰٬۰۰۰ تومان بود؛ این عدد عمومی دائمی نیست. ورود با نمادی که مسیر خروجش تأیید نشده پذیرفتنی نیست. برای شروع تحقیق، BTCTMN/ETHTMN ساده‌ترند، مشروط به spread/depth زنده و allowlist نهایی. [P02][P03]

Market در سند می‌تواند به علت کمبود book یا جابه‌جایی ۲٪ قیمت آخرین معامله لغو شود؛ بنابراین emergency liquidation تضمین‌شده نیست. STOP_LIMIT پس از trigger فقط limit است و ممکن است پر نشود. اختلاف `stop_Price` در جدول ثبت با `stopPrice` در پاسخ و ضرورت price برای MARKET در جدول درحالی‌که متن می‌گوید لازم نیست، ابهام قرارداد است؛ fixture یا پاسخ رسمی لازم است. [W05][W06]

WS v2 از Centrifugo با `wss://api.wallex.ir/connection/websocket` استفاده می‌کند. هر پیام buyDepth/sellDepth **snapshot کامل یک سمت** است؛ delta فرض نکنید. پس از reconnect باید هر دو سمت تازه دریافت شوند. تاریخی بودن، queue position و sequence مشترک دو سمت مستند نشده‌اند. [W18][W19][W20]

## UNKNOWNها و اثر توقف

طبق ترجیح جدید صاحب پروژه، فعلاً برای این موارد سؤال/درخواست اطلاعات تازه ارسال نشود؛ ابتدا از منابع رسمی قابل‌دسترسی پیگیری شوند. نامعلوم‌بودن cutoff مانع ادامه تحقیق نیست، اما live باید clock رسمی داشته باشد.

| ID | سؤال حل‌نشده | مانع چه چیزی است؟ | مسیر رفع در Phase 2 |
|---|---|---|---|
| U01 | ساعت رسمی شروع/پایان و زمان باقی‌مانده توسعه | برنامه زمان‌بندی و خروج نهایی | اعلام برگزارکننده با timezone |
| U02 | اولویت بند realized بر mark-to-market؛ تکلیف dust/USDT و فروش خودکار | پذیرش سود نهایی | پاسخ مکتوب داور؛ طراحی فعلی TMN-flat |
| U03 | نسخه نهایی جدول امتیاز و حداقل فعالیت | بهینه‌سازی معیار رقابت | تأیید وزن‌ها؛ معامله مصنوعی ممنوع |
| U04 | الزام MCP در مسابقه و سازگاری راهنمای تأیید انسانی ابزارهای write با اجرای خودکار | انتخاب مسیر write در معماری | URL، transport و schema اکنون معلوم‌اند؛ راهنمای رسمی تغییر state را نیازمند تأیید معرفی می‌کند، enforcement سرور و تفسیر داور آزموده/روشن نشده؛ فقط‌خواندنی MCP قابل استفاده است [W26][W27][P08] |
| U05 | کلید حساب چالش و scope عملی خصوصی | هرگونه live readiness | صاحب حساب در محیط امن خود بررسی کند؛ این Research کلید نمی‌خواهد |
| U06 | نرخ fee دقیق، feeAsset و round/precision عملی | هزینه، اندازه و سود خالص قابل‌اعتماد | خواندن fee/metadata و fixture خصوصی در فاز بعد |
| U07 | نحوه اعمال warning_limit=1000 و liquidity_limit=900 در حساب خاص | حد زیان سخت قابل‌اتکا | واحد در UI رسمی **تومان** نمایش داده شد و ابهام واحد رفع شد؛ روش trigger/valuation واقعی هنوز تأیید نشده؛ درصد فرض نشود [P01][W28] |
| U08 | عمق تاریخچه، retention، محدودیت درخواست و کیفیت همه نمادها | دامنه بک‌تست معتبر | دانلود chunk کوچک و گزارش coverage؛ rate limit را از headers و اسناد هر endpoint بگیرید |
| U09 | native SL/OCO، رفتار reserve، partial fill و MARKET/STOP در credit | حفاظت سرمایه در live | مستند تکمیلی + آزمون کنترل‌شده فاز بعد با مجوز مستقل |
| U10 | auto-sell طرح نسبت به cutoff داوری، هزینه و قیمت آن | تسویه نهایی | تأیید برگزارکننده؛ اتکا به فروش خودکار ممنوع در طراحی |
| U11 | IP/شبکه میزبان، دسترسی LLM، هزینه/latency و سقف budget | پایداری agent | benchmark محیط اجرا؛ بدون کلید در این فاز |

عددهای ۱۰۰۰/۹۰۰ در public response مشاهده شدند و UI رسمی آن‌ها را تومان نمایش می‌دهد. [W28] با این حال نبود وثیقه در طرح هکاتون، مجوز استنتاج «تقریباً نمی‌توان لیکویید شد» نیست. متن طرح می‌گوید در پایان/توقف، دارایی‌ها فروش آنی می‌شوند؛ Brief تسویه پس از تأیید را مطرح می‌کند. ترتیب دقیق دو رویداد UNKNOWN باقی می‌ماند. [P01][B01]

## ارجاع‌ها

[B01]: evidence/BRIEF_PROVIDED_FA.md "Brief رسمی ارائه‌شده توسط صاحب پروژه؛ دسترسی 2026-10-22"
[P01]: https://api.wallex.ir/v1/prop/packages/183 "طرح ۱۸۳: پاسخ عمومی واقعی؛ دسترسی 2026-10-22"
[P02]: https://api.wallex.ir/hector/web/v1/markets "مارکت‌های اسپات: پاسخ عمومی واقعی؛ دسترسی 2026-10-22"
[P03]: https://api.wallex.ir/v1/markets "Metadata بازارها: پاسخ عمومی واقعی؛ دسترسی 2026-10-22"
[P04]: https://api.wallex.ir/v1/udf/history?symbol=BTCTMN&resolution=1&from=1792566950&to=1792653350 "تاریخچه ۱دقیقه BTCTMN: پاسخ واقعی؛ دسترسی 2026-10-22"
[P05]: https://api.wallex.ir/v1/udf/history?symbol=BTCTMN&resolution=15&from=1792566950&to=1792653350 "تاریخچه با resolution=15: پاسخ واقعی؛ دسترسی 2026-10-22"
[P06]: https://api.wallex.ir/v1/depth?symbol=BTCTMN "عمق BTCTMN: پاسخ عمومی واقعی؛ دسترسی 2026-10-22"
[W01]: https://developers.wallex.ir/docs/ "مقدمه — Wallex API Documentation؛ دسترسی 2026-10-22"
[W02]: https://developers.wallex.ir/docs/create-api-key "ساخت API-Key — Wallex API Documentation؛ دسترسی 2026-10-22"
[W03]: https://developers.wallex.ir/docs/basic-services-candles "کندل‌ها — Wallex API Documentation؛ دسترسی 2026-10-22"
[W04]: https://developers.wallex.ir/docs/spot-active-markets "مارکت‌های فعال اسپات — Wallex API Documentation؛ دسترسی 2026-10-22"
[W05]: https://developers.wallex.ir/docs/spot-create-order "ثبت سفارش اسپات — Wallex API Documentation؛ دسترسی 2026-10-22"
[W06]: https://developers.wallex.ir/docs/spot-types "انواع سفارش اسپات — Wallex API Documentation؛ دسترسی 2026-10-22"
[W07]: https://developers.wallex.ir/docs/spot-cancel-order "لغو سفارش — Wallex API Documentation؛ دسترسی 2026-10-22"
[W08]: https://developers.wallex.ir/docs/spot-single-order-detail "جزئیات سفارش — Wallex API Documentation؛ دسترسی 2026-10-22"
[W09]: https://developers.wallex.ir/docs/spot-order-list "لیست سفارش‌ها — Wallex API Documentation؛ دسترسی 2026-10-22"
[W10]: https://developers.wallex.ir/docs/spot-active-orders "سفارش‌های فعال — Wallex API Documentation؛ دسترسی 2026-10-22"
[W11]: https://developers.wallex.ir/docs/basic-services-depth "عمق بازار — Wallex API Documentation؛ دسترسی 2026-10-22"
[W12]: https://developers.wallex.ir/docs/basic-services-latest-trades "آخرین معاملات — Wallex API Documentation؛ دسترسی 2026-10-22"
[W13]: https://developers.wallex.ir/docs/basic-services-account-fee "کارمزد حساب — Wallex API Documentation؛ دسترسی 2026-10-22"
[W14]: https://developers.wallex.ir/docs/trade-credit-package-details "جزئیات طرح و بازارهای غیرفعال — Wallex API Documentation؛ دسترسی 2026-10-22"
[W17]: https://developers.wallex.ir/docs/trade-credit-wallet "کیف پول اعتبار معاملاتی — Wallex API Documentation؛ دسترسی 2026-10-22"
[W18]: https://developers.wallex.ir/docs/socket-migration "راهنمای مهاجرت وب‌سوکت — Wallex API Documentation؛ دسترسی 2026-10-22"
[W19]: https://developers.wallex.ir/docs/socket-buy-depth "عمق خرید وب‌سوکت — Wallex API Documentation؛ دسترسی 2026-10-22"
[W20]: https://developers.wallex.ir/docs/socket-sell-depth "عمق فروش وب‌سوکت — Wallex API Documentation؛ دسترسی 2026-10-22"
[W21]: https://developers.wallex.ir/docs/socket-private-channel "رویدادهای خصوصی کاربران — Wallex API Documentation؛ دسترسی 2026-10-22"
[W24]: https://developers.wallex.ir/docs/generated/get-markets "Get all markets — Wallex API Documentation؛ دسترسی 2026-10-22"
[W25]: https://developers.wallex.ir/docs/generated/sub-account-balances-details "Sub Account Balances Details — Wallex API Documentation؛ دسترسی 2026-10-22"
[X01]: https://github.com/mhsenpc/wallex_mcp_server "mhsenpc/wallex_mcp_server؛ دسترسی 2026-10-22"

[P07]: https://api.wallex.ir/mcp "MCP initialize response — Wallex official server؛ دسترسی 2026-10-22"
[P08]: https://api.wallex.ir/mcp "MCP tools/list — 52 advertised tools؛ دسترسی 2026-10-22"
[P09]: https://api.wallex.ir/mcp "MCP public package details — package 183؛ دسترسی 2026-10-22"
[W26]: https://wallex.ir/landing/mcp/ "MCP والکس — صفحه رسمی اتصال؛ دسترسی 2026-10-22"
[W27]: https://api.wallex.ir/llm.txt "Wallex canonical sources and MCP access rules؛ دسترسی 2026-10-22"
[W28]: https://wallex.ir/app/trade-credit/prop/183 "هکاتون استارکوچ | والکس — صفحه رسمی طرح ۱۸۳؛ دسترسی 2026-10-22"
