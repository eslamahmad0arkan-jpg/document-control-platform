# النشر السحابي الدائم على Koyeb + Neon Postgres (بدون بطاقة — بدون تجديد — غير تجريبي)

**لماذا هذا الحل أفضل من PythonAnywhere:**
- Koyeb → استضافة حاويات **ناضجة (GA)** لا بتلزم بتجديد شهري، وخطة Free **دائمة** (مفيش أي تجديد).
- Neon → قاعدة بيانات Postgres **مجانية دائمة** (مش trial)، بدون كارت.
- إبقاء الموقع مستيقظ 24/7 عبر مُراقب خارجي مجاني (UptimeRobot).

**بنية التشغيل النهائية:**
```
المتصفح ← Koyeb (حاوية التطبيق، دائم) ← Neon Postgres (الداتا، سحابية مستمرة)
              ↑
      UptimeRobot (بنغ كل 5 دقايق عشان يفضل شغال 24/7 ومش بينام)
```

---

## الخطوات (حوالي 15 دقيقة — مرة واحدة)

### 1) حساب Koyeb (يفضل عبر GitHub لتجنب طلب الكارت)
- افتح https://app.koyeb.com و "Sign up" عبر **GitHub** أو **Google** أو الإيميل.
- لو طلب بطاقة هنا → إلغي وسولي (عندها نرجع لخطة PythonAnywhere الاحتياطية).

### 2) قاعدة البيانات على Neon (مجاني دائم، بدون كارت)
- افتح https://neon.com → "Sign up" (إيميل أو GitHub) → Create a project.
- خذ **connection string** الـ **Pooled**، بصيغة مثل:
  `postgresql://USER:PASSWORD@HOST-POOLER/PROJECT?sslmode=require`
- احتفظ بـ الباسورد عشان الخطوة 4.
- ملاحظة: الجداول بتتخلق أوتوماتيك عند أول تشغيل (create_all في التطبيق) — **مفيش manual migration**.

### 3) استضافة التطبيق على Koyeb
- تدخل https://app.koyeb.com/services/new
- **Git repository**: `eslamahmad0arkan-jpg/document-control-platform` (نفس اللي دفعته إحنا).
  - Branch: `main` — Koyeb هيكتشف الـ Dockerfile أوتوماتيك.
- **Service settings**:
  - Name: `drivedoc` (مثال)
  - Instance: **Free** (512MB / 0.1 vCPU)
  - Region: القريب منك (Frankfurt أو Washington)
  - Port: `8000`
  - Health check path: `/api/health`
  - Autodeploy: فعّل (تحديث الكود بعدين = push فقط)
- **Environment variables** (راجع الخطوة 4 أدناه للحشو).
- اضغط **Deploy** وانتظر يظهر `Healthy`.

رابطك النهائي بيبقى شكل:
`https://drivedoc-USERORG.koyeb.app`

### 4) Environment variables بالظبط
| المتغير | القيمة |
|---|---|
| `APP_ENV` | `production` |
| `DEBUG` | `false` |
| `SESSION_COOKIE_SECURE` | `true` |
| `DATABASE_URL` | `postgresql+psycopg2://...` (اللي من Neon، مع إضافة `+psycopg2`) |
| `GOOGLE_CLIENT_ID` | (من رسالتي لك — مش في الريبو) |
| `GOOGLE_CLIENT_SECRET` | (من رسالتي لك — مش في الريبو) |
| `SECRET_KEY` | أي سلسلة طويلة عشوائية |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `43200` |
| `MONITOR_ENABLED` | `true` |
| `SYNC_INTERVAL_SECONDS` | `600` |
| `SYNC_DELAY_BETWEEN_REQUESTS_MS` | `200` |
| `WEBHOOK_BASE_URL` | `https://drivedoc-USERORG.koyeb.app` |

### 5) الإبقاء مستيقظ 24/7 (UptimeRobot — مجاني)
- https://uptimerobot.com → المجاني فيه لحد 50 مراقب على فترة فحص دقيقة واحدة؟ لا، الفحص الافتراضي المجاني **كل 5 دقايق** — وهذا كافٍ (Koyeb بينام بعد ساعة فقط).
- أضف **New monitor**: HTTP(s) → URL `https://drivedoc-USERORG.koyeb.app/api/health` → interval 5 min.
- النتيجة: الطلب كل 5 دقايق بيمنع النوم نهائيًا، فتظل المراقبة الداخلية (monitor thread بينام بعد 600 ثانية) شغالة 24/7 وتحفظ الداتا محدثة.

### 6) ربط تسجيل الدخول بجوجل
- جرّب `https://drivedoc-USERORG.koyeb.app/api/health` أولًا (يرجع `{"status":"ok"}`).
- افتح **Google Cloud Console >> Credentials >> ملف OAuth client**:
  - Authorized redirect URIs: أضف `https://drivedoc-USERORG.koyeb.app/api/auth/callback`
  - Authorized JavaScript origins: أضف `https://drivedoc-USERORG.koyeb.app`
  - Save.
- ادخل على الرابط → Sign in with Google → تأكيد الدخول.

---

## التحديثات بعدين
- عدّل الكود محليًا → `git push origin main` → Koyeb يعمل redeploy أوتوماتيك (لو Autodeploy مفعّل).
- الداتا كلها في Neon (مستمرة حتى لو الكود اتغير أو الحاوية اتعمل عليها redeploy).

## التنبيهات الواقعية
- **Koyeb cold start**: لو الـ UptimeRobot اشتغل وحد الساعة انقطعت، أول زيارة ممكن تاخد 1-5 ثواني wake-up. عادي.
- Neon: الـ compute بينام بعد 5 دقايق خمول ويقوم تلقائيًا عند أول query (احتياج قصير، غير ملحوظ).
- يوجد plan B: لو Koyeb طلب بطاقة، نرجع للـ PythonAnywhere (الـ files جهزتها قبل كده) أو نجرب تسجيل بـ GitHub محمّل بالكامل.
- Google Drive API quota: لو ظهرت رسائل عن quota زايد، زد `SYNC_INTERVAL_SECONDS` (مثل 900+).

## أسئلة؟
سجل أي رسالة ظهورها خطأ غامض هنا، وأنا أشيك وأكمل.