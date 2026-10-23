# بک‌تست، اعتبارسنجی و معماری AI

تاریخ: 2026-10-22. این فایل پروتکل اجرای Phase 2 است؛ هیچ عملکرد strategy، سود، برتری AI یا نتیجه Shadow در این فاز تولید نشده است. کار انجام‌شده: بررسی اسناد/کد و شش GET عمومی محدود برای قابلیت‌ها؛ سپس سه درخواست فقط‌خواندنی MCP شامل initialize، tools/list و خواندن عمومی طرح۱۸۳. [P07][P08][P09] Backtest یعنی اجرای قواعد روی گذشته با فرض‌های روشن؛ Shadow یعنی تصمیم زنده با سفارش شبیه‌سازی‌شده و بدون تغییر حساب. [B01]

## بک‌تستِ عملی در زمان کم

نسخه اول candle-driven و چندفرضیه‌ای محدود باشد. event-driven یعنی تصمیم از ترتیب رخداد مشخص ساخته می‌شود؛ به replay کامل order book نیاز نیست تا منطق کندلی را تست کنیم، ولی ادعای دقت اجرای صف مجاز نیست.

### ۱. قرارداد داده قبل از هر نمودار سود

- فایل raw پاسخ Wallex را بدون بازنویسی نگه دارید؛ dataset، symbol، from/to، resolution درخواستی، فاصله واقعی timestamp، زمان دریافت و hash ثبت شود. نام TMN/USDT و واحد price صریح باشد. یک قیمت جهانی BTCUSDT جای price محلی BTCTMN نیست. [W03][P04][P05]
- lengths آرایه‌ها، ترتیب/تکرار timestamp، OHLC معتبر، حجم نامنفی، gap و کندل ناتمام بررسی شود. معنای timestamp باز/بسته و واحد volume را به‌طور مستقل احراز کنید؛ سند current تمام جزئیات را صریح نکرده است.
- در probe، ۱۵دقیقه درخواستی به فاصله واقعی یک دقیقه برگشت؛ aggregate فقط از ۱دقیقه تأییدشده بسازید. کندل ۱۵دقیقه تا تمام‌شدن بازه قابل مصرف نیست. داده بد یا gap را با آینده و backfill پر نکنید. [P04][P05]
- عمق تاریخچه قابل دریافت UNKNOWN است. یک درخواست یک‌روزه، سابقه چندماه را اثبات نمی‌کند. Phase 2 با chunk کوچک، محدودیت نرخ/حجم و گزارش coverage شروع شود؛ اگر تاریخچه کافی نبود، دامنه ادعا کاهش یابد، نه داده مصنوعی جایگزین شود.
- universe فعلی، وضعیت تاریخی delisting/listing را نشان نمی‌دهد. برای تحقیق محدود، یک universe ثابت و دلیل انتخاب ثبت کنید؛ گزینش نمادها با volume امروز روی گذشته می‌تواند survivorship/selection bias بسازد. این دو یعنی نادیده‌گرفتن بازارهای حذف‌شده یا انتخاب نمونه پس از دیدن نتیجه. [F03][P02]

### ۲. سیاست fill صریح

