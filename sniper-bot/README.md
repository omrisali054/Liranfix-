# 🎯 Sniper Bot – בוט איתור מציאות

סקריפט שרץ ברקע, סורק את **יד2** (ואופציונלית כל לוח עם RSS) ומחפש מחשבים ניידים וטלפונים
עם תקלות שקל לתקן: בעיית טעינה, מסך סדוק, סוללה, לא נדלק וכו'. ברגע שמתפרסמת מודעה מתחת
למחיר הרצפה שקבעת, נשלחת **התראה לטלפון** (ntfy או טלגרם) עם מחיר, תקלה, עיר, תמונה וקישור.

```
🎯 MacBook Pro Retina 15 2015 – 400 ₪
💰 400 ₪ (תקרה: 900 ₪)
🔧 טעינה: מטען תקול | לא נדלק: אינו נדלק | כללי: דרוש תיקון, תקול, לחלקי חילוף, נמכר במצבו
📍 פרדס חנה כרכור · דרוש תיקון/שיפוץ · מחשבים ניידים
🔎 לפטופ לתיקון (yad2)
```

## איך זה עובד

1. כל ~2 דקות הבוט פותח את חיפוש יד2 Market **ממוין מהחדש לישן ועם תקרת מחיר**, וקורא את
   המודעות מה-JSON שמוטמע בדף. לא צריך דפדפן.
2. מודעה חדשה (או מודעה ישנה שהמחיר שלה ירד) עוברת סינון: טווח מחיר, סוג מוצר, מצב, מוכר
   פרטי, ומילים שפוסלות אותה (קונים/מחפשים, נעול ל-iCloud, אביזרים).
3. אם המודעה עברה את הסינון, הבוט פותח אותה וקורא את **התיאור המלא** כדי לזהות תקלות.
   מילת שלילה לפני התקלה מבטלת אותה ("אין בעיית טעינה", "לא תקול"). תחיליות כמו
   "והמסך שבור" מזוהות.
4. התראה נשלחת פעם אחת לכל מודעה, והבוט זוכר הכל ב-SQLite (`sniper.db`).
5. בהרצה הראשונה של כל חיפוש הבוט רק **זוכר** את המודעות הקיימות, כדי לא להציף אותך.
   אחר כך מתריעים רק על חדשות.
