#!/usr/bin/env python3
"""VELIORA BEAUTY — Telegram kanal uchun avtomatik post bot.

GitHub Actions bu skriptni har 30 daqiqada ishga tushiradi. Har safar:
  1) botga kelgan xabarlarni o'qiydi (tasdiqlash tugmalari, narx, buyruqlar);
  2) yangi postlarni adminga ko'rib chiqish uchun yuboradi;
  3) jadvaldagi vaqt kelgan bo'lsa, navbatdagi tasdiqlangan postni kanalga joylaydi.

Holat (qaysi post tasdiqlangan, narxi, joylangan) state.json faylida saqlanadi.
Faqat Python standart kutubxonasi ishlatiladi.
"""
import html
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POSTS_DIR = ROOT / "posts"
STATE_FILE = ROOT / "state.json"
CONFIG_FILE = ROOT / "config.json"

TZ = timezone(timedelta(hours=5))  # Toshkent vaqti
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
PRICE_TAG = "{NARX}"
CAPTION_LIMIT = 1024
SLOT_WINDOW_MIN = 110  # jadval vaqtidan keyin shu daqiqa ichida post joylanadi


def log(*a):
    print(datetime.now(TZ).strftime("%H:%M:%S"), *a, flush=True)


# ----------------------------------------------------------------- Telegram API
class TgError(Exception):
    pass


