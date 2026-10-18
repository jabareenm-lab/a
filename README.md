# מהנדס שואל – צ'אט בוט מקומי מעל ספריית ההנחיות

אפליקציה שרצה על המחשב במשרד, קוראת את תיקיית ההנחיות (במחשב / OneDrive / SharePoint), בונה אינדקס חיפוש בעברית,
ועונה על שאלות מקצועיות בעזרת Claude – **עם הפניה למסמך ולעמוד**, וקישור שפותח את ה-PDF בעמוד הנכון.

```
שאלה ← Claude מחפש בספרייה (כמה ניסוחים) ← קורא את העמודים הרלוונטיים ← תשובה + מקורות
```

## מה צריך
1. **Python 3.11 ומעלה** – https://www.python.org/downloads/ (בהתקנה לסמן "Add Python to PATH").
2. **מפתח API של Anthropic** – https://console.anthropic.com → API Keys.
3. גישה לתיקיית ההנחיות (אחת מהאפשרויות למטה).

## התקנה והפעלה (Windows)
1. מורידים/משכפלים את התיקייה הזו למחשב.
2. לחיצה כפולה על `start_windows.bat`. בפעם הראשונה הוא מתקין הכול ופותח שני קבצים:
   - `.env` – מדביקים את המפתח: `ANTHROPIC_API_KEY=sk-ant-...`
   - `config.yaml` – מגדירים את תיקיית ההנחיות (ראו למטה).
3. מפעילים שוב את `start_windows.bat`. הדפדפן ייפתח בכתובת http://localhost:8765.

ב-Mac: `start_mac.command`. ידנית: `pip install -r requirements.txt` ואז `python run.py`.

## חיבור לתיקיית ההנחיות

### אפשרות א' (מומלצת, בלי IT): סנכרון SharePoint ל-OneDrive
1. נכנסים בדפדפן לספריית ההנחיות ב-SharePoint ← לוחצים **סנכרן (Sync)**.
2. התיקייה מופיעה בסייר הקבצים (בדרך כלל `C:\Users\<שם>\<שם הארגון>\<אתר> - Documents\...`).
3. ב-`config.yaml` מוסיפים את הנתיב:
   ```yaml
   sources:
     local_folders:
       - "C:/Users/mostafa/MJ/Office - Documents/הנחיות"
   ```
   אפשר לרשום כמה תיקיות (גם תיקיות רגילות במחשב).

> טיפ: ב-OneDrive לבחור "Always keep on this device" על תיקיית ההנחיות, כדי שהקבצים יהיו זמינים לקריאה.

### אפשרות ב': חיבור ישיר ל-SharePoint (Microsoft Graph)
מתאים כשלא רוצים לסנכרן, או כשמריצים את האפליקציה על שרת. דורש רישום אפליקציה פעם אחת:
1. https://entra.microsoft.com ← App registrations ← **New registration** (שם: "מהנדס שואל", Single tenant).
2. Authentication ← **Allow public client flows = Yes**.
3. API permissions ← Microsoft Graph ← Delegated ← `Files.Read.All`, `Sites.Read.All` (ייתכן שנדרש Grant admin consent).
4. מעתיקים את ה-Tenant ID וה-Client ID ל-`config.yaml`:
   ```yaml
   sharepoint:
     enabled: true
     tenant_id: "..."
     client_id: "..."
     auth: device_code
     folder_url: "https://TENANT.sharepoint.com/sites/Office/Shared%20Documents/הנחיות"
   ```
   (`folder_url` – פשוט מעתיקים את הקישור לתיקייה מהדפדפן.)
5. התחברות חד-פעמית: `python run.py sharepoint-login`, ופועלים לפי ההוראות (קוד ב-microsoft.com/devicelogin).

האפליקציה מורידה רק קבצים חדשים או ששונו לתיקיית מטמון (`data/sharepoint_cache`), והקישורים בתשובות פותחים את הקובץ המקורי ב-SharePoint.

