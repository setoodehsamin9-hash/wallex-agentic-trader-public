# شواهد انتخاب معماری Agent برای Wallex

تاریخ: **۲۲ اکتبر ۲۰۲۶**. دامنه بررسی: نسخه‌های Python. **این سند Framework نهایی انتخاب نمی‌کند؛ دو مسیر را برای آزمایش فنی کوتاه Architect محدود می‌کند: PydanticAI با state صریح برنامه، و LangGraph با checkpoint پایدار.** انتخاب براساس مهارت تیم، مدل قابل‌دسترسی و نتیجه آزمون اتصال/بازیابی است، نه محبوبیت.

## ۱. مبنا و حد شواهد

Brief کامل و فایل‌های Markdown Phase 01، از جمله اصلاحات MCP، ابتدا مرور شدند. Brief نقش واقعی AI، تصمیم و اجرای الگوریتمی، مدیریت ریسک، لاگ دلیل معامله و اجرای مجدد را می‌خواهد؛ جدول امتیاز هنوز با عنوان «پیشنهاد» است. جداسازی risk/execution و استفاده محدود از LLM، پیشنهاد Phase 01 است، نه متن الزام‌آور Brief. [B][PH]

مستندات فعلی و کد MCP، approval، persistence، retry و نمونه تست‌های upstream بررسی ایستا شدند. هیچ Framework نصب یا با مدل واقعی benchmark نشد؛ هیچ ابزار Wallex در این تحقیق جدید فراخوانی نشد. قابلیت transport از اسناد/کد نتیجه شده؛ **سازگاری انتهابه‌انتهای هر Framework با Wallex هنوز آزموده نشده**. کد production، معامله و ابزار خصوصی اجرا نشده‌اند.

### قرارداد Wallex که انتخاب را محدود می‌کند

- endpoint رسمی `https://api.wallex.ir/mcp` با **Streamable HTTP**؛ هدر خصوصی `X-API-Key` متعلق به حساب هدف؛ ابزار عمومی بدون کلید. MCP، stream وب‌سوکت یا برداشت ارائه نمی‌دهد. پس feed سریع و watcher خروج، وظیفه مستقل‌اند. [W][W-RULES]
- snapshot Phase 01 دارای ۵۲ ابزار است: **هیچ‌کدام annotation استاندارد ایمنی ندارند**؛ ۱۵ مورد در توضیح، تغییردهنده state معرفی شده‌اند. اعتماد به نبود `destructiveHint` برای آزادکردن ابزار، خطاست؛ registry صریح نام ابزار و default-deny لازم است. [SCHEMA]
- schema سفارش Wallex فقط side/symbol/type را required کرده و مقدار/enumهای کافی را enforce نمی‌کند. Framework با schema معتبر هنوز می‌تواند intent معاملاتی نامعتبر تولید کند؛ validator دامنه مستقل لازم است. [SCHEMA]
- راهنمای Wallex برای عملیات تغییردهنده تأیید صریح می‌خواهد، درحالی‌که Brief اجرای خودکار را مطرح می‌کند. **Autonomous یک قابلیت معماری است، نه مجوز موجود مسابقه یا حساب.** سازگاری این دو پیش از Live باید روشن شود؛ فعلاً این UNKNOWN مانع تحقیق نیست و اطلاعات تازه از صاحب پروژه درخواست نمی‌شود. [B][W-RULES]

## ۲. سه نوع state را یکی نکنیم

| نوع | مثال | مالک مناسب |
|---|---|---|
| تاریخچه مدل | پیام‌ها، tool call، tool result و خروجی ساخت‌یافته | Agent SDK یا adapter مدل |
| وضعیت workflow | دریافت داده، انتظار تأیید، پایان run، خطا و checkpoint | orchestrator + storage پایدار |
| حقیقت معاملاتی | سفارش pending، سرمایه رزروشده، fill، موجودی و زیان | ledger و executor مستقل؛ تطبیق با صرافی |

MCP session هم صرفاً اتصال/جلسه پروتکل است؛ حافظه Agent یا حساب معاملاتی نیست. داشتن checkpoint یا SQLiteSession به‌تنهایی تضمین «سفارش فقط یک بار» نمی‌دهد. این مرزبندی، استنتاج معماری از semantics بازیابی و سابقه Phase 01 است. [LG-C][PA-S][OA-S][PH]

## ۳. مقایسه قابلیت‌ها