class Telegram:
    def __init__(self, token):
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method, params=None, files=None, _retry=True):
        params = params or {}
        if files:
            boundary = uuid.uuid4().hex
            body = bytearray()
            for k, v in params.items():
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
                body += str(v).encode() + b"\r\n"
            for name, path in files.items():
                ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
                body += (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                    f'filename="{Path(path).name}"\r\nContent-Type: {ctype}\r\n\r\n'
                ).encode()
                body += Path(path).read_bytes() + b"\r\n"
            body += f"--{boundary}--\r\n".encode()
            req = urllib.request.Request(
                self.base + method, data=bytes(body),
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        else:
            req = urllib.request.Request(
                self.base + method, data=json.dumps(params, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                res = json.loads(e.read() or b"{}")
            except ValueError:
                res = {"ok": False, "description": f"HTTP {e.code}"}
        if not res.get("ok"):
            wait = (res.get("parameters") or {}).get("retry_after")
            if wait and _retry:
                time.sleep(min(int(wait), 60) + 1)
                return self.call(method, params, files, _retry=False)
            raise TgError(f"{method}: {res.get('description')}")
        return res["result"]


# ------------------------------------------------------------------- yordamchi
def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text("utf-8"))
    return default


def natural_key(p):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def list_posts():
    """posts/<papka>/ ichidagi rasmlar + post.txt — bitta post."""
    posts = []
    if not POSTS_DIR.exists():
        return posts
    for d in sorted((p for p in POSTS_DIR.iterdir() if p.is_dir()), key=natural_key):
        txt = d / "post.txt"
        imgs = sorted((f for f in d.iterdir() if f.suffix.lower() in IMG_EXT), key=natural_key)
        if txt.exists() and imgs:
            posts.append({"id": d.name, "text": txt.read_text("utf-8").strip(), "images": imgs[:10]})
    return posts


def parse_price(text):
    t = text.lower()
    mult = 1000 if re.search(r"\d\s*(ming|k)\b", t) else 1
    digits = re.sub(r"\D", "", t)
    if not digits or len(digits) > 10:
        return None
    n = int(digits) * mult
    return n if n > 0 else None


def fmt_price(n):
    return f"{n:,}".replace(",", " ") + " so'm"


def tg_len(s):
    """Telegram uzunlikni UTF-16 belgilarida, HTML teglarisiz hisoblaydi."""
    plain = html.unescape(re.sub(r"<[^>]+>", "", s))
    return len(plain.encode("utf-16-le")) // 2


def first_line(text):
    line = text.strip().splitlines()[0] if text.strip() else ""
    return html.unescape(re.sub(r"<[^>]+>", "", line)).strip()


# ------------------------------------------------------------------------- bot
class Bot:
    def __init__(self, tg, config, state, now):
        self.tg, self.cfg, self.st, self.now = tg, config, state, now
        self.st.setdefault("offset", 0)
        self.st.setdefault("admin_chat_ids", [])
        self.st.setdefault("posts", {})
        self.st.setdefault("awaiting", {})       # chat_id -> post_id (narx kutilmoqda)
        self.st.setdefault("preview_msgs", {})   # "chat:msg" -> post_id
        self.st.setdefault("done_slots", [])
        self.posts = {p["id"]: p for p in list_posts()}
        for pid in self.posts:
            self.st["posts"].setdefault(pid, {"status": "new"})

    # --- saqlash
    def save(self):
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.st, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
        tmp.replace(STATE_FILE)

    def ps(self, pid):
        return self.st["posts"].setdefault(pid, {"status": "new"})

    def title(self, pid):
        p = self.posts.get(pid)
        return first_line(p["text"]) if p else pid

    def send(self, chat_id, text, **kw):
        try:
            return self.tg.call("sendMessage", {"chat_id": chat_id, "text": text,
                                                "parse_mode": "HTML", **kw})
        except TgError as e:
            log("sendMessage xato:", e)

    def notify_admins(self, text):
        for cid in self.st["admin_chat_ids"]:
            self.send(cid, text)

    def is_admin(self, chat_id):
        return chat_id in self.st["admin_chat_ids"]

    # --- 1. kelgan xabarlar
    def process_updates(self):
        while True:
            updates = self.tg.call("getUpdates", {
                "offset": self.st["offset"], "timeout": 0, "limit": 100,
                "allowed_updates": ["message", "callback_query", "my_chat_member"]})
            if not updates:
                return
            for u in updates:
                self.st["offset"] = u["update_id"] + 1
                try:
                    if "my_chat_member" in u:
                        self.on_member(u["my_chat_member"])
                    elif "callback_query" in u:
                        self.on_callback(u["callback_query"])
                    elif "message" in u:
                        self.on_message(u["message"])
                except Exception as e:  # bitta xabar xatosi butun ishni to'xtatmasin
                    log("update xato:", repr(e))
                self.save()

    def on_member(self, m):
        chat = m["chat"]
        if chat.get("type") == "channel" and m["new_chat_member"]["status"] == "administrator":
            self.st["channel_id"] = chat["id"]
            self.st["channel_title"] = chat.get("title", "")
            self.notify_admins(f"📢 Kanal ulandi: <b>{html.escape(chat.get('title', ''))}</b>")

    def on_message(self, msg):
        chat = msg["chat"]
        if chat.get("type") != "private":
            return
        cid = chat["id"]
        text = (msg.get("text") or "").strip()
        user = (msg.get("from") or {}).get("username") or ""

        if text.startswith("/start"):
            allowed = [u.lstrip("@").lower() for u in self.cfg.get("admin_usernames", [])]
            if self.is_admin(cid):
                self.send(cid, "👋 Siz allaqachon adminsiz. /yordam — qo'llanma.")
            elif (allowed and user.lower() in allowed) or (not allowed and not self.st["admin_chat_ids"]):
                self.st["admin_chat_ids"].append(cid)
                self.send(cid, "✅ Siz <b>VELIORA BEAUTY</b> boti admini sifatida ro'yxatdan o'tdingiz.\n\n"
                               "Yangi postlar shu yerga ko'rib chiqish uchun keladi.\n/yordam — qo'llanma.")
            else:
                self.send(cid, "Bu bot faqat VELIORA BEAUTY adminlari uchun.")
            return

        if not self.is_admin(cid):
            self.send(cid, "Bu bot faqat VELIORA BEAUTY adminlari uchun.")
            return

        if text.startswith("/yordam") or text.startswith("/help"):
            self.send(cid, HELP_TEXT)
            return
        if text.startswith("/holat"):
            self.send(cid, self.status_text())
            return

        # narx: yo preview xabariga javob (reply), yo oxirgi bosilgan ✅ tugmasi
        pid = None
        reply = msg.get("reply_to_message")
        if reply:
            pid = self.st["preview_msgs"].get(f"{cid}:{reply['message_id']}")
        if not pid:
            pid = self.st["awaiting"].get(str(cid))
        if not pid:
            self.send(cid, "Avval postning ostidagi <b>✅ Tasdiqlash</b> tugmasini bosing, "
                           "keyin narxini yozing.")
            return
        price = parse_price(text)
        if price is None:
            self.send(cid, "Narxni raqam bilan yozing, masalan: <code>220000</code>")
            return
        self.approve(cid, pid, price)

    def on_callback(self, cq):
        cid = cq["message"]["chat"]["id"] if cq.get("message") else cq["from"]["id"]
        try:
            self.tg.call("answerCallbackQuery", {"callback_query_id": cq["id"]})
        except TgError:
            pass  # eski tugma — muhim emas
        if not self.is_admin(cid):
            return
        action, _, pid = (cq.get("data") or "").partition(":")
        if pid not in self.st["posts"]:
            return
        s = self.ps(pid)
        if s["status"] == "posted":
            self.send(cid, "Bu post allaqachon kanalga chiqqan.")
            return
        if action == "ok":
            self.st["awaiting"][str(cid)] = pid
            self.send(cid, f"💰 <b>{html.escape(self.title(pid))}</b>\n\nNarxini yozing "
                           "(masalan: <code>220000</code>):")
        elif action == "no":
            s["status"] = "rejected"
            self.st["awaiting"].pop(str(cid), None)
            self.send(cid, f"❌ Bekor qilindi: {html.escape(self.title(pid))}")

    def approve(self, cid, pid, price):
        s = self.ps(pid)
        if s["status"] == "posted":
            self.send(cid, "Bu post allaqachon kanalga chiqqan.")
            return
        s.update(status="approved", price=price, approved_at=self.now.isoformat())
        self.st["awaiting"].pop(str(cid), None)
        queue = self.queue()
        place = queue.index(pid) + 1 if pid in queue else len(queue)
        self.send(cid, f"✅ <b>Tasdiqlandi!</b>\n{html.escape(self.title(pid))}\n"
                       f"💰 Narx: <b>{fmt_price(price)}</b>\n"
                       f"📅 Navbatda: {place}-o'rinda. Taxminiy vaqt: {self.eta(place)}")

    # --- 2. ko'rib chiqishga yuborish
    def send_previews(self):
        if not self.st["admin_chat_ids"]:
            return
        limit = int(self.cfg.get("preview_pending_limit", 7))
        pending = sum(1 for s in self.st["posts"].values() if s["status"] == "pending")
        for pid, p in self.posts.items():
            if pending >= limit:
                break
            s = self.ps(pid)
            if s["status"] != "new":
                continue
            for cid in self.st["admin_chat_ids"]:
                self.send_preview(cid, pid, p)
            s["status"] = "pending"
            pending += 1
            self.save()
            time.sleep(1)

    def send_preview(self, cid, pid, p):
        msgs = self.send_album(cid, p, caption=None)
        for m in msgs:
            self.st["preview_msgs"][f"{cid}:{m['message_id']}"] = pid
        body = p["text"].replace(PRICE_TAG, "<b>❓ (narxni siz yozasiz)</b>")
        n = list(self.posts).index(pid) + 1
        text = (f"🆕 <b>Yangi post #{n}</b> — ko'rib chiqing\n➖➖➖➖➖➖➖➖\n\n{body}\n\n"
                f"➖➖➖➖➖➖➖➖\n✅ Tasdiqlash → keyin narxini yozasiz\n❌ Bekor qilish → kanalga chiqmaydi")
        if tg_len(text) > 4096:
            text = body[:3500]
        m = self.tg.call("sendMessage", {
            "chat_id": cid, "text": text, "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[
                {"text": "✅ Tasdiqlash", "callback_data": f"ok:{pid}"[:64]},
                {"text": "❌ Bekor qilish", "callback_data": f"no:{pid}"[:64]}]]}})
        self.st["preview_msgs"][f"{cid}:{m['message_id']}"] = pid

    def send_album(self, chat_id, p, caption):
        """Rasmlarni albom qilib yuboradi. Avval yuklangan rasmlar file_id orqali qayta ishlatiladi."""
        s = self.ps(p["id"])
        fids = s.get("file_ids") if len(s.get("file_ids") or []) == len(p["images"]) else None
        files, media = {}, []
        for i, img in enumerate(p["images"]):
            if fids:
                ref = fids[i]
            else:
                ref = f"attach://f{i}"
                files[f"f{i}"] = img
            item = {"type": "photo", "media": ref}
            if i == 0 and caption:
                item.update(caption=caption, parse_mode="HTML")
            media.append(item)
        if len(media) == 1:
            params = {"chat_id": chat_id}
            if caption:
                params.update(caption=caption, parse_mode="HTML")
            if fids:
                m = self.tg.call("sendPhoto", {**params, "photo": fids[0]})
            else:
                m = self.tg.call("sendPhoto", params, files={"photo": p["images"][0]})
            msgs = [m]
        else:
            msgs = self.tg.call("sendMediaGroup", {"chat_id": chat_id, "media": media}, files=files or None)
        if not fids:
            s["file_ids"] = [m["photo"][-1]["file_id"] for m in msgs]
        return msgs

    # --- 3. kanalga joylash
    def queue(self):
        return [pid for pid in self.posts if self.ps(pid)["status"] == "approved"]

    def slot_times(self):
        return sorted(tuple(map(int, s.split(":"))) for s in self.cfg["slots"])

    def current_slot(self):
        for h, m in reversed(self.slot_times()):
            t = self.now.replace(hour=h, minute=m, second=0, microsecond=0)
            if t <= self.now < t + timedelta(minutes=SLOT_WINDOW_MIN):
                return t
        return None

    def eta(self, place):
        """place-chi navbatdagi post qachon chiqishini taxmin qiladi."""
        slots, day, count = self.slot_times(), self.now, 0
        done = set(self.st["done_slots"])
        for _ in range(60):
            for h, m in slots:
                t = day.replace(hour=h, minute=m, second=0, microsecond=0)
                if t + timedelta(minutes=SLOT_WINDOW_MIN) <= self.now or t.strftime("%Y-%m-%d %H:%M") in done:
                    continue
                count += 1
                if count == place:
                    d = "bugun" if t.date() == self.now.date() else (
                        "ertaga" if t.date() == self.now.date() + timedelta(days=1) else t.strftime("%d.%m"))
                    return f"{d} {t:%H:%M}"
            day = day + timedelta(days=1)
        return "-"

    def publish_if_due(self):
        slot = self.current_slot()
        if not slot:
            return
        key = slot.strftime("%Y-%m-%d %H:%M")
        if key in self.st["done_slots"]:
            return
        channel = self.cfg.get("channel") or self.st.get("channel_id")
        queue = self.queue()
        if not channel:
            self.notify_admins("⚠️ Kanal aniqlanmadi. config.json dagi \"channel\" ga kanal "
                               "username'ini yozing yoki botni kanaldan chiqarib, qayta admin qiling.")
        elif not queue:
            self.notify_admins(f"⚠️ Soat {slot:%H:%M} dagi post uchun tasdiqlangan post qolmadi. "
                               "Shuning uchun bu vaqt o'tkazib yuborildi.")
        else:
            pid = queue[0]
            p, s = self.posts[pid], self.ps(pid)
            caption = p["text"].replace(PRICE_TAG, fmt_price(s["price"]))
            if tg_len(caption) <= CAPTION_LIMIT:
                msgs = self.send_album(channel, p, caption)
            else:  # matn uzun — albom, so'ng alohida matn
                msgs = self.send_album(channel, p, None)
                self.tg.call("sendMessage", {"chat_id": channel, "text": caption, "parse_mode": "HTML"})
            s.update(status="posted", posted_at=self.now.isoformat(), message_id=msgs[0]["message_id"])
            left = len(self.queue())
            self.notify_admins(f"📢 Kanalga chiqdi ({slot:%H:%M}): {html.escape(self.title(pid))}\n"
                               f"Navbatda yana {left} ta tasdiqlangan post bor.")
        self.st["done_slots"] = (self.st["done_slots"] + [key])[-30:]
        self.save()

    def status_text(self):
        c = {}
        for pid in self.posts:
            st = self.ps(pid)["status"]
            c[st] = c.get(st, 0) + 1
        q = len(self.queue())
        return (f"📊 <b>Holat</b>\n"
                f"✅ Tasdiqlangan (navbatda): {q}\n"
                f"👀 Ko'rib chiqish kutilmoqda: {c.get('pending', 0)}\n"
                f"🆕 Hali yuborilmagan: {c.get('new', 0)}\n"
                f"📢 Kanalga chiqqan: {c.get('posted', 0)}\n"
                f"❌ Bekor qilingan: {c.get('rejected', 0)}\n\n"
                f"Navbatdagi post: {self.eta(1) if q else '—'}")

    def run(self):
        self.process_updates()
        self.publish_if_due()
        self.send_previews()
        self.save()


