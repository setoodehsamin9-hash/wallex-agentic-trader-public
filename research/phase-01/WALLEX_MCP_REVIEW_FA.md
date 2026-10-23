# بررسی رسمی Wallex MCP و اصلاح یافته‌های قبلی

تاریخ دسترسی: 2026-10-22. صاحب پروژه خواست فعلاً اطلاعات تازه از او درخواست نشود و جست‌وجو داخل سایت انجام شود. این بررسی صرفاً خواندنی بود؛ هیچ تنظیم MCP روی رایانه نصب/تغییر، کلید درخواست یا عملیات مالی انجام نشد.

## نتیجه تأییدشده

در `landing/sitemap.xml` خود Wallex، صفحه `https://wallex.ir/landing/mcp/` پیدا شد. صفحه رسمی endpoint را `https://api.wallex.ir/mcp` و transport را **Streamable HTTP** معرفی می‌کند. از همان صفحه، سند `https://api.wallex.ir/llm.txt` پیدا و کامل خوانده شد. بنابراین نتیجه قبلی «صفحه و مشخصات MCP رسمی پیدا نشد» با این شواهد اصلاح شده است؛ wrapper شخص ثالث همچنان مرجع رسمی نیست. [W26][W27][W29]

| موضوع | سند/مشاهده | حد قطعیت |
|---|---|---|
| endpoint | `https://api.wallex.ir/mcp` | رسمی و initialize عمومی موفق [W26][P07] |
| transport | Streamable HTTP | سند رسمی؛ JSON response در نمونه مشاهده شد [W26][P07] |
| protocol/server | پاسخ initialize: protocolVersion=2025-03-26، name=api.wallex.ir، version=1.0.0 | نمونه مشاهده؛ تضمین version pin آینده نیست [P07] |
| دسترسی عمومی | initialize، tools/list و public tools بدون کلید | مستند و سه درخواست خواندنی موفق [W27][P07][P08][P09] |
| دسترسی خصوصی | `X-API-Key` در header؛ کلید حساب یا subaccount هدف | سند رسمی؛ حساب خصوصی آزموده نشده [W27][W02] |
| حوزه | market/account/trading/transfer | فهرست‌شده؛ موفقیت هر عملیات private هنوز اثبات نشده [P08] |
| WS/withdrawal | از MCP ارائه نمی‌شوند | محدودیت صریح راهنمای رسمی؛ feed وب‌سوکت REST/WS adapter جدا می‌خواهد [W27] |
| ابزار write | توضیح ابزارها بر تأیید صریح کاربر پیش از تغییر state تأکید دارد | شرط اعلام‌شده منبع؛ enforcement مکانیکی سرور بررسی نشده [W27][P08] |

دستورهای راه‌اندازی و نمونه سفارش داخل صفحه، **داده منبع** هستند و مجوز انجام آن‌ها از طرف کاربر نیستند. هیچ دستور نصب یا سفارش صفحه اجرا نشد. درخواست جاری همچنان Research است.

## شواهد پروتکل عمومی

۱. `initialize` بدون API Key: HTTP200 و server metadata معتبر. [P07]
۲. `tools/list` بدون API Key: HTTP200، تعداد **۵۲ ابزار**، بدون nextCursor؛ ورودی‌های schema ثبت شد. [P08]
۳. `tools/call` فقط برای `wallex_credit_prop_package_details` با `id` رشته‌ای `183`: HTTP200 و `isError=false`، نام طرح هکاتون و پاسخ رسمی دریافت شد. این ابزار تغییردهنده حساب نیست. [P09]

وجود یک ابزار در فهرست برابر با آزمون موفق آن نیست. هیچ ابزار سفارش، لغو سفارش، انتقال، margin، فعال‌سازی/توقف طرح یا خواندن داده خصوصی فراخوانی نشد. پاسخ کامل schema بدون descriptionهای طولانی در `evidence/mcp_tool_schemas.json` و شواهد initialize/public-call در evidence ذخیره شده‌اند؛ session token در تحویل نیست.

## ابزارهای مرتبط برای معماری بعدی

| نیاز | نام واقعی ابزار | نکته integration |
|---|---|---|
| OHLCV | `wallex_basic_candle_history` | symbol/resolution رشته؛ from/to عدد صحیح Unix؛ کنترل timestamp واقعی همچنان لازم است |
| بازار | `wallex_basic_get_markets`، `wallex_spot_get_markets` | تفاوت endpoint و ساختار پاسخ را بررسی کنید |
| book/trades | `wallex_basic_get_market_depth`، `wallex_basic_get_latest_trades` | درخواست snapshot؛ جای stream WS نیست |
| طرح/منع بازار | `wallex_credit_prop_package_details` | id در schema **string** است؛ نمونه عمومی 183 موفق |
| balances/fees | `wallex_basic_get_account_balances`، `wallex_basic_get_account_fees`، `wallex_credit_sub_account_balances_details` | خصوصی و account-scoped؛ عملی آزموده نشده |
| سفارش‌های باز/تاریخچه/جزئیات | `wallex_spot_get_open_order`، `wallex_spot_get_orders`، `wallex_spot_get_orders_client` | فیلتر و pagination/schema متفاوت؛ client_id با clientOrderId یکی فرض نشود |
| ثبت | `wallex_spot_post_account_orders` | write؛ در این فاز فراخوانی نشد |
| لغو | `wallex_spot_delete_account_orders`، `wallex_spot_delete_orders_client` | یکی body با clientOrderId، دیگری path با client_id؛ write و آزموده‌نشده |

