# -*- coding: utf-8 -*-
"""
ربات پیش‌فاکتور و فاکتور فروش — کلینیک طب کار آسان
(پلتفرم پیش‌فرض: بله/بأله بدون نیاز به فیلترشکن — هوی تلگرام هم پشتیبانی می‌شود)
===============================================================
- بدون نیاز به نصب هیچ کتابخانه‌ای (فقط پایتون ۳.۸ به بالا)
- پیام‌ها را با long polling می‌گیرد (نیازی به دامنه، سرور یا SSL ندارد)
- خروجی: PDF مشابه نمونه (فارسی، راست‌به‌چپ، لوگو، اعداد فارسی)
- تولید PDF با مرورگر Edge
- پلتفرم: پیش‌فرض «بله» است؛ برای کار با تلگرام، در فایل platform.txt بنویسید: telegram

راه‌اندازی:
  1) توکن ربات را از BotFather (در اپ «بله» یا تلگرام) بگیرید و در فایل bot_token.txt قرار دهید.
  2) اجرا:  python invoice_bot.py
  3) به ربات پیام بدهید. /start را بزنید تا راهنمای قالب پیام را ببینید.
"""

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import html
import json
import os
import re
import socket as _socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    import socks as _socks_mod
    _SOCKS_READY = True
except Exception:
    _SOCKS_READY = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "فاکتورهای-تولیدشده")
TOKEN_FILE = os.path.join(BASE_DIR, "bot_token.txt")
PROXY_FILE = os.path.join(BASE_DIR, "proxy.txt")
PLATFORM_FILE = os.path.join(BASE_DIR, "platform.txt")
PROFILES_FILE = os.path.join(BASE_DIR, "profiles.json")

API_BASE_BALE = "https://tapi.bale.ai"        # بله — بدون نیاز به فیلترشکن (پیش‌فرض)
API_BASE_TELEGRAM = "https://api.telegram.org"


def api_base() -> str:
    """پلتفرم ربات: بله (پیش‌فرض) یا تلگرام — با فایل platform.txt قابل تغییر"""
    try:
        with open(PLATFORM_FILE, "r", encoding="utf-8-sig") as f:
            p = f.read().strip().lower()
        if "telegram" in p:
            return API_BASE_TELEGRAM
    except OSError:
        pass
    return API_BASE_BALE

LOGO_BASE64_PATHS = [
    os.path.join(BASE_DIR, "logo-final-with-text-base64.txt"),
    r"D:\کلینیک خصوصی طب کار\لوگو کلینیک\logo-final-with-text-base64.txt",
]

# ----------------------------------------------------------------------
# اطلاعات پیش‌فرض فروشنده (کلینیک) — اگر در پیام مقدار بفرستید بازنویسی می‌شود
# ----------------------------------------------------------------------
SELLER_DEFAULT = {
    "name": "طب کار آسان",
    "id": "۵۰۹۰۰۵۱۸۷۳",
    "economic": "۴۱۱۴۱۸۱۰۵۹۷۹",
    "postal": "۱۸۳۴۱۸۵۰۹۳۱",
    "phone": "۰۹۱۲۴۴۸۵۶۲۶",
    "address": "تهران - شهرک صنعتی شمس آباد - بعد از بهار دستپین - نبش بهار بهرستان - نبش چهارم دستکن - بخش حسن آباد - شهرستان ری",
}

NEW_PARTY = {"name": "", "id": "", "economic": "", "postal": "", "phone": "", "address": ""}

# ----------------------------------------------------------------------
# ابزارهای کمکی
# ----------------------------------------------------------------------
FA_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
EN_TO_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def normalize_digits(s: str) -> str:
    return str(s).translate(FA_TO_EN)


def fa_digits(s: str) -> str:
    return str(s).translate(EN_TO_FA)


def fmt_amount(v: float) -> str:
    s = f"{int(round(float(v))):,}"
    return fa_digits(s.replace(",", "٬"))