| خطر | رفتار لازم در شبیه‌ساز سبک | چیزی که هنوز نمی‌توان ادعا کرد |
|---|---|---|
| Look-ahead | features فقط تا کندل بسته t؛ intent پس از آن، earliest fill در t+1 به‌علاوه تأخیر مدل | ورود در close همان کندل بدون تأخیر واقع‌گرایانه نیست [F04] |
| Stop و TP در یک candle | اگر داده ریزتر معتبر نداریم، حالت زیان‌بارتر یا بازه خوش‌بینانه/بدبینانه گزارش شود | ترتیب high/low از OHLC قابل استخراج نیست [F03][N05] |
| Limit touch | قیمت به limit رسیده لزوماً fill نیست؛ عبور از قیمت یا fill probability محافظه‌کارانه با seed؛ حساسیت no-fill بررسی شود | صف واقعی یا maker fill تاریخی بدون book نداریم [N04] |
| Market / SL gap | هزینه adverse و gap به قیمت بعدی قابل معامله؛ fill دقیق در stop تضمین نشود | Market همیشه فوری و کامل پر نمی‌شود [W06] |
| Fee | هزینه هر دو سمت، feeAsset و conversion؛ نرخ واقعی حساب در live؛ قبل از آن سناریوی fee با برچسب فرض | نرخ نمونه مستند معادل سطح واقعی تیم نیست [W13][W08] |
| Slippage | هزینه تابع اندازه و stress؛ spread ناموجود تاریخی با سناریوی ثابت/متغیر فرضی شفاف | book امروز را به تمام گذشته تعمیم ندهید |
| Latency | تأخیر داده/تصمیم/AI/submit؛ signal بعد از TTL حذف؛ یک و چند کندل تأخیر را stress کنید | کندل یک‌دقیقه latency میلی‌ثانیه‌ای را شبیه‌سازی نمی‌کند |
| Partial fill | سناریوی پرشدن بخشی، lock سرمایه و حفاظت همان مقدار | candle volume جای نقدینگی قابل‌گرفتن ما نیست |
| Price/size | precision/minQty/minNotional و capital مشترک؛ خریدهای هم‌زمان سرمایه مستقل ندارند | min limits امروز ممکن است در گذشته متفاوت بوده باشند [P03] |
| Force close | پایان هر run و پنجره ۷۲ساعته به TMN، با fee و slippage و dust | mark-to-market فقط metric ریسک است، جای realized final نیست [B01] |

چرا صرف استفاده از موتور معروف کافی نیست؟ Freqtrade در مستندات fill بدون slippage داخل دامنه کندل را فرض می‌کند؛ Hummingbot simulator بخشی از محاسبات را با close و هزینه ثابت انجام می‌دهد؛ Nautilus حتی برای مصرف دوباره عمق تاریخی هشدار می‌دهد. این محدودیت‌ها دلایل افزودن stress و Shadow هستند. [F03][H03][N04]

### ۳. جداکردن گذشته برای انتخاب و ارزیابی

طرح پیشنهادی پس از مشاهده coverage: قدیمی‌ترین ۶۰٪ برای ساخت/کالیبراسیون، ۲۰٪ بعد برای validation و تازه‌ترین ۲۰٪ یک test قفل‌شده. درصدها قانون آماری یا مسابقه نیستند؛ طول کافی برای warmup، شرایط مختلف و چند موقعیت لازم است. تصادفی shuffle کردن ردیف‌ها برای سری زمانی رد است.

- train اینجا لزوماً training مدل نیست؛ محل طراحی قواعد هم هست. validation محل انتخاب از بودجه محدود تنظیم‌هاست. test فقط یک بار برای گزارش نهایی خوانده می‌شود.
- warmup آزمون می‌تواند از گذشته قبل از مرز استفاده کند؛ اجازه استفاده از آینده ندارد. scaler/threshold یادگرفته‌شده فقط با گذشته fit شود.
- labelی که سود آینده h گام را مصرف می‌کند، اگر از مرز train عبور کند حذف/purge شود. purge یعنی کنارگذاشتن نمونه‌ای که اطلاعات هدف آن وارد بخش آینده می‌شود. موقعیت‌های باز مرزی نیز با سیاست ازپیش‌ثبت‌شده بسته یا carry شوند؛ عملکرد train را با test مخلوط نکنید.
- برای robustness، دو یا سه پنجره متوالی walk-forward روی بخش پیش از test بسازید: در هر گام فقط گذشته برای calibration و پنجره بعد برای ارزیابی. Walk-forward یعنی جلو رفتن زمان با انتخاب فقط از داده قبلی.
- اگر داده/زمان کافی نیست، یک split زمانی قفل‌شده + stress + Shadow از ادعای walk-forward پرحجم ساختگی بهتر است. کمبود نمونه را صریح گزارش کنید؛ نبود معامله کافی، PASS سودآوری نیست.
- مثال بودجه تحقیق **پیشنهادی**: حداکثر ۶ تنظیم برای هر یک از دو خانواده، یعنی ۱۲ آزمایش اصلی؛ همه شکست‌ها ثبت شوند. تغییر prompt، universe و risk threshold هم trial محسوب می‌شود. برنده یک نقطه تیز پارامتر مطلوب نیست؛ همسایه‌های آن باید رفتار مشابه داشته باشند.
- دیدن نتیجه test و تغییر strategy، آن test را validation می‌کند. نام OOS را حفظ نکنید؛ به دوره تازه یا forward نیاز است. هشدار multiple testing از پژوهش overfitting می‌آید، ولی CSCV کامل در این هکاتون پیشنهاد نمی‌شود. [A03]