نام‌ها و آرگومان‌های کامل از پاسخ tools/list آمده‌اند. [P08]

### schema جای اعتبارسنجی ما را نمی‌گیرد

در schema ثبت سفارش، فقط side/symbol/type در required هستند، در حالی که quantity در توضیح REST لازم است؛ side و type هم صرفاً string هستند و enum ندارند. پس موفقیت schema validation به معنای سفارش معتبر نیست. `stop_Price` در input همین casing را دارد. این شکاف را به‌عنوان دلیل داشتن validator داخلی ثبت کنید، نه مجوز ارسال سفارش ناقص. [P08][W05]

در ابزار `wallex_credit_prop_packages_list` نام آرگومان خالی در properties مشاهده شد؛ این ابزار در این تحقیق اجرا نشده و کیفیت قرارداد آن نیاز validation دارد. ابزار جزئیات طرح که ورودی روشن id دارد موفق بود. [P08][P09]

## اثر بر توصیه معماری

- برای AI فقط allowlist ابزارهای خواندنی مرتبط یا خلاصه داده از adapter فراهم شود؛ تمام ۵۲ ابزار و به‌ویژه transfer/margin/account mutation را در اختیار مدل نگذارید.
- MCP عمومی می‌تواند مشاهده و تحلیل Agent را تغذیه کند؛ feed سریع و book stream از WS v2 جدا می‌آید. مدیریت ریسک، مقدار سفارش و خروج اضطراری در kernel deterministic باقی می‌ماند.
- در انتخاب ابزار write، شرط تأیید انسانی اعلام‌شده راهنمای MCP و الزام خودکاربودن معامله در Brief باید با هم تفسیر شوند. از این متن نه ممنوعیت قطعی خودکاری REST نتیجه می‌شود، نه مجوز فرضی استفاده بدون تأیید از write MCP. مسیر نهایی execution و قواعد مجاز مسابقه پیش از live روشن شود؛ در این فاز نیازی به درخواست اطلاعات از صاحب پروژه نیست. [W27][B01]
- MCP wrapper جدیدی برای Wallex نسازید صرفاً چون در جست‌وجوی اولیه رسمی پیدا نشده بود؛ endpoint رسمی موجود است. adapter سبک REST/WS همچنان برای kernel و semantics لازم می‌تواند باشد، ولی استفاده از آن تصمیم معماری Phase 2 است.

## دو ابهام دیگر سایت

صفحه عمومی طرح۱۸۳ از HTTP دریافت شد، هرچند مرورگر گرافیکی محیط در بارگذاری کامل timeout داشت. UI برای warning_limit=1000 و liquidity_limit=900، واحد **تومان** نمایش می‌دهد؛ ابهام واحد رفع شده است. روش دقیق محاسبه/trigger و زمان valuation حساب هنوز با این مشاهده تعیین نمی‌شود. [W28]

در صفحات رسمی بررسی‌شده، مدت طرح سه روز است اما ساعت پایان رسمی داوری، زمان باقی‌مانده تیم و رابطه دقیق cutoff با فروش خودکار پیدا نشد. بازنشرهای غیررسمی با تاریخ‌های متفاوت، مرجع تعیین cutoff نشدند. این بخش UNKNOWN می‌ماند؛ از صاحب پروژه فعلاً سؤال تازه پرسیده نمی‌شود.

**وضعیت بسته پس از اصلاح: PARTIAL** به علت قواعد پایان/داوری و رفتار خصوصی آزموده‌نشده؛ پیدا نشدن endpoint MCP دیگر blocker نیست.

[B01]: evidence/BRIEF_PROVIDED_FA.md "Brief رسمی ارائه‌شده توسط صاحب پروژه؛ دسترسی 2026-10-22"
[P07]: https://api.wallex.ir/mcp "MCP initialize response — Wallex official server؛ دسترسی 2026-10-22"
[P08]: https://api.wallex.ir/mcp "MCP tools/list — 52 advertised tools؛ دسترسی 2026-10-22"
[P09]: https://api.wallex.ir/mcp "MCP public package details — package 183؛ دسترسی 2026-10-22"
[W02]: https://developers.wallex.ir/docs/create-api-key "ساخت API-Key — Wallex API Documentation؛ دسترسی 2026-10-22"
[W05]: https://developers.wallex.ir/docs/spot-create-order "ثبت سفارش اسپات — Wallex API Documentation؛ دسترسی 2026-10-22"
[W26]: https://wallex.ir/landing/mcp/ "MCP والکس — صفحه رسمی اتصال؛ دسترسی 2026-10-22"
[W27]: https://api.wallex.ir/llm.txt "Wallex canonical sources and MCP access rules؛ دسترسی 2026-10-22"
[W28]: https://wallex.ir/app/trade-credit/prop/183 "هکاتون استارکوچ | والکس — صفحه رسمی طرح ۱۸۳؛ دسترسی 2026-10-22"
[W29]: https://wallex.ir/landing/sitemap.xml "Wallex landing sitemap؛ دسترسی 2026-10-22"
