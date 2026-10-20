# ⚡ Lead Responder – מענה מיידי ללידים בוואטסאפ

שרת אחד שמשרת את כל הלקוחות שלך. כל עסק שמתחבר מקבל:

1. **הודעת וואטסאפ ללקוח תוך שניות** מרגע שהוא משאיר פרטים (טופס באתר, פייסבוק/אינסטגרם, Make/Zapier).
2. **התראה לבעל העסק** (טלגרם ו/או וואטסאפ) עם כל הפרטים, כפתור "💬 וואטסאפ ללקוח" וכפתור "✅ סמן כטופל".
3. **תזכורות** לבעל העסק כל עוד הליד לא סומן כטופל (ברירת מחדל: אחרי 15 ו-60 דק').
4. **Follow-up אוטומטי** ללקוח אחרי 24 שעות אם אף אחד לא טיפל בו ולא ענה.
5. **תשובות של הלקוח בוואטסאפ מועברות לבעל העסק**, ועוצרות את ה-follow-up.
6. **Google Sheet** כ-CRM פשוט: כל ליד שורה, הסטטוס מתעדכן לבד.
7. **דוח שבועי** ביום ראשון ב-09:00: כמה לידים, כמה טופלו, זמן טיפול חציוני, מי עדיין פתוח.
8. בלי הודעות בלילה ובשבת (תזכורות ו-follow-up מחכים. התגובה המיידית יוצאת תמיד).

```
⚡ ליד חדש · מיכל אברהם
📞 052-7654321
📣 אתר
• איזה טיפול מעניין אותך?: בוטוקס
✅ נשלחה ללקוח הודעת וואטסאפ אוטומטית
🏷 הקליניקה של דנה
[💬 וואטסאפ ללקוח]  [✅ סמן כטופל]
```

לתוכנית העסקית (למי למכור, איך, כמה זה עולה לך): [PLAN.md](PLAN.md).

## איך זה עובד

```
טופס / פייסבוק / Make ──POST──▶ /c/<לקוח>/lead ──▶ SQLite: ליד + תור משימות
                                                        │
                    worker (שרשור ברקע, מתעורר מיד) ◀───┘
                      ├─ ack       → וואטסאפ ללקוח (תבנית מאושרת)
                      ├─ notify    → טלגרם/וואטסאפ לבעל העסק
                      ├─ remind    → תזכורת אם לא סומן "טופל"     (מכבד שעות שקט ושבת)
                      ├─ followup  → וואטסאפ שני ללקוח אחרי 24 ש' (מכבד שעות שקט ושבת)
                      └─ sheet     → שורה ב-Google Sheet
Meta ──POST──▶ /c/<לקוח>/meta : תשובות של לקוחות, "ההודעה לא נמסרה", Lead Ads
בעל העסק ──▶ /d/<token> : דף קטן עם "✅ טופל" / "🚫 לא רלוונטי" (עוצר תזכורות)
```

- כל הודעה יוצאת היא **משימה ב-DB**, אז שום דבר לא הולך לאיבוד בהפעלה מחדש, וכשל זמני מנוסה שוב
  (אחרי 1, 5, 15, 60 דק'). אחרי 5 כשלונות, או כשל קבוע (תבנית שנדחתה, טוקן שפג), בעל העסק מקבל
  "⚠️ ההודעה לא נמסרה, כדאי להתקשר", ו**אתה** (המפעיל) מקבל התראה בטלגרם.
- אותו מספר טלפון שוב תוך 24 שעות: בעל העסק מקבל "🔁 פנייה חוזרת", והלקוח לא מקבל הודעה כפולה.
- יותר מ-30 לידים בשעה ללקוח אחד (בוט שמציף טופס): נשמר, לא נשלח כלום, ואתה מקבל התראה.
  זה מגן על הלקוח מחשבון וואטסאפ מנופח.
- מספר קווי (03/04/08/09...) לא מקבל וואטסאפ, ובעל העסק רואה "☎️ מספר קווי, צריך להתקשר".

## התקנה מקומית (5 דקות)

צריך Python 3.11 ומעלה.

```bash
cd lead-responder
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.toml config.toml     # למלא secret ו-verify_token
cp examples/client.example.toml clients/demo.toml
python -m lead_responder check         # בודק הכל ומדפיס את כתובות ה-webhook של כל לקוח
python -m lead_responder test-lead demo --phone 0501234567 --dry-run   # מדפיס מה היה נשלח
python -m lead_responder serve --dry-run                               # שרת אמיתי, הודעות מודפסות
```

ערכים אקראיים ל-secret / key:
`python -c "import secrets; print(secrets.token_urlsafe(24))"`

כדי ש-Meta יוכל לשלוח webhooks למחשב שלך בזמן פיתוח: `cloudflared tunnel --url http://localhost:8080`
נותן כתובת https זמנית. שמים אותה ב-`public_url`.

## הקמה – פעם אחת אצלך

### 1. בוט טלגרם (להתראות, חינם)

1. בטלגרם: **@BotFather** → `/newbot` → מקבלים token → `[telegram] bot_token` ב-`config.toml`.
2. בוט אחד משרת את כל הלקוחות. כל בעל עסק פותח את הבוט ולוחץ Start, ואז:
   ```bash
   python -m lead_responder telegram-chats      # מציג chat id של מי שכתב לבוט
   ```
3. ה-chat id שלך הולך ל-`[admin] telegram_chat_id`. שם תקבל התראות על תקלות אצל כל הלקוחות.

### 2. שרת (Docker + HTTPS אוטומטי)

כל VPS קטן מספיק לעשרות לקוחות (למשל Hetzner CX22, בערך 5$ לחודש).

```bash
git clone https://github.com/omrisali054/Liranfix-.git && cd Liranfix-/lead-responder
mkdir -p data/clients
cp config.example.toml data/config.toml            # public_url = https://leads.הדומיין-שלך
nano deploy/Caddyfile                              # אותו שם דומיין
docker compose up -d --build
docker compose exec app python -m lead_responder -c /data/config.toml check
```

רשומת DNS מסוג A של הדומיין צריכה להצביע על השרת. Caddy מוציא תעודת HTTPS לבד.
מומלץ גם ניטור חינמי (UptimeRobot) על `https://leads.../health`, וגיבוי יומי של `data/leads.db`.

בלי Docker: [`deploy/lead-responder.service`](deploy/lead-responder.service) (systemd), ומאחוריו Caddy/nginx.

## הוספת לקוח חדש (בערך שעתיים, רובן המתנה לאישור תבניות)

**מה לבקש מהלקוח:** גישת admin ל-Meta Business שלו, מספר טלפון לבוט (מומלץ SIM חדש וייעודי),
גישה לטופס באתר, ושבעל העסק ילחץ Start בבוט הטלגרם.

1. `cp examples/client.example.toml data/clients/<שם-הלקוח>.toml`, וממלאים `business_name`, `key` אקראי.
2. **WhatsApp Cloud API** (בחשבון ה-Business **של הלקוח**, כך שהחשבון והמספר שייכים לו):
   1. [developers.facebook.com](https://developers.facebook.com) → Create App → Business → מוסיפים את המוצר WhatsApp.
   2. WhatsApp → API Setup → Add phone number → המספר הייעודי (אימות ב-SMS).
      מספר שכבר פעיל באפליקציית WhatsApp Business צריך מעבר, או Coexistence (ראו "מגבלות").
   3. מעתיקים את `Phone number ID` ל-`[whatsapp] phone_number_id`.
   4. Business Settings → Users → **System users** → Add (Admin) → Assign assets: האפליקציה ו-WhatsApp Account →
      Generate token עם ההרשאות `whatsapp_business_messaging`, `whatsapp_business_management`, ותוקף **Never**
      → `[whatsapp] access_token`.
   5. App settings → Basic → **App secret** → `[meta] app_secret`.
   6. WhatsApp → Configuration → Webhook: Callback URL = `https://leads.../c/<שם-הלקוח>/meta`,
      Verify token = ה-`verify_token` מ-`config.toml` → Verify → Subscribe לשדה **messages**.
   7. מוסיפים אמצעי תשלום ב-WhatsApp Manager (Meta גובה לפי הודעה, ראו PLAN.md).
3. **תבניות** (WhatsApp Manager → Message templates → Create, שפה: Hebrew). הטקסטים בהמשך.
   אישור לוקח בדרך כלל בין כמה דקות ל-24 שעות.
4. **חיבור הטופס**: לפי המקור, [למטה](#חיבור-מקורות-לידים). את הכתובת המדויקת מדפיס `check`.
5. אופציונלי: **Google Sheet** לפי ההוראות בראש [`apps-script/sheet-webhook.gs`](apps-script/sheet-webhook.gs).
6. `check` → `docker compose restart app` →
   `test-lead <שם-הלקוח> --phone <הנייד-של-בעל-העסק>`. אמורה להגיע הודעה לנייד והתראה לבעל העסק.
7. ממלאים את הטופס האמיתי באתר של הלקוח, ומוודאים שהכל הגיע.

### טקסטים לתבניות

**`lead_ack`**, קטגוריה **Utility** (פרמטרים: `ack_params = ["{first_name}", "{business}"]`):
```
היי {{1}}, תודה שפנית ל{{2}}! 🙏
קיבלנו את הפרטים שלך ונחזור אליך בהקדם.
אפשר לכתוב לנו כאן מה הכי חשוב לך לדעת, וזה יגיע ישר אלינו.
```

**`lead_followup`** (פרמטרים: `followup_params = ["{first_name}", "{business}"]`). Meta עשויים לסווג אותה כ-Marketing, וזה בסדר:
```
היי {{1}}, ניסינו לחזור אליך בעקבות הפנייה ל{{2}}.
מתי נוח לך שנדבר? אפשר פשוט לענות להודעה הזו.
להסרה השיבו "הסר".
```

**`owner_lead_alert`** (רק אם בעל העסק מעדיף התראות בוואטסאפ על פני טלגרם), קטגוריה Utility.
הפרמטרים קבועים בקוד. מוסיפים כפתור **Visit website** מסוג **Dynamic** עם הכתובת
`https://leads.הדומיין-שלך/d/{{1}}` והטקסט "סמן כטופל":
```
🔔 {{1}} במערכת הלידים
שם: {{2}}
טלפון: {{3}}
פרטים: {{4}}

אחרי שטיפלתם, לחצו על הכפתור כדי לעצור תזכורות.
```
ואז בקובץ הלקוח: `owner_template = "owner_lead_alert"` ו-`[owner] whatsapp_numbers = ["05..."]`.
הדוח השבועי נשלח רק בטלגרם.

אפשר לשנות את הטקסטים כרצונך. רק לשמור שמספר ה-`{{n}}` יתאים לרשימת הפרמטרים בקונפיג. משתנים
זמינים: `{name} {first_name} {phone} {email} {source} {summary} {business} {booking_url}`.

## חיבור מקורות לידים

הכתובת של כל לקוח (מ-`check`): `https://leads.../c/<לקוח>/lead?key=<key>`.
אפשר להוסיף `&source=facebook` / `&source=website` כדי שהמקור יופיע בהתראה ובדוח.
השרת מזהה לבד שדות כמו name / phone / email / שם / טלפון / נייד / אימייל (גם `fields[...]`
של Elementor, JSON מקונן, ו-`field_data` של פייסבוק). כל שאר השדות מוצגים לבעל העסק כמו שהם.
אם תווית לא מזוהה, מוסיפים אותה ל-`[fields]` בקובץ הלקוח.

| מקור | איך |
|---|---|
| **Elementor Pro** | עריכת הטופס → Actions After Submit → Webhook → מדביקים את הכתובת + `&source=website` |
| **WordPress, Contact Form 7** | תוסף "CF7 to Webhook" → אותה כתובת |
| **Wix** | Automations → Trigger: Form submitted → Action: Send HTTP request (POST, JSON) |
| **Facebook / Instagram Lead Ads** (הכי פשוט) | [Make.com](https://www.make.com) (התוכנית החינמית מספיקה): Facebook Lead Ads → Watch Leads → HTTP → Make a request, POST, JSON: `{"name": "...", "phone": "...", "email": "..."}` + `&source=facebook` |
| **Facebook Lead Ads ישירות** | `[meta] page_access_token` + מנוי של הדף לשדה `leadgen` באפליקציה. דורש הרשאת `leads_retrieval`, ולכן Make מהיר יותר להקמה |
| **כל דבר אחר** | Zapier / Make / קוד: POST של JSON או form לכתובת |
| **הודעה ישירה למספר הבוט** | נהיית ליד בעצמה (מקור: וואטסאפ) |

## פקודות

```bash
python -m lead_responder check                         # בדיקת קונפיג + כתובות webhook לכל לקוח
python -m lead_responder serve [--dry-run]             # השרת
python -m lead_responder test-lead <לקוח> --phone 05.. [--dry-run]
python -m lead_responder telegram-chats [<לקוח>]       # chat ids של מי שכתב לבוט
python -m lead_responder leads <לקוח> [-n 50]          # לידים אחרונים
python -m lead_responder report <לקוח> [--send]        # הדוח השבועי עכשיו
python -m lead_responder failed                        # הודעות שלא הצליחו להישלח
```

בדוקר: `docker compose exec app python -m lead_responder -c /data/config.toml <פקודה>`.

## כללים (בקובץ הלקוח, `[rules]`)

| מפתח | ברירת מחדל | |
|---|---|---|
| `owner_reminder_minutes` | `[15, 60]` | תזכורות לבעל העסק כל עוד לא סומן "טופל" |
| `followup_hours` | `[24]` | follow-up ללקוח אם לא טופל ולא ענה (`[]` = בלי) |
| `dedupe_hours` | `24` | אותו מספר שוב: רק התראה, בלי הודעה כפולה ללקוח |
| `max_leads_per_hour` | `30` | הגנה מהצפה |
| `quiet_hours` | `"21:00-08:00"` | `""` = בלי |
| `shabbat`, `shabbat_start`, `shabbat_end` | `true`, `"15:00"`, `"20:30"` | שישי מ-15:00 עד מוצ"ש 20:30 |
| `weekly_report` | `true` | ראשון 09:00, בטלגרם |

## מגבלות שחשוב לדעת

- **הודעה ראשונה ללקוח חייבת להיות תבנית מאושרת** (כלל של Meta). טקסט חופשי אפשר רק תוך 24 שעות
  מהודעה שהלקוח שלח. לכן ה-ack וה-follow-up הם תבניות.
- **מספר הבוט**: מספר שעובד עם ה-Cloud API לא זמין באפליקציית WhatsApp הרגילה, אלא אם מפעילים את
  Coexistence של Meta. הכי פשוט: מספר ייעודי חדש. כל מה שלקוחות כותבים אליו מועבר לבעל העסק
  (`forward_replies = true`), ובעל העסק חוזר אליהם מהנייד שלו. עם Coexistence כדאי `forward_replies = false`.
- **הסכמה**: לענות למי שהשאיר פרטים זה בסדר. ל-follow-up ולכל דבר שיווקי כדאי להוסיף לטופס תיבת
  "אני מאשר/ת פנייה בוואטסאפ" (חוק התקשורת, סעיף 30א). זה לא ייעוץ משפטי.
- השרת שומר פרטים אישיים של לקוחות הקצה: לשמור על השרת מעודכן, על `data/` מגובה ופרטי, ולא לשתף טוקנים.

## פיתוח

```bash
python -m unittest        # כל הבדיקות רצות בלי רשת
```

```
lead_responder/
  engine.py     intake → תור משימות → worker; תזכורות, follow-up, ניסיונות חוזרים, webhooks של Meta, דוח
  web.py        Flask: /c/<client>/lead, /c/<client>/meta, /d/<token>, /health
  leads.py      זיהוי שם/טלפון/מייל מכל פורמט טופס (Elementor, JSON, Facebook field_data)
  channels.py   WhatsApp Cloud API, טלגרם, Google Sheet, Facebook Lead Ads (+ גרסאות dry-run)
  messages.py   כל הטקסטים בעברית: התראות, דוח שבועי, שורת גיליון
  schedule.py   שעות שקט ושבת
  storage.py    SQLite: לידים, משימות, סימוני "כבר טופל"
  phone.py      050-1234567 → 972501234567
  security.py   קישורי "טופל" חתומים, אימות חתימת Meta
  config.py     config.toml + clients/*.toml
apps-script/    הסקריפט של Google Sheets
deploy/         Caddyfile, systemd
```

ערוץ התראה חדש (למשל SMS או מייל) = מחלקה עם `send(...)` ב-`channels.py`, וחיבור שלה ב-`Engine._tell_owner`.