### ۴. robustness با هزینه کم

Robustness یعنی نتیجه با تغییر کوچک فرض‌ها فرو نریزد. به‌جای ساخت Monte Carlo پیچیده، این‌ها را اجرا کنید:

۱. fee واقعی/سناریوی محافظه‌کارانه؛ slippage در چند سطح، از جمله دو برابر baseline فرضی.
۲. تأخیر بیشتر، حذف تصادفی بخشی از fills و چند outage کوتاه با seed ثابت.
۳. تغییر محدود پارامتر همسایه، زمان شروع و زیرپنجره‌های ۷۲ساعته؛ انتخاب شروع خوب بعد از دیدن نتیجه ممنوع.
۴. اگر تعداد معامله کافی است، block bootstrap بلوک زمانی/معامله برای توزیع P&L و drawdown؛ bootstrap یعنی نمونه‌برداری مجدد برای دیدن حساسیت نتیجه. ترتیب مستقلِ shuffle همه معاملات وابستگی بازار را حذف می‌کند؛ آن را احتمال واقعی زیان معرفی نکنید.
۵. اگر نمونه کم است، stressهای قابل توضیح را نشان دهید و برآورد اعتماد آماری را UNKNOWN بگذارید.

از مدل queue سفارشی، Monte Carlo چندلایه، optimizer بزرگ و annualization چشمگیر روی سه روز صرف‌نظر شود. Sharpe یعنی بازده نسبت به نوسان؛ در نمونه کوتاه یا توزیع با جهش‌های بزرگ ممکن است گمراه‌کننده باشد. معیار اصلی net realized TMN و افت کل حساب است، همراه تعداد معاملات و عدم‌قطعیت. [B01][A05]

### ۵. خروجی هر run

| حوزه | فیلدهای لازم |
|---|---|
| provenance | run_id، commit، config hash، dataset hash، universe/limits snapshot، بازه زمانی/UTC، نسخه کتابخانه و seed |
| مالی | cash شروع/پایان TMN، realized P&L خالص، هزینه‌ها، دارایی باقیمانده و dust، turnover، exposure time |
| ریسک | max drawdown equity، بدترین معامله، زیان متوالی، قفل‌ها و دلیل توقف |
| اجرا | signals، orders، fills، fill ratio، partial/reject/cancel/UNKNOWN، deviation از reference price، latency توزیعی |
| اثر AI | تعداد تأیید/رد/WAIT، disagreement با baseline، اثر بر هزینه/ریسک، خطای schema و timeout |
| محدودیت | فرض fill، spread تاریخی ناموجود، coverage، تعداد trial، نقش آزمون: train/validation/test/forward |

drawdown یعنی بیشترین افت ارزش حساب نسبت به بالاترین ارزش قبلی؛ برای دارایی باز از قیمت قابل فروش پس از هزینه استفاده شود. داده واقعی حساب داور ممکن است valuation متفاوتی داشته باشد؛ هر دو روش برچسب بخورند. [B01]

## Shadow که واقعاً سفارش نمی‌فرستد

همان pipeline تصمیم و risk، با execution adapter شبیه‌سازی‌شده استفاده شود. endpoint write در این mode در دسترس نباشد؛ صرفاً اسم `dry_run=true` کافی نیست. از Condor ایده مهار mutation و از تست تجمع exposure در یک tick استفاده کنید. [C01][C04]