«پشتیبانی» در جدول یعنی قابلیت مستند/کدشده، نه تست موفق این پروژه. همه گزینه‌ها برای چند رفت‌وبرگشت **مدل → tool → result → مدل** قابل استفاده‌اند؛ در Custom باید حلقه را خودمان بسازیم. ثبت این رفت‌وبرگشت‌ها با دسترسی به استدلال خصوصی داخلی مدل متفاوت است.

| گزینه | MCP و tool calling واقعی | state و human approval | structured output / testing |
|---|---|---|---|
| **LangGraph** | graph منطق اجرا را می‌چیند؛ MCP از client/adapter بیرونی و node ابزار می‌آید؛ خود graph transport Wallex نیست | state و checkpoint، `interrupt`/`Command`؛ storage پایدار لازم است؛ node قطع‌شده از ابتدا اجرا می‌شود | schema/validation از مدل و ابزار انتخابی؛ تست node و مسیر/resume؛ تست interrupt upstream موجود [LG][LG-C][LG-T] |
| **LangChain** | `create_agent` حلقه آماده دارد؛ MCPAdapter فعلی بر FastMCP است؛ مسیر قدیمی `langchain-mcp-adapters` هم بررسی شد | روی LangGraph ساخته شده؛ HITL middleware برای approve/edit/reject و checkpointer | خروجی ساخت‌یافته در agent factory؛ middleware و مدل fake برای تست؛ استقلال از LangGraph فرض نشود [LC][LC-C][LC-H] |
| **PydanticAI** | `MCPToolset` و Streamable HTTP، ابزار typed و حلقه Agent | `Conversation` قابل ذخیره؛ deferred approval با pause/result/resume؛ دوام وسط run به storage/backend انتخابی وابسته | `output_type` و validation؛ `TestModel`/`FunctionModel` بدون مدل واقعی؛ مناسب قرارداد تصمیم محدود [PA-M][PA-A][PA-S][PA-O][PA-T] |
| **OpenAI Agents SDK** | `MCPServerStreamableHttp` با header/filter؛ حلقه Runner آماده؛ برای کنترل محلی لازم نیست HostedMCPTool انتخاب شود | Sessions برای history؛ `RunState` برای approval pause/resume و serialization؛ این دو یکی نیستند | typed outputs، ابزارهای guardrail و `ScriptedModel`؛ تست approval واقعی MCP در سورس وجود دارد [OA-M][OA-H][OA-S][OA-T] |
| **Custom + MCP client رسمی** | transport و `call_tool` از SDK رسمی؛ نگاشت پیام مدل/ابزار، loop limit و output validation مسئولیت ما | state machine کوچک قابل کنترل؛ اما pause/resume/approval storage و recovery را باید بسازیم | fakes و schema دست خودمان؛ تمام قراردادها و تست‌های glue را تیم نگه می‌دارد [MC][MC-V] |

### هزینه و تناسب با هکاتون

این ارزیابی کیفی است؛ benchmark سرعت اجرا یا برآورد ساعت قطعی نیست.

| گزینه | مزیت مشخص | ضعف/پیچیدگی | observability و vendor lock-in | حکم این تحقیق |
|---|---|---|---|---|
| LangGraph | مرحله‌های داده/AI/تأیید/ریسک/اجرا به‌وضوح دیده می‌شوند؛ pause/resume جدی | یادگیری node/reducer/checkpointer و مرز side effect؛ MCP/model integration جدا | قفل provider کم، وابستگی به مدل state/checkpoint graph متوسط؛ LangSmith می‌تواند اضافه شود ولی audit محلی همچنان لازم است [LC-OBS] | مناسب وقتی شاخه‌ها و recovery مهم‌اند؛ shortlist دوم |
| LangChain | سریع‌ترین راه حلقه آماده برای تیمی که از قبل با آن کار کرده | هم‌پوشانی با LangGraph و چند لایه dependency؛ MCP namespace جدید beta است | providerهای متعدد، callback/trace اکوسیستم؛ مهاجرت state/middleware هزینه دارد؛ سرویس cloud جزو هسته اجباری نیست [LC-OBS] | معتبر، اما برای این مسئله گزینه مستقل shortlist نمی‌سازد |
| PydanticAI | قرارداد typed تصمیم و dependency injection؛ تغییر provider کم‌اصطکاک‌تر | state/ledger پایدار خودکار نمی‌شود؛ APIها و dependency نسل MCP را pin کنید؛ durable backend اضافه هزینه دارد | instrumentation/Logfire قابل افزودن؛ وابستگی provider کم‌تر، انتقال کامل state بین SDKها خودکار نیست [PA-OBS] | مناسب حلقه محدود و زمان کم؛ shortlist اول |
| OpenAI Agents SDK | Runner، MCP محلی، approval، Session و tracing در یک SDK | Sessions با recovery کامل اشتباه نشود؛ مسیر غیرOpenAI ممکن است adaptation و تست بیشتری بخواهد | OpenAI پیش‌فرض است ولی SDK انحصاراً OpenAI-only نیست؛ feature parity providerها تضمین نشده؛ exporter/داده trace باید کنترل شود [OA-P][OA-OBS] | گزینه قوی؛ با مدل و تجربه تیم نامعلوم، نسبت به دو مسیر بالا مزیت قطعی نداریم |
| Custom | جریان کوچک و قابل توضیح؛ وابستگی Framework کم | prototype ارزان، نسخه قابل‌بازیابی لزوماً ارزان نیست؛ approvals، log، retry و سازگاری مدل را خودمان می‌نویسیم | lock-in Framework کم، glue مخصوص provider بیشتر؛ observability و تست کاملاً بر عهده تیم | مرجع مقایسه؛ ساخت Agent loop تازه از صفر انتخاب پیش‌فرض هکاتون نیست |