## שימוש
- כותבים שאלה ← רואים בזמן אמת מה הוא מחפש ← תשובה עם מקורות.
- **מסמכים שנבדקו** מתחת לכל תשובה: "נקרא" = המודל קרא את העמוד המלא; "תוצאת חיפוש" = הופיע בחיפוש בלבד.
- **רענון ספרייה ואינדקס** – אחרי שהוסיפו או עדכנו מסמכים (מתבצע גם אוטומטית בכל הפעלה, ורק קבצים שהשתנו מעובדים מחדש).
- **חיפוש מהיר** בצד – חיפוש מילים ישיר במסמכים, בלי Claude.
- מהטרמינל: `python run.py ask "מה רוחב נתיב מינימלי בדרך עירונית?"`

## התאמת "האישיות" של הבוט
ההנחיות לבוט נמצאות ב-`prompts/system_he.md`. אם יש לכם הנחיות מפרויקט ה-Cowork של "מהנדס שואל" – מדביקים אותן שם
(או מוסיפים אותן לקובץ הקיים). שינוי נכנס לתוקף בשאלה הבאה, בלי הפעלה מחדש.

## הערות חשובות
- **קבצים סרוקים** (PDF שהוא תמונה בלבד) לא ניתנים לחיפוש. הם מופיעים בלוח הסטטוס תחת "ללא טקסט". יש להעביר אותם OCR
  (למשל ב-Adobe Acrobat: Scan & OCR) ולשמור במקום המקורי.
- **סוגי קבצים**: PDF, Word ‏(docx), Excel ‏(xlsx), txt, md. קובצי ‎.doc ישנים – לשמור מחדש כ-docx.
- **פרטיות**: המסמכים והאינדקס נשארים במחשב. רק השאלה וקטעי הטקסט הרלוונטיים נשלחים ל-API של Anthropic לצורך התשובה.
  לפי תנאי השימוש המסחריים של Anthropic, נתוני API לא משמשים לאימון מודלים.
- **גישה מעמדות אחרות במשרד**: ב-`config.yaml` לשנות `host: 0.0.0.0`, ואז לגשת מ-`http://<שם-המחשב>:8765`. אין באפליקציה מנגנון
  התחברות משתמשים, לכן להפעיל זאת רק ברשת הפנימית.
- **עלות**: כל שאלה צורכת טוקנים לפי המודל ב-`config.yaml` (ברירת מחדל `claude-opus-5-5`, effort `high`).
  `effort: medium` מהיר וזול יותר; `xhigh` יסודי יותר.
- **אחריות מקצועית**: הבוט מצטט את המסמכים שבספרייה. בהחלטות מחייבות יש לאמת מול המסמך המקורי, את עדכניות ההנחיה,
  ומול תקנות התעבורה / חוק התכנון והבנייה בנוסחן העדכני.

## מבנה הקוד
| קובץ | תפקיד |
|---|---|
| `run.py` | הפעלה: שרת / אינדוקס / התחברות SharePoint / שאלה מהטרמינל |
| `app/extract.py` | חילוץ טקסט מ-PDF (לפי עמוד), Word (לפי כותרות), Excel (לפי גיליון) |
| `app/hebrew.py` | נרמול עברית לחיפוש (תחיליות ו/ה/ב/כ/ל/מ/ש, אותיות סופיות, ראשי תיבות, תיקון PDF הפוך) |
| `app/index.py` | אינדקס SQLite FTS5 (BM25), עדכון אינקרמנטלי |
| `app/sharepoint.py` | סנכרון מ-SharePoint דרך Microsoft Graph |
| `app/agent.py` | Claude עם כלי חיפוש/קריאה במסמכים |
| `app/server.py`, `app/static/index.html` | שרת מקומי וממשק הצ'אט |

בדיקות: `pip install pytest httpx && python -m pytest tests`