- feed واقعی، local receive time و exchange time در صورت وجود ثبت؛ reconnect و snapshot هر دو سمت book آزمایش شود. پیام book را delta تلقی نکنید. [W19][W20]
- shadow market fill از سطوح قابل‌مشاهده پس از latency شبیه‌سازی‌شده و به اندازه عمق قابل‌مصرف؛ اگر depth کم است partial/no-fill، نه نقدینگی بی‌نهایت.
- shadow limit با فرض محافظه‌کارانه صف؛ touch فوری fill کامل ایجاد نکند. چون صف واقعی نامعلوم است حد خوش‌بینانه و بدبینانه جدا شوند.
- capital، pending reservation، fees و min limits همان kernel؛ مسیر HTTP ثبت/لغو واقعی باید غیرقابل‌فراخوانی باشد.
- سناریوهای هدفمند: پاسخ سفارش گم‌شده، fill بعد از cancel، partial fill، تکرار event، restart با سفارش باز، LLM timeout، stale book، تغییر allowlist، اتمام زمان و Market rejection. برای سناریوهای write از fixture استفاده کنید؛ این فاز اجازه ارسال سفارش ندارد.
- بازه پیشنهادی Shadow تا حد زمان موجود و پوشش سناریوها؛ «دو ساعت» به‌تنهایی اثبات edge نیست. آمادگی اجرا را از شواهد رفتار بسنجید، نه ساعت صرفاً.

**دروازه قبولی عملیاتی پیشنهادی:** صفر سفارش واقعی در Shadow؛ صفر duplicate/overspend در سناریوها؛ همه unknownها به reconciliation ختم شوند یا حالت مسدود روشن؛ stop watcher بدون AI ادامه دهد؛ پایان replay به TMN یا باقی‌مانده صریح برسد. زمان پاسخ مجاز از benchmark محیط و افق strategy استخراج شود؛ SLA اختراعی نسازید.

**دروازه اقتصادی پیشنهادی:** نتایج OOS و cost stress، drawdown و وابستگی به چند معامله آشکار باشند؛ اگر edge پس از هزینه دیده نشد یا نمونه کافی نبود، برتری اثبات نشده. این gate مجوز پنهان برای حذف تمام ناموفق‌ها از گزارش نیست.

## مقایسه نقش AI

| گزینه | مزیت مورد انتظار | هزینه و خطر | تصمیم |
|---|---|---|---|
| LLM تصمیم‌گیر مستقیم BUY/SELL/size آزاد | انعطاف و استفاده واضح از AI | عددسازی، هزینه inference، ناپایداری، دشواری replay، exposure نامحدود | Reject؛ ابزار raw order/size آزاد ندهید |
| LLM regime classifier | جمع‌بندی evidence چندگانه | داده عددی را از rule ساده بهتر تفسیرکردن اثبات نشده؛ برچسب regime معیار نهایی سود نیست | فقط بخشی از arbitration؛ با classifier ساده مقایسه شود |
| LLM signal arbitration | انتخاب/رد میان چند intent معتبر و WAIT؛ اثر علّی روشن | ممکن است تمام فرصت‌ها را حذف کند یا صرفاً ruleها را تکرار کند | **پیشنهاد اصلی**؛ کم‌دامنه و قابل ablation |
| LLM strategy selection | انتخاب C1/C2/WAIT براساس وضعیت | switching/churn و prompt overfit | Candidate C3، پس از پذیرش مستقل هر خانواده |
| Risk adviser بدون override | توضیح ریسک و یافتن evidence ناقص | اگر هیچ اثر بر تصمیم نداشته باشد برای ۳۵امتیاز AI کافی‌بودن UNKNOWN | مکمل؛ sole AI role پیشنهاد نمی‌شود [B01] |
| ML کوچک + LLM | ML می‌تواند score عددی و LLM arbitration بدهد | dataset label، leakage، calibration و دو لایه debug | فعلاً defer؛ فقط با dataset پاک آماده و نتیجه OOS بهتر |
| Ensemble چند LLM | دیدگاه‌های متفاوت | هزینه/latency بیشتر، خطاهای همبسته، arbitration جدید | Reject در زمان کوتاه |
| RL | یادگیری سیاست متوالی | شبیه‌ساز معتبر، reward، داده کافی، آموزش چند seed و فاصله simulation/live | Reject برای این هکاتون [L01] |
| Rule-based کامل | baseline سریع و deterministic | شرط نقش واقعی AI را به‌تنهایی تأمین نمی‌کند | **B0 لازم برای مقایسه**؛ موتور رقابتی بدون AI واقعی اعلام نشود [B01] |