## ۴. یافته‌های کد که بر معماری اثر مستقیم دارند

**LangGraph:** در `types.py`، ادامه پس از interrupt از ابتدای همان node است. پس «ثبت سفارش سپس interrupt» ممکن است هنگام resume دوباره اثر بگذارد. node تأیید را از node ارسال جدا کنید؛ ledger مستقل حتی در فاصله اثر خارجی و ذخیره checkpoint لازم است. تست‌های upstream از اجرای دوباره task تکمیل‌شده جلوگیری می‌کنند، اما این ضمانت تراکنش اتمیک با Wallex نیست. [LG-C][LG-T]

**LangChain:** در `factory.py`، `create_agent` واقعاً `StateGraph` می‌سازد و compile می‌کند؛ بنابراین حافظه/دوام مستقل تازه‌ای در کنار LangGraph نمی‌خریم. namespace جدید `langchain.mcp` از 1.4 به بعد beta است. مسیر قدیمی بررسی‌شده، نسخه 0.3.2، وابستگی `mcp<2` دارد؛ نمونه کد MCP SDK v2 را با آن مخلوط نکنید. SDK major و نسخه پروتکل MCP یک مفهوم نیستند. [LC-C][LC][LC-A][MC-V]

**PydanticAI:** `ApprovalRequiredToolset.call_tool` پیش از forwarding واقعاً approval را بررسی می‌کند؛ صرف پرامپت نیست. ولی سند deferred tools صریحاً می‌گوید approval جای احراز هویت و مجوز سمت سرور را نمی‌گیرد. Conversation، pending calls و correlation باید از storage مورداعتماد بازیابی شوند. [PA-A][PA-D][PA-S]

**Timeout و retry:** در نسخه بررسی‌شده PydanticAI، tool error به‌طور پیش‌فرض می‌تواند به `ModelRetry` تبدیل شود؛ timeout ابزار sync نیز الزاماً thread را متوقف نمی‌کند. در OpenAI SDK، `call_tool` از wrapper retry عبور می‌کند، ولی مقدار پیش‌فرض retry آن صفر است. فعال‌کردن retry عمومی روی write بدون reconciliation، خطر ارسال تکراری دارد. این دو رفتار «ایمنی اجرای معامله» نیستند. [PA-E][OA-M]

**OpenAI guardrails:** input guardrail می‌تواند به‌صورت موازی اجرا شود و پیش از لغو، ابزار اجرا شده باشد. نسخه فعلی برای MCP محلی tool guardrail هم دارد؛ با این حال risk gate باید درست پیش از side effect و با state تازه enforce شود، حتی اگر input/output check هم وجود دارد. [OA-G][OA-M]

**تست fake مدل، ابزار را fake نمی‌کند:** TestModel در PydanticAI به‌طور پیش‌فرض ابزارهای ثبت‌شده را فراخوانی می‌کند. بنابراین «مدل بدون هزینه» معادل Shadow ایمن نیست. در هر گزینه، adapter نوشتن واقعی باید در تست/Shadow غایب یا از نظر مسیر اجرا مسدود باشد. [PA-T]

## ۵. معماری مشترک؛ مستقل از Framework

`داده معتبر → حلقه محدود AI و ابزارهای read → TradeIntent ساخت‌یافته → ریسک اولیه → سیاست اختیار/تأیید → ریسک مجدد و reservation → executor → reconciliation`

