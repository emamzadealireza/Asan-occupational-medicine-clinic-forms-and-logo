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
        raise RuntimeError(f"Telegram API error: {payload}")
    return payload["result"]


def tg_send_message(chat_id: int, text: str):
    tg_get("sendMessage", {
        "chat_id": str(chat_id),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
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

ITEM_KEYS = {"کالا", "کالا یا خدمات", "محصول", "خدمت", "خدمات", "مورد"}

KEY_REGEX = re.compile(r"^\s*([^:：]+?)\s*[:：]\s*(.*)$")


def parse_invoice_text(raw: str) -> dict:
    inv = {
        "kind": "پیش‌فاکتور فروش کالا و خدمات",
        "date": "",
        "number": "",
        "seller": {**SELLER_DEFAULT},
        "buyer": {**NEW_PARTY},
        "items": [],   # (name, desc, qty, unit_price)
        "discount": 0.0,
        "vat": 0.0,
        "notes": [],   # توضیحات آزاد کاربر
    }

    for rawline in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = rawline.strip()
        if not line:
            continue

        m = KEY_REGEX.match(line)
        if not m:
            inv["notes"].append(line)          # متن آزاد → توضیحات تکمیلی
            continue

        key, value = m.group(1).strip(), m.group(2).strip()
        k = normalize_digits(key).replace("\u200c", "").replace("ی", "ی").replace("ک", "ک").strip()
        k_ns = k.replace(" ", "")

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

        if k_ns in ITEM_KEYS:
            parts = [p.strip() for p in re.split(r"\s*[|｜]\s*", value) if p.strip()]
            try:
                if len(parts) >= 4:
                    name, desc = parts[0], parts[1]
                    qty = parse_amount(parts[2]) or 1.0
                    unit = parse_amount(parts[3]) or 0.0
                elif len(parts) == 3:
                    name = parts[0]
                    qty = parse_amount(parts[1]) or 1.0
                    unit = parse_amount(parts[2]) or 0.0
                    desc = ""
                else:
                    name, desc, qty, unit = parts[0], "", 1.0, parse_amount(parts[1] if len(parts) > 1 else "") or 0.0
            except Exception:
                name, desc, qty, unit = value, "", 1.0, 0.0
            inv["items"].append((name, desc, qty, unit))
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
        field = None
        for fkey, fname in PARTY_FIELDS.items():
            fk = normalize_digits(fkey).replace("\u200c", "").replace(" ", "").lower()
            if k_ns == fk:
                field = fname
                break
        if field:
            inv["buyer"][field] = value
            continue

        if k_ns.startswith("فروشنده") or k_ns.startswith("خرید"):
            if k_ns.startswith("فروشنده"):
                party, role_len = "seller", len("فروشنده")
            else:
                party, role_len = "buyer", len("خریدار")
            field_key = k[role_len:].strip().lstrip("-_ ").strip()
            field_key_ns = field_key.replace(" ", "").lower()
            if not field_key_ns:
                inv[party]["name"] = value
                continue
            matched = False
            for fkey, fname in PARTY_FIELDS.items():
                fk = normalize_digits(fkey).replace("\u200c", "").replace("ی", "ی").replace("ک", "ک").replace(" ", "").lower()
                if field_key_ns == fk:
                    inv[party][fname] = value
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
HELP_TEXT = """🧾 <b>ربات پیش‌فاکتور و فاکتور — طب کار آسان</b>

اطلاعات فاکتور را در <b>یک پیام</b> بفرستید. قالب:

<pre>نوع: پیش فاکتور
تاریخ: ۱۴۰۵/۰۸/۰۷
خریدار: شرکت خدمات مهندسی شهرک
خریدار شناسه: ۱۴۰۶۴۸۸۷۷۲۹
خریدار شماره اقتصادی: ۴۱۱۴۱۸۱۰۵۹۷۹
خریدار کد پستی: ۱۸۳۴۱۸۵۰۹۳۱
خریدار نشانی: تهران - کیلومتر ۴۰ آزادراه تهران-قم، محور شهر صنعتی شمس آباد
کالا: معاینات بدو استخدام | (طب کار) کارکنان آتش نشانی | ۲۹ | ۳٬۸۷۲٬۰۰۰
تخفیف: ۰
مالیات بر ارزش افزوده: ۰
توضیح: هر متن اضافه‌ای که می‌خواهید در فاکتور درج شود.</pre>

<b>قواعد:</b>
• <code>نوع</code>: <code>پیش فاکتور</code> یا <code>فاکتور</code> (بدون این کلید، پیش‌فاکتور ساخته می‌شود)
• <code>کالا</code>: چهار بخش با <code>|</code> → نام | شرح | تعداد | قیمت واحد
   بدون شرح: <code>نام | تعداد | قیمت</code>
   می‌توانید چند خط <code>کالا</code> بفرستید
• <code>خریدار</code>: اسم را جلوی <code>خریدار:</code> بنویسید؛ بقیه موارد (شناسه، شماره اقتصادی، کد پستی، نشانی، شماره تماس) همه اختیاری‌اند
• اطلاعات فروشنده از قبل (طب کار آسان) درج می‌شود؛ برای تغییر، همان کلیدها را با پیشوند <code>فروشنده</code> بفرستید
• <code>تخفیف</code> و <code>مالیات بر ارزش افزوده</code> اختیاری‌اند
• هر خطی که کلید نباشد، در بخش «توضیحات تکمیلی» فاکتور نمایش داده می‌شود — پس هر توضیح اضافه‌ای آزادانه بنویسید
• اعداد را با هر نویسه‌ای می‌توانید بنویسید (فارسی/انگلیسی، با یا بدون جداکننده)

سند PDF هم در همین چت به شما ارسال می‌شود و هم در پوشه «فاکتورهای-تولیدشده» کنار اسکریپت ذخیره می‌شود."""


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

    html_text = build_html(inv)
    total = sum(q * u for _, _, q, u in inv["items"])
    base_name = sanitize_filename(inv["kind"].replace("\u200c", "")) + "-" + \
        sanitize_filename(inv["date"] or "") + "-" + time.strftime("%H%M%S")
    out_pdf = os.path.join(OUTPUT_DIR, base_name + ".pdf")
    pdf_from_html(html_text, out_pdf)

    caption = (
        f"<b>{html.escape(inv['kind'])}</b>"
        + (f" — {html.escape(inv['date'])}" if inv["date"] else "")
        + f"\nجمع کل: {fmt_amount(total)} تومان"
        + f"\nمبلغ قابل پرداخت: {fmt_amount(total + inv['vat'] - inv['discount'])} تومان"
    )
    tg_send_document(chat_id, out_pdf, caption=caption)
    archive_pdf_to_git(out_pdf, inv["kind"], inv["date"] or "بدون-تاریخ")


def main():
    if not os.path.exists(TOKEN_FILE) or not bot_token():
        print("=" * 56)
        print("توکن ربات تنظیم نشده است.")
        print("1) در تلگرام به  @BotFather  پیام دهید و /newbot را بزنید.")
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
            return
        except Exception as loop_err:
            print("loop error:", repr(loop_err))
            time.sleep(3)


if __name__ == "__main__":
    main()