پژوهش FinBen تفاوت توان مدل در وظایف مالی را نشان می‌دهد، نه سود تضمینی این معماری در Wallex. انتخاب arbitration یک تصمیم مهندسی بر مبنای کنترل دامنه و قابلیت سنجش است؛ برتری مدل در پیش‌بینی قیمت در این تحقیق ثابت نشده. [A04]

## قرارداد پیشنهادی agent؛ بدون Training

`MarketSnapshot → deterministic features → bounded candidate list → LLM selection → deterministic risk verdict → executor`

یک مدل ازپیش‌آموزش‌دیده با prompt و schema محدود برای شروع کافی است. انتخاب vendor/model، هزینه و latency تا مشخص‌شدن دسترسی تیم UNKNOWN می‌ماند. هیچ provider یا framework Agent صرفاً به دلیل محبوبیت انتخاب نشده است.

ورودی: snapshot_id، زمان انقضا، features با واحد/پنجره، candidate_idها، هزینه تخمینی و محدودیت‌های خواندنی. محاسبه indicator، موجودی، precision و مقدار سفارش با deterministic code انجام شود. متن آزاد خبری/صفحه وب در نسخه اول ورودی نیست.

خروجی **نمونه قرارداد داده، نه کد محصول**:

```json
{
  "snapshot_id": "observed_snapshot_id",
  "selected_candidate_id": null,
  "action": "WAIT",
  "regime": "UNCERTAIN",
  "reason_codes": ["CONFLICTING_SIGNALS"],
  "evidence_feature_ids": ["provided_feature_id"],
  "brief_rationale": "خلاصه قابل ارائه بر پایه داده موجود"
}
```

- id باید در فهرست ورودی باشد؛ schema نامعتبر، تناقض داده یا پاسخ دیرهنگام → WAIT برای ورود جدید.
- مدل نمی‌تواند symbol، price، size، stop یا risk budget جدید بسازد، قانون را override کند یا code اجرا کند.
- قبل از submit snapshot/TTL/risk دوباره چک شود؛ فاصله inference تا اجرا فرصت را منقضی می‌کند.
- تعداد فراخوانی محدود به رخداد candidate/کندل جدید باشد؛ حلقه inference روی هر tick book لازم نیست.
- LLM outage → ورود جدید WAIT؛ exits، reconciliation و پایان مسابقه deterministic ادامه دارند. جایگزینی پنهان همه تصمیم‌ها با rule و ادعای «AI فعال» پذیرفته نیست.
- rationale خلاصه تصمیم و evidence کافی است؛ نیاز به استخراج استدلال خصوصی داخلی مدل نیست. prompt/input/output قابل ارائه بدون secret ذخیره شود.

### اثبات ارزش واقعی AI با ablation

Ablation یعنی یک جزء را حذف کنیم و اثرش را روی همان شرایط بسنجیم. سه سیاست مقایسه شوند:

- B0: همان candidates، selector deterministic ساده، همان risk/execution.
- A1: همان candidates با LLM approve/WAIT یا انتخاب.
- A2: selector ساده regime با همان ویژگی‌ها؛ تنها اگر زمان هست، برای تفکیک ارزش LLM از خود switching.

دوره، هزینه، سرمایه و داده مشترک؛ ولی ledger هر سیاست جدا چون انتخاب‌ها روی موجودی بعدی اثر می‌گذارند. گزارش disagreement، معاملات از‌دست‌رفته/جلوگیری‌شده، هزینه inference، latency و P&L/افت سرمایه. چند مثال از تصمیم‌های تغییرکرده نشان دهید. انتخاب یک مثال سودده کافی نیست.

اگر AI فقط همان گزینه ثابت را برمی‌گرداند، evidence ارزش آن ضعیف است. اگر دائم WAIT می‌کند، drawdown کم همراه با غیرفعال‌بودن را موفقیت معرفی نکنید. داور باید درباره کف پذیرش AI نظر بدهد؛ هیچ معماری تضمین دریافت امتیاز ندارد. [B01]

