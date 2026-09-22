# النشر المجاني الدائم على PythonAnywhere (بدون بطاقة بنكية — 24/7)

الخطة باختصار:
- الموقع بيتسجل مجانًا على `username.pythonanywhere.com` (نفس الكود).
- القاعدة SQLite بتتخزن على السيرفر نفسه (مستدامة على الديسك).
- الـ Google OAuth بيشتغل لأن `redirect_uri` بيتحسب أوتوماتيك من المجال.
- الخلفية المراقبة (monitor) بتشغل جوه الـ worker نفسه على طول.

## مهم الأول — علشان يشتغل على السحابة لازم الكود يتدفع لقيتاب
الخطوة دي انعملت: تم عمل كوميت + بوش لآخر التعديلات (`auth.py` و `oauth.py` وغيرها).

## خطوات من عندك (حوالي 10 دقايق — مرة واحدة بس)

### 1) اعمل حساب على https://www.pythonanywhere.com
- سجل بايميل + باسورد. **مفيش كارت** مطلوب.

### 2) افتح **Bash console** (من الصفحة الرئيسية إلى Console ثم Bash)

### 3) اكتب الأوامر دي بالظبط (كل أمر في سطر)

```bash
git clone https://github.com/eslamahmad0arkan-jpg/document-control-platform.git
cd document-control-platform
mkvirtualenv --python=python3.12 drivedoc
pip install requests
pip install -r backend/requirements-cloud.txt
```

### 4) اعمل ملف `.env` في جذر المشروع
اضغط `nano .env`، الصق ده، وعدّل:
- `SECRET_KEY` : أي سلسلة طويلة عشوائية
- `WEBHOOK_BASE_URL` : استبدل `USERNAME` باسمك مثل `https://eslamtest.pythonanywhere.com`

```ini
APP_ENV=production
DEBUG=false
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=lax

GOOGLE_CLIENT_ID=PASTE_GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET=PASTE_GOOGLE_CLIENT_SECRET

SECRET_KEY=CHANGE_ME_LONG_RANDOM_STRING
ACCESS_TOKEN_EXPIRE_MINUTES=43200

MONITOR_ENABLED=true
SYNC_INTERVAL_SECONDS=600
SYNC_DELAY_BETWEEN_REQUESTS_MS=200
WEBHOOK_BASE_URL=https://USERNAME.pythonanywhere.com
```

الحفظ: `Ctrl+O` ثم Enter ثم `Ctrl+X`.

> لو اتحط أكونسول جديد بعدين: `workon drivedoc` ثم `cd ~/document-control-platform`

### 5) خد الـ API Token
- من صفحة الحساب: **Account ثم API token**، اضغط "Create" وانسخ الـ token.

### 6) أنشئ الموقع
في الكونسول، شغّل (مع استبدال `USERNAME` و `TOKEN`):
```bash
python ~/document-control-platform/pythonanywhere/pa_deploy.py --user USERNAME --token TOKEN --mode create
```
بعد ما يكتب Success، جرّب من المتصفح:
`https://USERNAME.pythonanywhere.com/api/health`

### 7) اربط الدخول بجوجل
- افتح `console.cloud.google.com >> APIs & Services >> Credentials` ثم ملف الـ OAuth client.
- أضف في **Authorized redirect URIs** السطر:
  `https://USERNAME.pythonanywhere.com/api/auth/callback`
- ولو موجود، أضف في **Authorized JavaScript origins**:
  `https://USERNAME.pythonanywhere.com`
- احفظ (Save).

### 8) جرّب تسجيل الدخول من الرابط الجديد

## تجديد شهري (مجاني لكن بيتطلب ضغطة كل شهر)
الحساب المجاني بيعطّل الموقع لو فضل **غير مستخدم شهر كامل**. الحل: ادخل حسابك (أو مر على الموقع) مرة كل فترة قليلة، ودوس على زر "Run until ..." لما يظهر.

## تحديث الكود بعدين
بعد ما نعدّل الكود محليًا ونبوشه:
```bash
cd ~/document-control-platform && git pull
python ~/document-control-platform/pythonanywhere/pa_deploy.py --user USERNAME --token TOKEN --mode reload
```

## ملاحظات
- الداتا المحلية الحالية لا تنتقل للسحابة؛ أول تسجيل دخول على الرابط الجديد بيعمل حساب جديد ويربط جوجل فورًا.
- `SYNC_INTERVAL_SECONDS=600` = فحص كل 10 دقايق، علشان نفضل جوه حدود الـ CPU المجاني (قابل للتغيير في الـ .env).
- الـ monitor هو اللي بجدد قنوات Google Drive push (webhook) أوتوماتيك، وده ممكن ما شاء الله أن يشغل push notifications مباشر من جوجل للسحابة.
- إذا ظهر `database is locked` نادرًا، أعد المحاولة أو ظبط `SYNC_INTERVAL_SECONDS` لرقم أكبر.