این **طرح پیشنهادی** است. مدل opportunity را انتخاب/رد می‌کند و می‌تواند داده لازم را با ابزار بخواند؛ پارامتر مالی آزاد، raw write و override ریسک ندارد. ثبت intent یا درخواست تأیید نباید به مدل به‌عنوان «سفارش پرشده» گزارش شود. Agent یک مسئولیت دارد؛ افزودن چند Agent مستقل برای همین loop مزیت اثبات‌شده‌ای ندارد. [PH][PH-AI]

Risk gate، بررسی موجودی/سقف exposure/بازار/TTL و اعتبار مقدار را انجام می‌دهد. executor، `intent_id/client_id` پایدار و دفتر وضعیت سفارش دارد. اگر پاسخ submit گم شود، وضعیت **UNKNOWN** است؛ ابتدا reconcile، نه submit دوباره. حافظه Framework حقیقت سفارش نیست. exit watcher، kill switch و زمان‌بندی خروج از انتظار مدل یا approval UI مستقل بمانند. watcher همیشه خطر را تشخیص می‌دهد، اما اختیار ارسال خروج نیز باید صریحاً در policy تعیین شود؛ Advisor پنهانی مجوز write نمی‌دهد.

### سه اختیار × سه محیط

| محیط | Advisor | Approval Required | Autonomous |
|---|---|---|---|
| Backtest | پیشنهاد روی snapshot تاریخی؛ هیچ ارسال خارجی | approval با fixture/رویداد ضبط‌شده؛ اجرا فقط شبیه‌سازی | اجرای شبیه‌ساز پس از gate؛ بدون اتصال به داده «امروز» |
| Shadow | خواندن زنده و پیشنهاد؛ بدون سفارش واقعی | کاربر intent شبیه‌سازی‌شده را تأیید/رد می‌کند؛ همچنان بدون write واقعی | policy خودکار در شبیه‌ساز؛ write واقعی همچنان مسدود |
| Live | read و پیشنهاد؛ مسیر ارسال مالی بسته | فقط همان intent معتبرِ تأییدشده، سپس recheck و executor | فقط در محدوده اختیار صریحاً مجاز و قواعد روشن Wallex/مسابقه؛ risk gate پابرجاست |

authority و environment دو فیلد مستقل و مورداعتماد میزبان‌اند، نه چیزی که مدل با tool argument عوض کند. تغییر mode در یک run یا با متن پرامپت مجاز نباشد. **HITL یک نیاز محصول/ارزیابی است؛ پذیرفته‌شدن معامله تأییدشده توسط انسان در داوری خودکار هنوز از Brief قطعی نیست.** [B][W-RULES]

approval باید به hash همان intent، حساب، محیط، policy version و expiry متصل و یک‌بارمصرف باشد. approval قدیمی پس از حرکت قیمت یا تغییر مقدار، مجوز سفارش جدید نیست. risk پس از approval دوباره اجرا شود؛ رد ریسک با تأیید انسان override نشود. تأییدها در server-owned storage بمانند؛ snapshot ارسالی مرورگر مجوز قابل اعتماد نیست. این طراحی با هشدارهای هر دو SDK درباره state/approval سازگار است. [PA-D][OA-H]

### audit، بازپخش و خطا

- log محلیِ پیوسته و ledger پایدار: `run_id, decision_id, intent_id, mode, authority, clock, data_hash, prompt/model/version, tool_name/args/result/status, approval, risk_verdict, order/fill correlation`؛ همراه code/config/schema hash. secret از prompt و trace حذف شود. rationale و evidence ثبت شوند؛ ادعای ثبت استدلال خصوصی مدل نکنید.
- **Replay:** پاسخ‌های ثبت‌شده مدل و ابزار + ساعت مجازی؛ بدون مدل تازه یا Wallex write. **Rerun:** inference تازه با احتمال نتیجه متفاوت. checkpoint/trace به‌تنهایی replay اقتصادی نمی‌سازد. [PH-AI]
- مدل/transport/tool/run هر کدام deadline دارند؛ سقف جمع retryها باید در عمر signal جا شود. خطای schema یا `isError=true` موفقیت مالی نیست. خطای AI → WAIT ورود جدید؛ watcher ریسک همچنان کار کند.
- readها retry محدود و backoff دارند؛ write نامعلوم مسیر reconciliation دارد. برنامه retry را در چند لایه SDK/HTTP/model بی‌حساب ضرب نکنید. cancel یا timeout، rollback سفارش نیست.
- یک writer برای حساب و reservation اتمیک لازم است؛ دو پیشنهاد هم‌زمان نباید یک موجودی را دوبار مصرف کنند. tracing اختیاری هر Framework برای debug مفید است، اما جای ledger را نمی‌گیرد؛ در OpenAI capture داده حساس trace به‌طور پیش‌فرض فعال گزارش شده است. [OA-OBS]