### replay و نشت از حافظه مدل

**Replay تصمیم:** داده و پاسخ ثبت‌شده AI را مصرف کنید، با همان نسخه kernel نتیجه intent/risk/order simulation را بازسازی کنید؛ inference جدید لازم نیست.

**Rerun مدل:** همان prompt را دوباره به سرویس بفرستید؛ حتی temperature پایین تضمین برابری بیتی نیست. model id/version، تنظیمات، prompt hash، پاسخ، latency و error ذخیره شوند. replay و rerun را در Demo یکسان معرفی نکنید.

بک‌تست تاریخی LLM می‌تواند از دانسته‌های پیش‌آموزش درباره رویدادها تأثیر بگیرد. پنهان‌کردن تاریخ/نماد به‌تنهایی آن را اثباتاً حذف نمی‌کند. برای بخش AI، Shadow روی داده‌ای که پس از freeze prompt آمده و سپس replay مهم‌ترین شواهد عملی این هکاتون است. نتیجه historical LLM با قید احتمال آلودگی داده گزارش شود. این ریسک طراحی است؛ FinBen آن را برای این مدل/داده خاص اثبات یا رفع نکرده است.

## آیا Training یا Fine-tuning لازم است؟

**برای نسخه اول خیر.** مسئله اولیه ما ساخت policy پژوهشی جدید نیست؛ اتصال صحیح، هزینه واقعی، قفل ریسک و سنجش تصمیم AI است. Fine-tuning یعنی تغییر وزن‌های مدل با نمونه؛ prompt/schema یعنی استفاده از مدل موجود بدون تغییر وزن. هیچ مجموعه برچسب معتبر Wallex یا سابقه اجرایی پاک تحویل نشده است.

| راه | dataset/کار لازم | زمان نسبی نسبت به baseline | شرط بازگشت به بررسی |
|---|---|---|---|
| prompt/schema مدل آماده | چند snapshot با outcome پنهان، contract tests، forward ablation | کم‌تر؛ همچنان دسترسی مدل و latency باید سنجیده شود | همین فاز معماری پیشنهاد می‌شود |
| ML ساده برای score معامله | تاریخچه کافی، features ثابت، label پس از هزینه، purge split، calibration | متوسط تا زیاد؛ آماده‌سازی/اعتبارسنجی غالب است، نه فقط fit | داده آماده و زمان اضافه، بهبود OOS نسبت به rule |
| Fine-tune LLM | نمونه تصمیم صحیح و متنوع، train/eval جدا، budget و deployment | زیاد و با ارزش افزوده نامعلوم | ضعف تکرارشونده اندازه‌گیری‌شده در مدل آماده، خارج هکاتون |
| RL/FinRL | شبیه‌ساز fee/latency/partial fill، reward، داده چند regime، چند seed | بسیار زیاد؛ CryptoEnv فعلی اجرای Wallex را مدل نمی‌کند [L01] | بعد از داشتن execution و dataset معتبر، نه برای امتیاز ظاهری AI |

ساعت قطعی بدون مهارت تیم و داده موجود قابل برآورد نیست. اگر کمتر از یک روز توسعه باقی است، حتی C2/C3 و ML اضافه حذف شوند؛ C1 + AI محدود + ریسک/replay/Shadow اولویت دارد. مدت سه‌روزه رقابت را با سه روز کامل زمان توسعه باقی‌مانده یکی نگیرید.

## ارجاع‌ها