def parse_amount(s: str):
    s = re.sub(r"[^\d.−-]", "", normalize_digits(s).replace("٫", ".").replace("٬", "").replace(",", ""))
    if s in ("", "-", "−"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ----------------------------------------------------------------------
# تلگرام
# ----------------------------------------------------------------------
_TOKEN_CACHE = None
_TOKEN_MTIME = 0


def bot_token() -> str:
    global _TOKEN_CACHE, _TOKEN_MTIME
    st = os.stat(TOKEN_FILE)
    if _TOKEN_CACHE is None or st.st_mtime != _TOKEN_MTIME:
        with open(TOKEN_FILE, "r", encoding="utf-8-sig") as f:
            _TOKEN_CACHE = f.read().strip()
        _TOKEN_MTIME = st.st_mtime
    return _TOKEN_CACHE


# ----------------------------------------------------------------------
# تشخیص خودکار پروکسی/VPN و اتصال
# ----------------------------------------------------------------------
_SOCKS_ACTIVE_URL = None
_OPENER = None
_COMMON_PORTS = (10809, 10808, 7890, 7897, 2080, 8118, 3128, 8890)


def _proxy_candidates():
    cands = []
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, "r", encoding="utf-8-sig") as f:
                for line in f.read().strip().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        cands.append(line if "://" in line else "http://" + line)
        except OSError:
            pass
    for port in _COMMON_PORTS:
        cands.append(f"http://127.0.0.1:{port}")
        if _SOCKS_READY:
            cands.append(f"socks5h://127.0.0.1:{port}")
    return cands


def _socks_enable(url: str) -> None:
    """فعال‌سازی پروکسی SOCKS برای کل پروسه (نیازمند pysocks)"""
    global _SOCKS_ACTIVE_URL
    if _SOCKS_ACTIVE_URL == url:
        return
    m = re.match(r"socks5h?://([^:/]+):(\d+)", url)
    if not m:
        raise ValueError(f"آدرس پروکسی نامعتبر: {url}")
    host, port = m.group(1), int(m.group(2))
    _socks_mod.socks.setdefaultproxy(_socks_mod.socks.PROXY_TYPE_SOCKS5, host, port, rdns=True)
    _socket.socket = _socks_mod.socksocket
    _SOCKS_ACTIVE_URL = url


def _opener_for(proxy_url):
    if proxy_url is None:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if proxy_url.startswith("socks"):
        _socks_enable(proxy_url)
        return urllib.request.build_opener()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))


def _probe(opener) -> bool:
    try:
        with opener.open(f"{api_base()}/bot{bot_token()}/getMe", timeout=6) as resp:
            return bool(json.loads(resp.read().decode("utf-8")).get("ok"))
    except Exception:
        return False


def reset_opener():
    global _OPENER
    _OPENER = None


def get_opener():
    """اول برقراری مستقیم، بعد پروکسی‌های رایج محلی را امتحان می‌کند"""
    global _OPENER
    if _OPENER is not None:
        return _OPENER
    direct = _opener_for(None)
    if _probe(direct):
        _OPENER = direct
        return _OPENER
    for c in _proxy_candidates():
        try:
            opener = _opener_for(c)
        except Exception:
            continue
        if _probe(opener):
            print(f"اتصال از طریق پروکسی: {c}")
            _OPENER = opener
            return _OPENER
    # هیچ مسیری موفق نبود — مستقیم برمی‌گردیم تا دوباره تلاش شود
    return direct


def tg_get(method: str, params: dict, timeout: int):
    url = f"{api_base()}/bot{bot_token()}/{method}?" + urllib.parse.urlencode(params)
    try:
        with get_opener().open(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError):
        reset_opener()   # دفعه بعد مسیر اتصال دوباره بررسی می‌شود
        raise
    if not payload.get("ok"):
        raise RuntimeError(f"{api_base()} API error: {payload}")
    return payload["result"]


def tg_send_message(chat_id: int, text: str):
    tg_get("sendMessage", {
        "chat_id": str(chat_id),
        "text": text,
    }, 30)


