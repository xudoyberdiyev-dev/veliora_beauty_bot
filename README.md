# VELIORA BEAUTY — avtomatik post bot

Bot har kuni **08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00** (Toshkent vaqti) da
kanalga bittadan tasdiqlangan post joylaydi.

## Qanday ishlaydi
1. Bot yangi postni (rasmlar + matn) sizga shaxsiy chatda yuboradi.
2. **✅ Tasdiqlash** → narxini yozasiz (`220000` yoki `220 ming`). Post navbatga tushadi.
3. **❌ Bekor qilish** → post kanalga chiqmaydi.
4. Jadval vaqti kelganda navbatdagi post narxi bilan kanalga chiqadi.
5. Tasdiqlangan post qolmasa, bot sizga eslatma yuboradi.

Buyruqlar: `/start`, `/holat`, `/yordam`.

Bot GitHub Actions orqali **har 30 daqiqada** uyg'onadi, shuning uchun
javoblar 30 daqiqagacha kechikishi mumkin. Postlar jadvaldagi vaqtdan bir necha daqiqa keyin chiqadi.

## Sozlash (bir marta)
1. **Token:** repo → **Settings → Secrets and variables → Actions → New repository secret**  
   Name: `TELEGRAM_BOT_TOKEN` va `TELEGRAM_CHAT_ID` — admin (sizning) shaxsiy chat ID'ingiz, Secret: @BotFather bergan token.
2. Botni kanalga **admin** qiling ("Post joylash" huquqi bilan).
3. Botga `/start` yozing. Birinchi `/start` yozgan odam admin bo'ladi
   (yoki `config.json` → `admin_usernames` ga username yozing).
4. **Actions** bo'limi → **Veliora bot** → **Run workflow** — birinchi ishga tushirish.

## Fayllar
- `posts/<raqam-nom>/` — bitta post: rasmlar (`1.jpg`, `2.jpg`, … ko'pi bilan 10 ta) va `post.txt`.
  `post.txt` dagi `{NARX}` o'rniga siz yozgan narx qo'yiladi.
- `config.json` — jadval, kanal (`"channel": "@username"`, bo'sh bo'lsa avtomatik aniqlanadi).
- `state.json` — bot holati (o'zgartirmang).
- `bot.py` — bot kodi.