HELP_TEXT = (
    "📖 <b>Qo'llanma</b>\n\n"
    "1️⃣ Bot sizga yangi postni yuboradi.\n"
    "2️⃣ <b>✅ Tasdiqlash</b> ni bosing va narxini yozing (masalan <code>220000</code>).\n"
    "    Yoki post xabariga javob (reply) qilib narxni yozsangiz ham bo'ladi.\n"
    "3️⃣ Post navbatga tushadi va jadval bo'yicha kanalga chiqadi: "
    "08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00.\n"
    "❌ <b>Bekor qilish</b> — post kanalga chiqmaydi.\n\n"
    "/holat — nechta post navbatda\n\n"
    "⏱ Bot har 30 daqiqada xabarlarni tekshiradi, shuning uchun javob biroz kechikib keladi."
)


def main():
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        sys.exit("BOT_TOKEN topilmadi (GitHub → Settings → Secrets → Actions).")
    config = load_json(CONFIG_FILE, {})
    state = load_json(STATE_FILE, {})
    bot = Bot(Telegram(token), config, state, datetime.now(TZ))
    admin = os.environ.get("ADMIN_CHAT_ID", "").strip()
    if re.fullmatch(r"-?\d+", admin) and int(admin) not in bot.st["admin_chat_ids"]:
        bot.st["admin_chat_ids"].append(int(admin))
        bot.send(int(admin), "✅ Siz <b>VELIORA BEAUTY</b> boti admini sifatida ulandingiz.\n\n"
                             "Yangi postlar shu yerga ko'rib chiqish uchun keladi.\n/yordam — qo'llanma.")
    bot.run()
    log("tayyor")


if __name__ == "__main__":
    main()