def tg_send_document(chat_id: int, path: str, caption: str = ""):
    url = f"{api_base()}/bot{bot_token()}/sendDocument"
    boundary = "----AsanBot" + uuid.uuid4().hex
    filename = os.path.basename(path)
    with open(path, "rb") as f:
        file_bytes = f.read()

    def part(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    head = part("chat_id", str(chat_id))
    if caption:
        head += part("caption", caption)
    head += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8")

    payload = head + file_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(url, data=payload)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with get_opener().open(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError):
        reset_opener()
        raise
    if not result.get("ok"):
        raise RuntimeError(f"sendDocument failed: {result}")


def load_logo_data_uri() -> str:
    for p in LOGO_BASE64_PATHS:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                data = f.read().strip()
            if data.startswith("data:image"):
                return data
    return ""


# ----------------------------------------------------------------------
# تجزیه پیام کاربر
# ----------------------------------------------------------------------
PARTY_FIELDS = {
    "نام": "name",
    "نام شرکت": "name",
    "نام شرکت یا مجموعه": "name",
    "نام یا مجموعه": "name",
    "نام مجموعه": "name",
    "شناسه": "id",
    "شناسه ملی": "id",
    "شماره ملی": "id",
    "شماره اقتصادی": "economic",
    "کد پستی": "postal",
    "نشانی": "address",
    "ادرس": "address",
    "آدرس": "address",
    "شماره تماس": "phone",
    "تلفن": "phone",
    "موبایل": "phone",
}

ITEM_KEYS = {"کالاجانبی", "کالا", "محصول", "خدمت", "خدمات", "مورد"}

KEY_REGEX = re.compile(r"^\s*([^:：]+?)\s*[:：]\s*(.*)$")

# ----------------------------------------------------------------------
# تطبیق هوشمند کلیدها (خطای تایپی یا ترتیب متفاوت)
# ----------------------------------------------------------------------
KNOWN_FIELD_KEYS = None  # ساخته می‌شود در اولین فراخوانی


def _lev(a: str, b: str) -> int:
    """فاصله ادیت (Levenshtein) ساده"""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _norm_key(s: str) -> str:
    s = normalize_digits(s)
    for a, b in (("ي", "ی"), ("ك", "ک"), ("ة", "ه"), ("أ", "ا"), ("إ", "ا"), ("ٱ", "ا"), ("ؤ", "و"), ("ئ", "ی")):
        s = s.replace(a, b)
    return s.replace("\u200c", "").replace(" ", "").lower()


def _candidate_keys():
    global KNOWN_FIELD_KEYS
    if KNOWN_FIELD_KEYS is not None:
        return KNOWN_FIELD_KEYS
    out = []
    core = ("نوع", "تاریخ", "شماره", "شمارهفاکتور", "شمارهسند", "تخفیف",
            "مالیات", "مالیاتبرارزشافزوده", "توضیج", "توضیحات", "یادداشت", "متناضافه")
    out += list(core)
    for p in ("فروشنده", "خریدار"):
        out.append(p)
        for f in PARTY_FIELDS:
            fn = _norm_key(f)
            out.append(p + fn)     # «شناسهخریدار»
            out.append(fn + p)     # «شناسهخریدار» (ترتیب کمی متفاوت)
    out = list(dict.fromkeys(out))
    KNOWN_FIELD_KEYS = out
    return out


def _canonical_key(k_ns: str) -> str:
    """کلید نزدیک‌ترین شکل استاندارد (در حد خطای تایپی) را برمی‌گرداند"""
    if not k_ns:
        return k_ns
    # کلیدهای کالا ممکن است شماره‌دار باشند: «کالا12»
    m = re.match(r"^(.*?)(\d*)$", k_ns)
    body, digits = m.group(1), m.group(2)
    item_best, item_d = None, 99
    for t in ("کالایاخدمات", "کالا", "محصول", "خدمت", "خدمات", "مورد", "شرح", "تعداد", "قیمتواحد", "قیمت"):
        d = _lev(body, t)
        if d < item_d:
            item_best, item_d = t, d
    if item_best and item_d <= 1 and (body == item_best or item_d <= 1):
        return item_best + digits
    best, best_d = k_ns, 99
    for c in _candidate_keys():
        d = _lev(k_ns, c)
        if d < best_d:
            best, best_d = c, d
    tol = 1 if (best and len(best) <= 6) else 2
    if best and best_d <= tol:
        return best
    return k_ns


def parse_invoice_text(raw: str) -> dict:
    inv = {
        "kind": "پیش‌فاکتور فروش کالا و خدمات",
        "date": "",
        "number": "",
        "seller": {**SELLER_DEFAULT},
        "seller_overrides": {},   # کلیدهای صریح فروشنده در همین پیام
        "buyer": {**NEW_PARTY},
        "items": [],   # (name, desc, qty, unit_price)
        "slots": {},   # شماره ردیف → {"name","desc","qty","unit"}
        "discount": 0.0,
        "vat": 0.0,
        "notes": [],   # توضیحات آزاد کاربر
    }
    last_idx = [0]  # آخرین شماره ردیف فعال برای کلیدهای بدون شماره

    for rawline in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = rawline.strip()
        if not line:
            continue

        m = KEY_REGEX.match(line)
        if not m:
            inv["notes"].append(line)          # متن آزاد → توضیحات تکمیلی
            continue

        key, value = m.group(1).strip(), m.group(2).strip()
        k_ns = _canonical_key(_norm_key(key))
        k = k_ns

        if k_ns in ("نوع",):
            v = normalize_digits(value)
            if v.startswith("پیش"):
                inv["kind"] = "پیش‌فاکتور فروش کالا و خدمات"
            elif "فاکتور" in v:
                inv["kind"] = "فاکتور فروش کالا و خدمات"
            continue

        if k_ns in ("تاریخ", "تاریخ"):
            inv["date"] = value
            continue

        if k_ns in ("شماره", "شماره فاکتور", "شماره پیشفاکتور", "شماره پیش فاکتور", "شماره سند"):
            inv["number"] = value
            continue

        mat = re.match(r"^(کالایاخدمات|کالا|محصول|خدمت|خدمات|مورد|قیمتواحد|قیمت|شرح|تعداد)(\d*)$", k_ns)
        if mat:
            base_key, idx_s = mat.group(1), mat.group(2)
            fname = {"شرح": "desc", "تعداد": "qty", "قیمت": "unit", "قیمتواحد": "unit"}.get(base_key, "name")
            if idx_s:
                idx = int(idx_s)
                last_idx[0] = idx
            elif fname == "name" or not inv["slots"] or last_idx[0] not in inv["slots"]:
                idx = (max(inv["slots"]) + 1) if inv["slots"] else 1
                last_idx[0] = idx
            else:
                idx = last_idx[0]
            slot = inv["slots"].setdefault(idx, {})
            if fname == "name":
                parts = [p.strip() for p in re.split(r"\s*[|｜]\s*", value) if p.strip()]
                slot["name"] = parts[0]
                if len(parts) == 3:
                    slot["qty"] = parse_amount(parts[1]) or 1.0
                    slot["unit"] = parse_amount(parts[2]) or 0.0
                elif len(parts) >= 4:
                    slot["desc"] = parts[1]
                    slot["qty"] = parse_amount(parts[2]) or 1.0
                    slot["unit"] = parse_amount(parts[3]) or 0.0
            elif fname == "desc":
                slot["desc"] = value
            elif fname == "qty":
                slot["qty"] = parse_amount(value) or 1.0
            elif fname == "unit":
                slot["unit"] = parse_amount(value) or 0.0
            continue

        if k_ns == "تخفیف" or k_ns.endswith("تخفیف"):
            am = parse_amount(value)
            if am is not None:
                inv["discount"] = am
            continue

        if k_ns.startswith("مالیات") or "ارزش افزوده" in k_ns:
            am = parse_amount(value)
            if am is not None:
                inv["vat"] = am
            continue

        # کلید بدون پیشوند فروشنده/خریدار (مثل «کد پستی») → خریدار
        # کلیدهای طرفین؛ هر دو ترتیب پذیرفته می‌شود:
        # «شناسه خریدار» / «خریدار شناسه» / «نشانی خریدار» / «شناسه» / «فروشنده:» / ...
        fk_norm = lambda s: normalize_digits(s).replace("\u200c", "").replace(" ", "").replace("ی", "ی").replace("ک", "ک").lower()
        party = None
        rest = k_ns
        if "فروشنده" in k_ns:
            party, rest = "seller", k_ns.replace("فروشنده", "")
        elif "خریدار" in k_ns:
            party, rest = "buyer", k_ns.replace("خریدار", "")
        else:
            for fkey, fname in PARTY_FIELDS.items():
                if k_ns == fk_norm(fkey):
                    party, rest = "buyer", k_ns
                    break
        if party:
            rest_ns = rest.replace(" ", "").strip("-_")
            if not rest_ns:
                inv[party]["name"] = value
                if party == "seller":
                    inv["seller_overrides"]["name"] = value
                continue
            matched = False
            for fkey, fname in PARTY_FIELDS.items():
                if rest_ns == fk_norm(fkey):
                    inv[party][fname] = value
                    if party == "seller":
                        inv["seller_overrides"][fname] = value
                    matched = True
                    break
            if not matched:
                inv["notes"].append(line)
            continue

        if k_ns in ("توضیح", "توضیحات", "توضیح", "توضیحات", "شرح", "متن اضافه", "یادداشت", "توضیحات اضافه"):
            if value:
                inv["notes"].append(value)
            # سطرهای بعدی بدون کلید هم ادامه همین توضیح هستند
            continue

        inv["notes"].append(line)

    # تبدیل ردیف‌ها به آیتم‌های نهایی به ترتیب شماره
    final_items = []
    for idx in sorted(inv["slots"]):
        s = inv["slots"][idx]
        name = s.get("name") or ""
        if not name:
            # ردیف بدون نام؛ برای از دست نرفتن اطلاعات به توضیحات می‌رود
            bits = []
            if s.get("desc"):
                bits.append(s["desc"])
            if s.get("qty") is not None:
                bits.append("تعداد: " + fmt_amount(s["qty"]))
            if s.get("unit"):
                bits.append("قیمت واحد: " + fmt_amount(s["unit"]))
            if bits:
                inv["notes"].append("ردیف " + to_persian_digits(str(idx)) + ": " + " | ".join(bits))
            continue
        desc = s.get("desc") or ""
        qty = s.get("qty") if s.get("qty") is not None else 1.0
        unit = s.get("unit") or 0.0
        final_items.append((name, desc, qty, unit))
    inv["items"] = final_items

    return inv


# ----------------------------------------------------------------------
# ساخت HTML
# ----------------------------------------------------------------------
def esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def build_html(inv: dict) -> str:
    logo_uri = load_logo_data_uri()
    logo = f'<img src="{logo_uri}" alt="logo">' if logo_uri else ""

    total = sum(q * u for _, _, q, u in inv["items"])
    payable = total + inv["vat"] - inv["discount"]

    item_rows = "\n".join(
        f"<tr>"
        f'<td class="name-td">{esc(name)}</td>'
        f'<td class="desc-td">{esc(desc)}</td>'
        f"<td>{fmt_amount(qty)}</td>"
        f"<td>{fmt_amount(unit)}</td>"
        f"<td>{fmt_amount(qty * unit)}</td>"
        f"</tr>"
        for name, desc, qty, unit in inv["items"]
    ) or '<tr><td colspan="5" style="text-align:center;color:#9ca3af">—</td></tr>'

    free_notes_html = ""
    if inv["notes"]:
        items = "".join(f"<li>{esc(n)}</li>" for n in inv["notes"])
        free_notes_html = (
            '<table style="margin-top:3mm">'
            '<tr class="section-title"><td>توضیحات تکمیلی</td></tr>'
            f'<tr class="free-notes"><td><ul>{items}</ul></td></tr></table>'
        )

    date_html = f'<span>تاریخ : {esc(inv["date"])}</span>' if inv["date"] else ""
    num_html = f'<div class="num">شماره : {esc(inv["number"])}</div>' if inv["number"] else ""
    date_html = f'<div class="num">{date_html}</div>' if date_html else ""

    def party_html(p: dict) -> str:
        id_line = f"<span><span class='klabel'>شناسه / شماره ملی : </span>{esc(p['id'] or '-')}</span>"
        tel_line = f"<span><span class='klabel'>شماره تماس : </span>{esc(p['phone'] or '-')}</span>"
        return (
            f"<span class='klabel'>{esc(p['name'])}</span><br>"
            f"{id_line}"
            f"<span style='display:inline-block;width:10mm'></span>"
            f"<span><span class='klabel'>شماره اقتصادی : </span>{esc(p['economic'] or '-')}</span>"
            f"<span style='display:inline-block;width:10mm'></span>"
            f"<span><span class='klabel'>کد پستی : </span>{esc(p['postal'] or '-')}</span><br>"
            f"{tel_line}"
        )

    s, b = inv["seller"], inv["buyer"]

    html_out = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<title>{esc(inv['kind'])}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  @page {{ size: A4; margin: 10mm 8mm 8mm 8mm; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{ font-family: 'Vazirmatn', 'Segoe UI', Tahoma, sans-serif; color: #111827; font-size: 12.5px; direction: rtl; }}
  .top {{ display: flex; align-items: center; margin-bottom: 4mm; }}
  .logo {{ width: 34mm; }}
  .logo img {{ width: 30mm; height: auto; display: block; }}
  .title-box {{ flex: 1; text-align: center; border-top: 1.5px solid #d0d5dd; border-bottom: 1.5px solid #d0d5dd; padding: 5mm 4mm; }}
  .title {{ font-size: 19px; font-weight: 700; color: #17497B; }}
  .date {{ width: 44mm; text-align: right; font-weight: 600; font-size: 12.5px; }}
  .num {{ font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 2mm; }}
  td, th {{ border: 1.2px solid #374151; padding: 5px 8px; vertical-align: top; }}
  .section-title td {{ background: #f3f4f6; text-align: center; font-weight: 700; font-size: 13.5px; padding: 4px 6px; }}
  .kv td {{ border: 1.2px solid #374151; }}
  .kv .k {{ font-weight: 700; white-space: nowrap; width: 36mm; }}
  .kv .v {{ text-align: justify; }}
  .klabel {{ font-weight: 600; }}
  table.items th {{ background: #f3f4f6; text-align: center; font-weight: 700; font-size: 13px; padding: 5px 6px; }}
  table.items td {{ text-align: center; }}
  .name-td {{ text-align: right; font-weight: 600; }}
  .desc-td {{ text-align: right; }}
  .sum-label {{ font-weight: 700; text-align: center; }}
  .sum-val {{ font-weight: 700; text-align: center; }}
  .pay-box td {{ text-align: center; font-weight: 700; font-size: 13.5px; background: #f9fafb; }}
  .free-notes td {{ text-align: right; font-size: 12px; line-height: 1.9; }}
  .free-notes ul {{ margin: 0; padding: 0 12px 0 0; }}
  .free-notes li {{ margin: 1px 0; }}
  .signs {{ margin-top: 9mm; display: flex; }}
  .signs .s {{ width: 50%; font-weight: 600; font-size: 13px; }}
  .signs .s.l {{ text-align: left; padding-left: 15mm; }}
  .signs .s.r {{ text-align: right; padding-right: 15mm; }}
</style>
</head>
<body>
  <div class="top">
    <div class="date">{date_html}{num_html}</div>
    <div class="title-box"><div class="title">{esc(inv['kind'])}</div></div>
    <div class="logo">{logo}</div>
  </div>

  <table>
    <tr class="section-title"><td>مشخصات فروشنده</td></tr>
  </table>
  <table class="kv">
    <tr><td class="k">نام شرکت یا مجموعه : </td><td class="v">{party_html(s)}</td></tr>
    <tr><td class="k">نشانی : </td><td class="v" style="text-align:justify">{esc(s['address'] or '-')}</td></tr>
  </table>

  <table>
    <tr class="section-title"><td>مشخصات خریدار</td></tr>
  </table>
  <table class="kv">
    <tr><td class="k">نام شرکت یا مجموعه : </td><td class="v">{party_html(b) if b['name'] else '-'}</td></tr>
    <tr><td class="k">نشانی : </td><td class="v" style="text-align:justify">{esc(b['address'] or '-')}</td></tr>
  </table>

  <table>
    <tr class="section-title"><td>مشخصات کالا یا خدمات</td></tr>
  </table>
  <table class="items">
    <thead>
      <tr>
        <th style="width:34mm">نام کالا یا خدمات</th>
        <th>شرح</th>
        <th style="width:18mm">تعداد</th>
        <th style="width:27mm">قیمت واحد</th>
        <th style="width:27mm">جمع</th>
      </tr>
    </thead>
    <tbody>
      {item_rows}
      <tr>
        <td class="sum-label" colspan="4">جمع {'پیش‌فاکتور' if 'پیش' in inv['kind'] else 'فاکتور'}</td>
        <td class="sum-val">{fmt_amount(total)}</td>
      </tr>
    </tbody>
  </table>

  <table>
    <tr>
      <td style="width:30mm;font-weight:700">توضیحات :</td>
      <td>
        <span style="font-weight:600">تخفیف : </span>{fmt_amount(inv['discount'])}
        <span style="display:inline-block;width:14mm"></span>
        <span style="font-weight:600">مالیات بر ارزش افزوده : </span>{fmt_amount(inv['vat'])}
        <span style="display:inline-block;width:14mm"></span>
      </td>
    </tr>
    <tr class="pay-box">
      <td colspan="2" style="text-align:center">مبلغ قابل پرداخت : {fmt_amount(payable)} تومان</td>
    </tr>
  </table>

  {free_notes_html}

  <div class="signs">
    <div class="s r">مهر و امضای فروشنده : </div>
    <div class="s l">مهر و امضای خریدار : </div>
  </div>
</body>
</html>
"""
    return html_out


# ----------------------------------------------------------------------
# تبدیل HTML به PDF از طریق Edge
# ----------------------------------------------------------------------
def find_edge() -> str:
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if os.path.exists(p):
            return p
    return r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def pdf_from_html(html_text: str, out_pdf: str) -> None:
    tmp = os.path.join(tempfile.gettempdir(), f"bill_{uuid.uuid4().hex}.html")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html_text)
    try:
        cmd = [
            find_edge(),
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=12000",
            f"--print-to-pdf={out_pdf}",
            "file:///" + tmp.replace("\\", "/"),
        ]
        subprocess.run(cmd, capture_output=True, timeout=90)
    finally:
        for _ in range(25):
            if os.path.exists(out_pdf) and os.path.getsize(out_pdf) > 2000:
                break
            time.sleep(0.3)
        try:
            os.remove(tmp)
        except OSError:
            pass
    if not os.path.exists(out_pdf) or os.path.getsize(out_pdf) < 2000:
        raise RuntimeError("PDF ساخته نشد (Edge یافت نشد یا صفحه خالی بود)")


def sanitize_filename(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "-", str(s)).strip() or "فاکتور"


def archive_pdf_to_git(pdf_path: str, kind: str, date_str: str) -> None:
    """PDF تولیدشده را در مخزن گیت (GitHub) ثبت و ارسال می‌کند - بدون توقف چت"""
    def work():
        try:
            rel = os.path.relpath(pdf_path, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            subprocess.run(["git", "add", rel], cwd=BASE_DIR, capture_output=True, timeout=60)
            msg = f"فاکتور جدید: {kind} - {date_str} - {time.strftime('%Y-%m-%d %H:%M')}"
            subprocess.run(["git", "commit", "-m", msg], cwd=BASE_DIR, capture_output=True, timeout=60)
            subprocess.run(["git", "push", "origin"], cwd=BASE_DIR, capture_output=True, timeout=120)
        except Exception as e:
            print("archive error:", repr(e))
    threading.Thread(target=work, daemon=True).start()


# ----------------------------------------------------------------------
# پیام راهنما
# ----------------------------------------------------------------------
HELP_TEXT = """🧾 ساخت پیش‌فاکتور و فاکتور فروش

همه اطلاعات را در یک پیام بفرستید، مثل این:

نوع: پیش فاکتور (یا فاکتور)
تاریخ: ۱۴۰۵/۰۸/۰۷
شماره: ۱۲۳ (اختیاری)
خریدار: اسم شرکت یا شخص
شناسه خریدار: ۴۱۱۴۸۱۸۶۶۹۱۸ (اختیاری)
شماره اقتصادی خریدار: ۴۱۱۴۱۸۱۰۵۹۷۹ (اختیاری)
کد پستی خریدار: ۱۸۳۴۱۸۵۰۹۳۱ (اختیاری)
نشانی خریدار: تهران - کیلومتر ۴۰ آزاد راه تهران-قم (اختیاری)
شماره تماس خریدار: ۰۹۱۲۳۴۵۶۷۸۹ (اختیاری)
کالا ۱: معاینات بدو استخدام
شرح ۱: (طب کار) کارکنان آتش نشانی (اختیاری)
تعداد ۱: ۲۹ (اختیاری - پیش‌فرض ۱)
قیمت واحد ۱: ۳٬۸۷۲٬۰۰۰
کالا ۲: معاینات ادواری (اختیاری - برای ردیف بیشتر شماره را اضافه کنید)
تعداد ۲: ۱۰ (اختیاری)
قیمت واحد ۲: ۵۰۰٬۰۰۰ (اختیاری)
تخفیف: ۰ (اختیاری)
مالیات بر ارزش افزوده: ۰ (اختیاری)
توضیح: متن توضیحات تکمیلی فاکتور (اختیاری)

نکته‌ها:
• هر خطی که کلید نباشد هم در بخش «توضیحات تکمیلی» فاکتور می‌آید.
• برای تغییر مشخصات فروشنده (پیش‌فرض: طب کار آسان) یک بار بنویسید:
  فروشنده: اسم شما
  فروشنده شناسه: ...
  فروشنده نشانی: ...
  فروشنده شماره تماس: ۰۹…
  فقط موارد لازم را بفرستید؛ ذخیره می‌شود و دفعات بعد تکرار لازم نیست.
• بعد از ارسال، PDF در همین چت فرستاده می‌شود."""


# ----------------------------------------------------------------------
# حلقه اصلی
# ----------------------------------------------------------------------
def handle_message(msg: dict, offset_holder: list):
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")
    if not text:
        return
    if text.startswith("/start") or text.startswith("/help") or text == "راهنما" or text == "help":
        tg_send_message(chat_id, HELP_TEXT)
        return
    if text.startswith("/"):
        tg_send_message(chat_id, "دستور ناشناخته است. /help را بزنید.")
        return

    inv = parse_invoice_text(text)
    if not inv["items"] and not inv["notes"]:
        tg_send_message(chat_id, "اطلاعاتی در پیام پیدا نشد. برای دیدن قالب، /help را بفرستید.")
        return

    # اطلاعات فروشنده: پروفایل شخصی هر کاربر + تغییرات صریح همین پیام
    profile = dict(SELLER_DEFAULT)
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8-sig") as f:
                profiles = json.load(f)
            saved = profiles.get(str(chat_id))
            if isinstance(saved, dict) and saved:
                profile = saved
        except Exception:
            pass
    if inv["seller_overrides"]:
        profile = {**profile, **inv["seller_overrides"]}
        try:
            profiles = {}
            if os.path.exists(PROFILES_FILE):
                with open(PROFILES_FILE, "r", encoding="utf-8-sig") as f:
                    profiles = json.load(f)
            profiles[str(chat_id)] = profile
            with open(PROFILES_FILE, "w", encoding="utf-8") as f:
                json.dump(profiles, f, ensure_ascii=False, indent=1)
            tg_send_message(
                chat_id,
                "✔ اطلاعات فروشنده برای پیام‌های بعدی شما ذخیره شد.",
            )
        except Exception as e:
            print("profile save error:", repr(e))
    inv["seller"] = profile

    html_text = build_html(inv)
    total = sum(q * u for _, _, q, u in inv["items"])
    base_name = sanitize_filename(inv["kind"].replace("\u200c", "")) + "-" + \
        sanitize_filename(inv["date"] or "") + "-" + time.strftime("%H%M%S")
    out_pdf = os.path.join(OUTPUT_DIR, base_name + ".pdf")
    pdf_from_html(html_text, out_pdf)

    caption = (
        inv["kind"]
        + (f" — {inv['date']}" if inv["date"] else "")
        + f"\nجمع کل: {fmt_amount(total)} تومان"
        + f"\nمبلغ قابل پرداخت: {fmt_amount(total + inv['vat'] - inv['discount'])} تومان"
    )
    tg_send_document(chat_id, out_pdf, caption=caption)
    archive_pdf_to_git(out_pdf, inv["kind"], inv["date"] or "بدون-تاریخ")


def main():
    # جلوگیری از اجرای هم‌زمان چند نمونه ربات
    LOCK_FILE = os.path.join(BASE_DIR, "bot.lock")
    import ctypes
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE, "r", encoding="utf-8-sig") as f:
                old_pid = int((f.read().strip() or "0").split(".")[0] or 0)
            alive = False
            if old_pid:
                h = ctypes.windll.kernel32.OpenProcess(0x1000, False, old_pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                if h:
                    ctypes.windll.kernel32.CloseHandle(h)
                    alive = True
            if alive and old_pid != os.getpid():
                print("ربات از قبل در حال اجراست (PID " + str(old_pid) + ") — این نمونه بسته شد.")
                return
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        print("lock error:", repr(e))

    if not os.path.exists(TOKEN_FILE) or not bot_token():
        print("=" * 56)
        print("توکن ربات تنظیم نشده است.")
        print("1) در اپ بله به @BotFather پیام دهید و /newbot را بزنید.")
        print("2) توکن دریافتی (به شکل 123456:AAA... ) را")
        print("   داخل این فایل بگذارید و ذخیره کنید:")
        print("   " + TOKEN_FILE)
        if not os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write("")
        print("3) دوباره این اسکریپت را اجرا کنید:  python invoice_bot.py")
        print("=" * 56)
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("ربات روشن شد — منتظر پیام است… (Ctrl+C برای توقف)")

    offset = 0
    while True:
        try:
            updates = tg_get("getUpdates", {"offset": str(offset), "timeout": "25"}, 40)
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                try:
                    handle_message(msg, None)
                except Exception as e:
                    print("handle error:", repr(e))
                    try:
                        tg_send_message(msg["chat"]["id"],
                                        "⛔ خطا در پردازش پیام:\n" + html.escape(str(e)) +
                                        "\nبرای مشاهده قالب، /help را بفرستید.")
                    except Exception:
                        pass
        except KeyboardInterrupt:
            print("ربات متوقف شد.")
            try:
                os.remove(LOCK_FILE)
            except OSError:
                pass
            return
        except Exception as loop_err:
            print("loop error:", repr(loop_err))
            time.sleep(3)


if __name__ == "__main__":
    main()