## ۶. فقط دو مسیر برای بررسی عملی Architect

### مسیر A — PydanticAI + state پایدار کوچک برنامه + kernel مستقل

برای حلقه محدود «مشاهده → چند tool read → انتخاب signal یا WAIT» مناسب‌تر است: output typed، MCP و deferred approval را آماده می‌دهد و تیم حلقه LLM را از صفر نمی‌سازد. Conversation و state برنامه در storage خود تیم نگهداری شوند؛ ledger جدا بماند. این **Custom Orchestrator از صفر نیست**؛ فقط میزبان سیاست و state دامنه کوچک است. [PA-M][PA-O][PA-S]

شرط پذیرش: pause/restart بدون گم‌شدن pending approval، کنترل retry و اتصال MCP با مجموعه ابزار محدود در spike موفق شوند. اگر دوام خودکار وسط run لازم شد، هزینه backendهایی مثل Temporal/DBOS واقعی است؛ صرف افزودن Conversation کافی نیست. در هکاتون، افزودن چنین runtime تازه‌ای بدون نیاز اثبات‌شده توصیه نمی‌شود. [PA-U]

### مسیر B — LangGraph + MCP/model adapter محدود + checkpoint پایدار

وقتی انتظار approval، شاخه خطا، resume و نمایش مسیر تصمیم بخش مهم محصول است، graph صریح ارزش بیشتری دارد. nodeهای read، تصمیم، approval و execute از هم جدا؛ حافظه RAM برای restart کافی نیست. LangGraph الزام به استفاده از کل LangChain agent API ندارد، اما dependencyهای اکوسیستم خود را دارد. [LG][LG-P][LC-C]

شرط پذیرش: تیم بتواند مرز node و side effect را درست طراحی کند و dependency MCP سازگار pin شود. graph بیشتر، agent بیشتر یا LangSmith cloud شرط موفقیت نیست. در هر دو مسیر، مدل و کیفیت tool calling/structured output آن باید جدا ارزیابی شود؛ انتخاب Framework برتری AI یا سود را تضمین نمی‌کند.

## ۷. آزمایش کوتاه بعدی؛ هنوز اجرا نشده

هر دو مسیر با **همان مدل، schema، tool allowlist و fixture** مقایسه شوند؛ Fakeها برای صحت orchestration و یک خواندن عمومی مجاز برای transport، نه تست خصوصی:

| آزمون | معیار عبور |
|---|---|
| اتصال عمومی + یک چرخه چندمرحله‌ای | initialize/list و read عمومی شناخته‌شده؛ tool/result درست به نوبت بعد مدل برسد؛ loop سقف داشته باشد |
| قرارداد خروجی | intent خارج schema یا candidate منقضی رد شود؛ schema ضعیف Wallex validator مالی را دور نزند |
| pause/resume و restart | approval جعلی/تکراری/منقضی رد؛ پس از resume شرط ریسک تازه بررسی شود |
| خطا در بدترین نقطه | fixture «اثر انجام شده، پاسخ گم شده» به UNKNOWN/reconcile برسد؛ صفر submit تکراری |
| ماتریس ۹ حالت | Backtest و Shadow در هر سه اختیار **صفر write واقعی**؛ context/run/storage بین محیط‌ها مخلوط نشود |
| replay و پایداری | بدون شبکه همان تصمیم/رویدادهای recorded بازسازی شود؛ اتصال‌ها بسته/بازیابی شوند و log کافی برای تشخیص خطا بماند |

وجود تست upstream شاهد قابلیت تست‌پذیری است، نه قبولی این پروژه. latency انتهابه‌انتها، budget مدل، وضعیت runtime تیم، سازگاری نهایی dependencyها و قواعد Autonomous همچنان نیاز بررسی‌اند؛ فعلاً سؤال تکمیلی یا کلید درخواست نمی‌شود.

## ۸. نسخه و منابع

آخرین commit فقط نسخه مطالعه است؛ نسخه پایدار پیشنهادی نصب یا نتیجه عبور CI نیست. snapshot سورس‌های اصلی زیر و مجوز MIT آن‌ها بررسی شد؛ شرایط dependency/exporter/سرویس hosted جداگانه‌اند. قابلیت‌های beta و تفاوت APIهای نسل‌های قبل عمداً در مقایسه لحاظ شدند.

