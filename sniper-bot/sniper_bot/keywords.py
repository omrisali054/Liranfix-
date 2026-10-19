"""Default keyword lists (Hebrew + English).

Phrases are matched after normalisation (see ``text.normalize``): case, niqqud,
final letters and punctuation don't matter, and Hebrew phrases also match with
attached prefixes (ו/ה/ב/ל/מ/ש/כ), so "מסך שבור" catches "והמסך שבור".

Fault phrases describe the *fault*, not just the part, so "סוללה מחזיקה יום"
does not count as a battery problem while "סוללה חלשה" does. A match directly
preceded by a negation word ("אין בעיית טעינה", "לא תקול") is ignored.
"""

# Category label -> phrases. Labels are shown in the phone notification.
FAULT_KEYWORDS: dict[str, list[str]] = {
    "טעינה": [
        "לא נטען", "לא נטענת", "לא טוען", "לא טוענת", "אינו נטען", "אינה נטענת",
        "בעיית טעינה", "בעיה בטעינה", "בעיות טעינה", "תקלת טעינה", "תקלה בטעינה",
        "שקע טעינה", "חיבור טעינה", "כניסת טעינה", "פורט טעינה",
        "מטען תקול", "מטען לא עובד", "צריך מטען", "בלי מטען", "ללא מטען",
        "not charging", "won't charge", "doesn't charge", "does not charge",
        "charging port", "charging issue", "charging problem", "no charger",
    ],
    "מסך": [
        "מסך שבור", "מסך סדוק", "מסך מנופץ", "מסך לא עובד", "מסך שחור", "מסך מהבהב",
        "סדק במסך", "סדקים במסך", "שבר במסך", "פסים במסך", "קווים במסך",
        "כתם במסך", "כתמים במסך", "פיקסלים מתים", "החלפת מסך", "צריך מסך",
        "תאורת מסך", "זכוכית שבורה", "זכוכית סדוקה", "טאץ' לא עובד",
        "cracked screen", "broken screen", "screen broken", "screen cracked",
        "screen damage", "lines on screen", "no display", "black screen",
        "dead pixels", "broken lcd", "cracked lcd",
    ],
    "סוללה": [
        "סוללה חלשה", "סוללה מנופחת", "סוללה נפוחה", "סוללה תקולה", "סוללה גמורה",
        "סוללה מתה", "סוללה לא מחזיקה", "סוללה לא טובה", "צריך סוללה",
        "להחליף סוללה", "החלפת סוללה", "בלי סוללה", "ללא סוללה",
        "עובד רק על חשמל", "עובד רק מחובר", "רק מחובר לחשמל",
        "bad battery", "dead battery", "swollen battery", "battery swollen",
        "needs battery", "needs a battery", "no battery",
    ],
    "לא נדלק": [
        "לא נדלק", "לא נדלקת", "אינו נדלק", "אינה נדלקת", "לא עולה", "לא מגיב",
        "נכבה לבד", "נכבה מעצמו",
        "won't turn on", "not turning on", "doesn't turn on", "does not turn on",
        "no power", "won't boot", "doesn't boot", "not booting",
    ],
    "חומרה": [
        "מקלדת לא עובדת", "מקלדת תקולה", "מקשים לא עובדים", "מקש לא עובד",
        "ציר שבור", "צירים שבורים", "ציר תקול", "טאצפד לא עובד",
        "משטח מגע לא עובד", "מאוורר רועש", "רעש במאוורר", "מתחמם",
        "keyboard not working", "broken keyboard", "broken hinge", "hinge broken",
        "trackpad not working", "touchpad not working", "overheating",
    ],
    "תוכנה": [
        "צריך פרמוט", "צריך פירמוט", "דורש פרמוט", "דורש פירמוט", "צריך התקנה",
        "דורש התקנה", "בלי מערכת הפעלה", "ללא מערכת הפעלה", "נתקע בלוגו",
        "נתקע על הלוגו", "מסך כחול", "לולאת אתחול",
        "bootloop", "boot loop", "blue screen", "no os", "stuck on logo", "needs format",
    ],
    "כללי": [
        "דרוש תיקון", "צריך תיקון", "דורש תיקון", "לתיקון", "לא תקין", "לא תקינה",
        "תקול", "תקולה", "תקלה", "לחלקים", "לחלקי חילוף", "לפירוק", "כמו שהוא",
        "נמכר במצבו",
        "for parts", "as is", "needs repair", "need repair", "not working",
        "broken", "faulty", "for repair", "spares or repair",
    ],
}

# A listing matching any of these is skipped.
EXCLUDE_KEYWORDS: list[str] = [
    # Buyers / wanted ads (often dealers hunting the same deals).
    "קונה מחשב", "קונים מחשב", "קונה ניידים", "קונים ניידים", "קונה לפטופ",
    "קונים לפטופ", "קונה מכשיר", "קונים מכשיר", "קונה טלפון", "קונים טלפון",
    "קונה פלאפון", "קונים פלאפון", "קונה במזומן", "קונים במזומן", "קונה כל",
    "קונים כל", "מחפש מחשב", "מחפשת מחשב", "מחפש נייד", "מחפשת נייד",
    "wanted", "wtb",
    # Account-locked devices: usually not legally fixable.
    "icloud", "אייקלאוד", "איי קלאוד", "נעול לחשבון", "חסום לחשבון",
    "נעול לאייקלאוד", "mdm", "imei חסום", "חסום imei", "blacklisted",
    # Accessories sold on their own.
    "תיק למחשב", "תיק לנייד", "מטען למחשב", "מטען לנייד", "כיסוי ל", "מגן מסך",
    "מעמד למחשב",
]

# Words that cancel a fault phrase when they come right before it.
NEGATION_WORDS: list[str] = ["אין", "ללא", "בלי", "לא", "no", "not", "without", "never"]

# Filler words skipped when looking for a negation ("אין שום בעיית טעינה").
NEGATION_FILLERS: list[str] = ["שום", "any"]