6. אם הסריקה נכשלת שוב ושוב (חסימה, קאפצ'ה, אין אינטרנט), הבוט מאריך את ההמתנה ושולח לך
   התראת "⚠️ הבוט לא מצליח לסרוק". כשהסריקה חוזרת לעבוד מגיעה התראת "✅ חזר לעבוד".

## התקנה (5 דקות)

צריך Python 3.11 ומעלה.

```bash
cd sniper-bot
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.toml config.toml # Windows: copy config.example.toml config.toml
```

### חיבור הטלפון – ntfy (הכי פשוט, בלי חשבון)

1. מתקינים את האפליקציה **ntfy** ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iPhone](https://apps.apple.com/app/ntfy/id1625396347)).
2. לוחצים ➕ ונרשמים ל-topic עם שם ארוך ואקראי, למשל `liranfix-sniper-8d2kq7x`
   (כל מי שיודע את השם יכול לקרוא את ההתראות).
3. כותבים את אותו שם ב-`config.toml` תחת `[notify.ntfy]`.

### או טלגרם

1. בטלגרם פונים ל-**@BotFather**, שולחים `/newbot` ומקבלים token.
2. שולחים הודעה כלשהי לבוט החדש, ואז פותחים בדפדפן
   `https://api.telegram.org/bot<TOKEN>/getUpdates` ומעתיקים את `chat.id`.
3. ממלאים `bot_token` ו-`chat_id` תחת `[notify.telegram]` (או במשתני הסביבה
   `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`).

אפשר להפעיל את שניהם במקביל.

### בדיקה

```bash
python -m sniper_bot test-notify     # אמורה להגיע התראת בדיקה לטלפון
python -m sniper_bot once --dry-run  # מראה את כל המציאות שקיימות עכשיו, בלי לשלוח ובלי לשמור
python -m sniper_bot run             # מתחיל לצוד
```

## הגדרת חיפושים

כל חיפוש הוא בלוק `[[search]]` ב-`config.toml`:

```toml
[[search]]
name = "לפטופ לתיקון"      # השם שיופיע בהתראה
source = "yad2"
query = "מחשב נייד"        # מה שהיית מקליד בחיפוש של יד2
max_price = 900            # מחיר הרצפה: מעל זה לא מתריעים
min_price = 50             # מדלג על מחירי "1 ₪" פיקטיביים
product_types = ["328"]    # 328 = מחשבים ניידים, 267 = טלפונים, 4745 = טאבלטים
require_fault = true       # רק אם מוזכרת תקלה (או שהמצב הוא "דרוש תיקון/שיפוץ")
# conditions = ["4"]       # 2 = כמו חדש, 3 = משומש, 4 = דרוש תיקון/שיפוץ
# exclude = ["מחודש"]      # מילים שפוסלות מודעה בחיפוש הזה
# allow_no_price = true    # גם מודעות בלי מחיר / למסירה
# include_business = true  # גם חנויות ועסקים (ברירת מחדל: רק מוכרים פרטיים)
# pages = 2                # כמה עמודי תוצאות לסרוק בכל סבב (ברירת מחדל 1)
```

- `require_fault = false` מתאים ל"כל מחשב מתחת ל-350 ₪", גם תקין.
- רשימות התקלות והפסילות המובנות נמצאות ב-[`sniper_bot/keywords.py`](sniper_bot/keywords.py).
  אפשר להוסיף משלך ב-`[keywords]` בקונפיג (`extra_faults`, `extra_exclude`).
- לבדוק מה הבוט מזהה בטקסט מסוים:
  ```bash
  python -m sniper_bot check "המחשב לא נטען, המסך תקין, אין שום בעיה אחרת"
  # fault   [טעינה] לא נטען
  ```
- `python -m sniper_bot -v once --dry-run` מראה לכל מודעה למה היא נפסלה.
- `python -m sniper_bot history` מציג את ההתראות האחרונות.

### לוחות נוספים (RSS)

כל לוח שמפרסם RSS/Atom:

```toml
[[search]]
name = "לוח RSS"
source = "rss"
url = "https://example.com/feed.xml"
keywords = ["מחשב נייד", "לפטופ", "laptop"]   # לפחות אחת חייבת להופיע
max_price = 800
require_fault = true
```

המחיר נקרא מהטקסט ("400 ₪", "מחיר: 350"). מודעות בלי מחיר שאפשר לקרוא מדולגות, אלא אם
מגדירים `allow_no_price = true`.

## הרצה ברקע

הבוט צריך לרוץ על מחשב שדולק כל הזמן (מחשב בבית, Raspberry Pi, שרת קטן).

**Linux / Raspberry Pi (systemd):** ההוראות נמצאות בראש הקובץ [`deploy/sniper-bot.service`](deploy/sniper-bot.service).
בקצרה:
```bash
mkdir -p ~/.config/systemd/user
cp deploy/sniper-bot.service ~/.config/systemd/user/
systemctl --user enable --now sniper-bot
loginctl enable-linger "$USER"
journalctl --user -u sniper-bot -f
```

**Windows:** [`deploy/start-windows.bat`](deploy/start-windows.bat) מריץ את הבוט בלי חלון והלוג נכתב
ל-`sniper.log`. כדי שיעלה אוטומטית שמים קיצור דרך לקובץ ב-`shell:startup` (Win+R).

**על הטלפון עצמו (Android + Termux):** מעתיקים את התיקייה לטלפון ואז:
```bash
pkg install python
termux-wake-lock                     # שאנדרואיד לא יהרוג את התהליך
cd sniper-bot && pip install -r requirements.txt
cp config.example.toml config.toml   # וממלאים כמו למעלה
python -m sniper_bot run
```

**cron במקום לולאה:** `*/3 * * * * cd ~/Liranfix-/sniper-bot && .venv/bin/python -m sniper_bot once`

## מגבלות שחשוב לדעת

- **יד2 מפעילים הגנה נגד בוטים.** בקצב סביר (ברירת המחדל: סריקה כל 2-3 דקות, 3 שניות בין
  בקשות) מהאינטרנט הביתי זה בדרך כלל עובד. אם מקבלים קאפצ'ה, הבוט מאריך את ההמתנה (עד 30 דק')
  ומתריע לך. אל תקצר את `interval_seconds` יותר מדי. רוב השרתים בענן נחסמים מהר יותר מחיבור ביתי.
- יד2 יכולים לשנות את מבנה האתר. אם זה קורה, הלוג יציג `layout changed` או `blocked`, והבוט
  ישלח התראה אחרי כמה כשלונות רצופים. הקוד שקורא את יד2 נמצא כולו ב-[`sniper_bot/sources/yad2.py`](sniper_bot/sources/yad2.py).
- **פייסבוק מרקטפלייס וקבוצות וואטסאפ לא נתמכים.** הם דורשים התחברות, ופייסבוק חוסם סריקה
  אוטומטית.
- הבוט מיועד לשימוש אישי ובקצב נמוך. האחריות לעמוד בתנאי השימוש של כל אתר היא שלך.

## פיתוח

```bash
python -m unittest        # כל הבדיקות רצות בלי רשת
```

מבנה:
```
sniper_bot/
  bot.py          הלולאה: סריקה → סינון → התראה → זיכרון, backoff והתראת תקלה
  matcher.py      זיהוי תקלות/פסילות (תחיליות, שלילה), החלטה אם מודעה היא מציאה
  keywords.py     רשימות מילים בעברית ובאנגלית
  sources/yad2.py קריאת יד2 Market (חיפוש + דף מודעה)
  sources/rss.py  כל פיד RSS/Atom
  notifiers.py    ntfy, טלגרם, קונסול
  storage.py      SQLite: נראו / התריעו / אותחלו
  config.py       טעינה ובדיקה של config.toml
```

כדי להוסיף לוח חדש כותבים מחלקה עם `search(search) -> list[Listing]` ו-`enrich(listing)`
ורושמים אותה ב-`sources/__init__.py`.