[A03]: https://escholarship.org/uc/item/4w1110bb "The Probability of Backtest Overfitting — Bailey et al.؛ دسترسی 2026-10-22"
[A04]: https://arxiv.org/abs/2402.12659v2 "FinBen: A Holistic Financial Benchmark for Large Language Models؛ دسترسی 2026-10-22"
[A05]: https://onlinelibrary.wiley.com/doi/10.1002/ijfe.70036 "Cryptocurrency Momentum: Is It an Illusion?؛ دسترسی 2026-10-22"
[B01]: evidence/BRIEF_PROVIDED_FA.md "Brief رسمی ارائه‌شده توسط صاحب پروژه؛ دسترسی 2026-10-22"
[C01]: https://github.com/hummingbot/condor/blob/8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c/condor/agents/risk.py "Agent risk gate؛ دسترسی 2026-10-22"
[C04]: https://github.com/hummingbot/condor/blob/8e4860fa3d7efd4b55c2b74dd7f1f439e5a4f32c/tests/test_risk_gate.py "Risk gate tests؛ دسترسی 2026-10-22"
[F03]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/docs/backtesting.md "Backtesting assumptions؛ دسترسی 2026-10-22"
[F04]: https://github.com/freqtrade/freqtrade/blob/c051e9065c5b677f03817dd1f83cb980af1ed684/docs/lookahead-analysis.md "Lookahead analysis؛ دسترسی 2026-10-22"
[H03]: https://github.com/hummingbot/hummingbot/blob/9af100d6822da7d2d0291a906c730ef172284ee2/hummingbot/strategy_v2/backtesting/executors_simulator/position_executor_simulator.py "PositionExecutorSimulator؛ دسترسی 2026-10-22"
[L01]: https://github.com/AI4Finance-Foundation/FinRL/blob/c0018c25de864157dc8470f0047b0d10ec721715/finrl/meta/env_cryptocurrency_trading/env_multiple_crypto.py "CryptoEnv؛ دسترسی 2026-10-22"
[N04]: https://github.com/nautechsystems/nautilus_trader/blob/9156e8f2d7126236269b6fcd2e6402b7a8e4e6d8/docs/concepts/backtesting/fill-models.md "Fill models؛ دسترسی 2026-10-22"
[N05]: https://github.com/nautechsystems/nautilus_trader/blob/9156e8f2d7126236269b6fcd2e6402b7a8e4e6d8/docs/concepts/backtesting/bar-execution.md "Bar execution؛ دسترسی 2026-10-22"
[P02]: https://api.wallex.ir/hector/web/v1/markets "مارکت‌های اسپات: پاسخ عمومی واقعی؛ دسترسی 2026-10-22"
[P03]: https://api.wallex.ir/v1/markets "Metadata بازارها: پاسخ عمومی واقعی؛ دسترسی 2026-10-22"
[P04]: https://api.wallex.ir/v1/udf/history?symbol=BTCTMN&resolution=1&from=1792566950&to=1792653350 "تاریخچه ۱دقیقه BTCTMN: پاسخ واقعی؛ دسترسی 2026-10-22"
[P05]: https://api.wallex.ir/v1/udf/history?symbol=BTCTMN&resolution=15&from=1792566950&to=1792653350 "تاریخچه با resolution=15: پاسخ واقعی؛ دسترسی 2026-10-22"
[W03]: https://developers.wallex.ir/docs/basic-services-candles "کندل‌ها — Wallex API Documentation؛ دسترسی 2026-10-22"
[W06]: https://developers.wallex.ir/docs/spot-types "انواع سفارش اسپات — Wallex API Documentation؛ دسترسی 2026-10-22"
[W08]: https://developers.wallex.ir/docs/spot-single-order-detail "جزئیات سفارش — Wallex API Documentation؛ دسترسی 2026-10-22"
[W13]: https://developers.wallex.ir/docs/basic-services-account-fee "کارمزد حساب — Wallex API Documentation؛ دسترسی 2026-10-22"
[W19]: https://developers.wallex.ir/docs/socket-buy-depth "عمق خرید وب‌سوکت — Wallex API Documentation؛ دسترسی 2026-10-22"
[W20]: https://developers.wallex.ir/docs/socket-sell-depth "عمق فروش وب‌سوکت — Wallex API Documentation؛ دسترسی 2026-10-22"

[P07]: https://api.wallex.ir/mcp "MCP initialize response — Wallex official server؛ دسترسی 2026-10-22"
[P08]: https://api.wallex.ir/mcp "MCP tools/list — 52 advertised tools؛ دسترسی 2026-10-22"
[P09]: https://api.wallex.ir/mcp "MCP public package details — package 183؛ دسترسی 2026-10-22"