| مخزن رسمی | commit بررسی‌شده | زمان commit (UTC) | مجوز هسته |
|---|---|---|---|
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph/tree/3cd57c038ad827d05f80c6488adea9994156c58c) | `3cd57c038ad8` | 2026-10-21T12:22:40Z | MIT |
| [langchain-ai/langchain](https://github.com/langchain-ai/langchain/tree/6908e6775cb6f9e9a1bb6a13e2f0322cdff12a3c) | `6908e6775cb6` | 2026-10-22T04:22:40Z | MIT |
| [langchain-ai/langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters/tree/52a4535f3eb4b98f386836e4d9b8c4cadf99afca) | `52a4535f3eb4` | 2026-09-11T05:21:45Z | MIT |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai/tree/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9) | `7e0d7b32e0ba` | 2026-10-22T03:43:41Z | MIT |
| [openai/openai-agents-python](https://github.com/openai/openai-agents-python/tree/841dfdd938bfd4ca052475eb76d6245a356868e0) | `841dfdd938bf` | 2026-10-22T00:26:59Z | MIT |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk/tree/2af9e3089ac66b35c1550c500aae86a82efb1e17) | `2af9e3089ac6` | 2026-10-17T13:54:35Z | MIT |

منابع زیر همگی در **2026-10-22** دسترسی/بازخوانی شدند؛ لینک کدها به SHA ثابت است. ارجاع Phase 01، شواهد همان فاز است و فراخوانی جدید خصوصی محسوب نمی‌شود.

| ID | عنوان و منبع | نوع شواهد |
|---|---|---|
| B | [Brief رسمی ارائه‌شده — متن کامل](../phase-01/evidence/BRIEF_PROVIDED_FA.md) | ورودی رسمی مسابقه |
| PH | [Phase 01 — Handoff و قیود معماری](../phase-01/HANDOFF_TO_ARCHITECT.md) | تحقیق قبلی؛ پیشنهادها الزام مسابقه نیستند |
| PH-AI | [Phase 01 — Backtest / Shadow / AI](../phase-01/BACKTEST_AND_AI_RESEARCH_FA.md) | تحقیق قبلی |
| W | [Wallex — صفحه رسمی MCP](https://wallex.ir/landing/mcp/) | مستند/سورس رسمی |
| W-RULES | [Wallex — canonical sources and MCP access rules](https://api.wallex.ir/llm.txt) | مستند/سورس رسمی |
| SCHEMA | [Phase 01 — schema ثبت‌شده ۵۲ ابزار Wallex](../phase-01/evidence/mcp_tool_schemas.json) | مشاهده عمومی قبلی، بدون اجرای خصوصی |
| LG | [LangGraph — Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | مستند/سورس رسمی |
| LG-P | [LangGraph — Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | مستند/سورس رسمی |
| LG-C | [LangGraph — interrupt و semantics اجرای مجدد node](https://github.com/langchain-ai/langgraph/blob/3cd57c038ad827d05f80c6488adea9994156c58c/libs/langgraph/langgraph/types.py) | سورس/Docs ثابت‌شده با SHA |
| LG-T | [LangGraph — تست‌های interrupt/state/resume](https://github.com/langchain-ai/langgraph/blob/3cd57c038ad827d05f80c6488adea9994156c58c/libs/langgraph/tests/test_interrupt_state.py) | سورس/Docs ثابت‌شده با SHA |
| LC | [LangChain — MCPAdapter و وضعیت beta](https://docs.langchain.com/oss/python/langchain/mcp) | مستند/سورس رسمی |
| LC-C | [LangChain — create_agent روی StateGraph](https://github.com/langchain-ai/langchain/blob/6908e6775cb6f9e9a1bb6a13e2f0322cdff12a3c/libs/langchain_v1/langchain/agents/factory.py) | سورس/Docs ثابت‌شده با SHA |
| LC-H | [LangChain — HumanInTheLoopMiddleware](https://github.com/langchain-ai/langchain/blob/6908e6775cb6f9e9a1bb6a13e2f0322cdff12a3c/libs/langchain_v1/langchain/agents/middleware/human_in_the_loop.py) | سورس/Docs ثابت‌شده با SHA |
| LC-A | [langchain-mcp-adapters — نسخه و dependency constraints](https://github.com/langchain-ai/langchain-mcp-adapters/blob/52a4535f3eb4b98f386836e4d9b8c4cadf99afca/pyproject.toml) | سورس/Docs ثابت‌شده با SHA |
| PA-M | [PydanticAI — MCPToolset / Streamable HTTP](https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/mcp/client.md) | سورس/Docs ثابت‌شده با SHA |
| PA-A | [PydanticAI — gate واقعی ApprovalRequiredToolset](https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/pydantic_ai_slim/pydantic_ai/toolsets/approval_required.py) | سورس/Docs ثابت‌شده با SHA |
| PA-D | [PydanticAI — Deferred tools / approval / trust boundary](https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/deferred-tools.md) | سورس/Docs ثابت‌شده با SHA |
| PA-S | [PydanticAI — Conversation persistence و تفاوت durability](https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/persistence.md) | سورس/Docs ثابت‌شده با SHA |
| PA-U | [PydanticAI — durable backends و هزینه زیرساخت](https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/durable_execution/overview.md) | سورس/Docs ثابت‌شده با SHA |
| PA-T | [PydanticAI — TestModel / FunctionModel / منع درخواست مدل واقعی](https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/testing.md) | سورس/Docs ثابت‌شده با SHA |
| PA-E | [PydanticAI — timeouts، MCP request و رفتار sync tool](https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/timeouts.md) | سورس/Docs ثابت‌شده با SHA |
| PA-O | [PydanticAI — structured output modes و validation](https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/output.md) | سورس/Docs ثابت‌شده با SHA |
| OA-M | [OpenAI Agents — Streamable HTTP، approval، filters و retries](https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/src/agents/mcp/server.py) | سورس/Docs ثابت‌شده با SHA |
| OA-H | [OpenAI Agents — approval و RunState durable serialization](https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/human_in_the_loop.md) | سورس/Docs ثابت‌شده با SHA |
| OA-S | [OpenAI Agents — Session history و SQLiteSession](https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/sessions/index.md) | سورس/Docs ثابت‌شده با SHA |
| OA-G | [OpenAI Agents — guardrail boundaries و parallel input checks](https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/guardrails.md) | سورس/Docs ثابت‌شده با SHA |
| OA-T | [OpenAI Agents — ScriptedModel و تست‌های بدون provider](https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/testing.md) | سورس/Docs ثابت‌شده با SHA |
| OA-OBS | [OpenAI Agents — tracing، exporters و sensitive data](https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/tracing.md) | سورس/Docs ثابت‌شده با SHA |
| OA-P | [OpenAI Agents — model/provider interfaces و تفاوت feature support](https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/models/index.md) | سورس/Docs ثابت‌شده با SHA |
| MC | [MCP Python SDK — Streamable HTTP transport](https://github.com/modelcontextprotocol/python-sdk/blob/2af9e3089ac66b35c1550c500aae86a82efb1e17/src/mcp/client/streamable_http.py) | سورس/Docs ثابت‌شده با SHA |
| MC-V | [MCP Python SDK — v1/v2 migration](https://github.com/modelcontextprotocol/python-sdk/blob/2af9e3089ac66b35c1550c500aae86a82efb1e17/docs/migration.md) | سورس/Docs ثابت‌شده با SHA |

| LC-OBS | [LangChain — LangSmith Observability](https://docs.langchain.com/oss/python/langchain/observability) | مستند رسمی؛ tracing اختیاری |
| PA-OBS | [PydanticAI — Observability / Logfire](https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/logfire.md) | Docs ثابت‌شده با SHA |

[B]: ../phase-01/evidence/BRIEF_PROVIDED_FA.md "Brief رسمی ارائه‌شده — متن کامل؛ دسترسی 2026-10-22"
[PH]: ../phase-01/HANDOFF_TO_ARCHITECT.md "Phase 01 — Handoff و قیود معماری؛ دسترسی 2026-10-22"
[PH-AI]: ../phase-01/BACKTEST_AND_AI_RESEARCH_FA.md "Phase 01 — Backtest / Shadow / AI؛ دسترسی 2026-10-22"
[W]: https://wallex.ir/landing/mcp/ "Wallex — صفحه رسمی MCP؛ دسترسی 2026-10-22"
[W-RULES]: https://api.wallex.ir/llm.txt "Wallex — canonical sources and MCP access rules؛ دسترسی 2026-10-22"
[SCHEMA]: ../phase-01/evidence/mcp_tool_schemas.json "Phase 01 — schema ثبت‌شده ۵۲ ابزار Wallex؛ دسترسی 2026-10-22"
[LG]: https://docs.langchain.com/oss/python/langgraph/interrupts "LangGraph — Interrupts؛ دسترسی 2026-10-22"
[LG-P]: https://docs.langchain.com/oss/python/langgraph/persistence "LangGraph — Persistence؛ دسترسی 2026-10-22"
[LG-C]: https://github.com/langchain-ai/langgraph/blob/3cd57c038ad827d05f80c6488adea9994156c58c/libs/langgraph/langgraph/types.py "LangGraph — interrupt و semantics اجرای مجدد node؛ دسترسی 2026-10-22"
[LG-T]: https://github.com/langchain-ai/langgraph/blob/3cd57c038ad827d05f80c6488adea9994156c58c/libs/langgraph/tests/test_interrupt_state.py "LangGraph — تست‌های interrupt/state/resume؛ دسترسی 2026-10-22"
[LC]: https://docs.langchain.com/oss/python/langchain/mcp "LangChain — MCPAdapter و وضعیت beta؛ دسترسی 2026-10-22"
[LC-C]: https://github.com/langchain-ai/langchain/blob/6908e6775cb6f9e9a1bb6a13e2f0322cdff12a3c/libs/langchain_v1/langchain/agents/factory.py "LangChain — create_agent روی StateGraph؛ دسترسی 2026-10-22"
[LC-H]: https://github.com/langchain-ai/langchain/blob/6908e6775cb6f9e9a1bb6a13e2f0322cdff12a3c/libs/langchain_v1/langchain/agents/middleware/human_in_the_loop.py "LangChain — HumanInTheLoopMiddleware؛ دسترسی 2026-10-22"
[LC-A]: https://github.com/langchain-ai/langchain-mcp-adapters/blob/52a4535f3eb4b98f386836e4d9b8c4cadf99afca/pyproject.toml "langchain-mcp-adapters — نسخه و dependency constraints؛ دسترسی 2026-10-22"
[PA-M]: https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/mcp/client.md "PydanticAI — MCPToolset / Streamable HTTP؛ دسترسی 2026-10-22"
[PA-A]: https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/pydantic_ai_slim/pydantic_ai/toolsets/approval_required.py "PydanticAI — gate واقعی ApprovalRequiredToolset؛ دسترسی 2026-10-22"
[PA-D]: https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/deferred-tools.md "PydanticAI — Deferred tools / approval / trust boundary؛ دسترسی 2026-10-22"
[PA-S]: https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/persistence.md "PydanticAI — Conversation persistence و تفاوت durability؛ دسترسی 2026-10-22"
[PA-U]: https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/durable_execution/overview.md "PydanticAI — durable backends و هزینه زیرساخت؛ دسترسی 2026-10-22"
[PA-T]: https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/testing.md "PydanticAI — TestModel / FunctionModel / منع درخواست مدل واقعی؛ دسترسی 2026-10-22"
[PA-E]: https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/timeouts.md "PydanticAI — timeouts، MCP request و رفتار sync tool؛ دسترسی 2026-10-22"
[PA-O]: https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/output.md "PydanticAI — structured output modes و validation؛ دسترسی 2026-10-22"
[OA-M]: https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/src/agents/mcp/server.py "OpenAI Agents — Streamable HTTP، approval، filters و retries؛ دسترسی 2026-10-22"
[OA-H]: https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/human_in_the_loop.md "OpenAI Agents — approval و RunState durable serialization؛ دسترسی 2026-10-22"
[OA-S]: https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/sessions/index.md "OpenAI Agents — Session history و SQLiteSession؛ دسترسی 2026-10-22"
[OA-G]: https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/guardrails.md "OpenAI Agents — guardrail boundaries و parallel input checks؛ دسترسی 2026-10-22"
[OA-T]: https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/testing.md "OpenAI Agents — ScriptedModel و تست‌های بدون provider؛ دسترسی 2026-10-22"
[OA-OBS]: https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/tracing.md "OpenAI Agents — tracing، exporters و sensitive data؛ دسترسی 2026-10-22"
[OA-P]: https://github.com/openai/openai-agents-python/blob/841dfdd938bfd4ca052475eb76d6245a356868e0/docs/models/index.md "OpenAI Agents — model/provider interfaces و تفاوت feature support؛ دسترسی 2026-10-22"
[MC]: https://github.com/modelcontextprotocol/python-sdk/blob/2af9e3089ac66b35c1550c500aae86a82efb1e17/src/mcp/client/streamable_http.py "MCP Python SDK — Streamable HTTP transport؛ دسترسی 2026-10-22"
[MC-V]: https://github.com/modelcontextprotocol/python-sdk/blob/2af9e3089ac66b35c1550c500aae86a82efb1e17/docs/migration.md "MCP Python SDK — v1/v2 migration؛ دسترسی 2026-10-22"

[LC-OBS]: https://docs.langchain.com/oss/python/langchain/observability "LangChain — LangSmith Observability؛ دسترسی 2026-10-22"
[PA-OBS]: https://github.com/pydantic/pydantic-ai/blob/7e0d7b32e0ba0d3b8bebc50cfcf6bf756402c5b9/docs/logfire.md "PydanticAI — Observability / Logfire؛ دسترسی 2026-10-22"
