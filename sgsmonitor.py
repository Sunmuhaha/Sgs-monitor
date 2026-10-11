"""
SGS monitor สำหรับ Termux (ไม่ใช้เบราว์เซอร์ ใช้ requests ล้วน)

ติดตั้งใน Termux:
    pkg update && pkg install python
    pip install requests beautifulsoup4

รัน:
    python sgs_monitor_termux.py

ให้รันต่อเมื่อปิดจอ:
    termux-wake-lock
    (และปิด battery optimization ของแอป Termux)
"""
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ใส่ข้อมูลตรงนี้ได้เลย ข้อมูลทั้งหมดอยู่ในเครื่องที่รัน ไม่หลุดไปไหนแน่นอน
SGS_USER = "ใส่_user_ตรงนี้" #เช่น 37301
SGS_PASS = "ใส่_รหัส_ตรงนี้" #เช่น 1234567890123
# LINE Messaging API (จาก LINE Developers Console > ช่อง Messaging API ของคุณ)
LINE_TOKEN = "ใส่_โทเคน_Line_ตรงนี้"   # แท็บ Messaging API > Channel access token (long-lived)
# ผู้รับ: ใส่ได้หลายอัน ทั้ง user ID (ขึ้นต้น U) และ group ID (ขึ้นต้น C)
LINE_TARGETS = [
    "ใส่ตรงนี้",     # Basic settings > Your user ID
    "ใส่ตรงนี้",           # ไอดีกลุ่ม (ขึ้นต้นด้วย C) ดูวิธีหาในคำอธิบาย
]
# ถ้าตั้งเป็น [] จะส่งแบบ broadcast ให้ทุกคนที่เป็นเพื่อนกับบอท (ไม่ส่งเข้ากลุ่ม)

SIGNIN_URL = "https://sgs.bopp-obec.info/sgss/Security/SignIn.aspx"
TARGET_URL = "https://sgs.bopp-obec.info/sgss/View_TransScoreSub/Show-View-TransScoreSub-Table.aspx"

ALERT_KEYWORD = "ยังไม่ประกาศผลการเรียน"   # ข้อความใน alert ที่แปลว่ายังดูไม่ได้
YEAR = "2"        # ปีที่ 2
SEMESTER = "1"    # ภาคเรียนที่ 1
CHECK_EVERY_MIN = 10   # 0 = เช็คครั้งเดียว
TIMEOUT = 90          # วินาทีที่รอเว็บตอบ (เว็บ SGS ช้า)
RETRIES = 3           # ลองใหม่กี่ครั้งเมื่อเว็บช้า/หลุด ก่อนข้ามไปรอบถัดไป
DEBUG = True           # เซฟ HTML ล่าสุดไว้ที่ sgs_debug.html เพื่อตรวจสอบ

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}


def notify(msg: str):
    headers = {"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"}
    messages = [{"type": "text", "text": msg}]
    if not LINE_TARGETS:
        r = requests.post("https://api.line.me/v2/bot/message/broadcast",
                          json={"messages": messages}, headers=headers, timeout=15)
        print("[line] broadcast status:", r.status_code, r.text[:200])
        r.raise_for_status()
        return
    failed = []
    for to in LINE_TARGETS:
        r = requests.post("https://api.line.me/v2/bot/message/push",
                          json={"to": to, "messages": messages}, headers=headers, timeout=15)
        print(f"[line] -> {to[:6]}... status:", r.status_code, r.text[:200])
        if not r.ok:
            failed.append(to[:6])
    if failed and len(failed) == len(LINE_TARGETS):
        raise RuntimeError(f"ส่ง LINE ไม่สำเร็จทุกผู้รับ: {failed}")


def form_data(form, overrides=None):
    """รวมค่าทุก field ในฟอร์ม ASP.NET (รวม __VIEWSTATE ฯลฯ)"""
    data = {}
    for el in form.find_all(["input", "select", "textarea"]):
        name = el.get("name")
        if not name:
            continue
        t = (el.get("type") or "").lower()
        if t in ("submit", "button", "image", "file"):
            continue
        if t in ("checkbox", "radio") and not el.has_attr("checked"):
            continue
        if el.name == "select":
            opt = el.find("option", selected=True) or el.find("option")
            data[name] = opt.get("value", "") if opt else ""
        else:
            data[name] = el.get("value", "")
    data.update(overrides or {})
    return data


NOT_LOGGED_IN = "คุณยังไม่ได้เข้าระบบ"   # ข้อความมุมขวาบนตอนยังไม่ล็อกอิน


def find_ok_button(form):
    """หาปุ่ม 'ตกลง' ได้ทั้งแบบ <input>, <button>, <a href=javascript:__doPostBack>"""
    for el in form.find_all(["input", "button", "a"]):
        label = (el.get("value") or el.get_text() or "").strip()
        if label == "ตกลง":
            return el
    return None


def login(s: requests.Session) -> bool:
    r = s.get(SIGNIN_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # ช่อง user = input text ตัวแรก, ช่อง password = input type=password
    pw = soup.find("input", {"type": "password"})
    if pw is None:
        raise RuntimeError("ไม่พบช่อง password (ดู sgs_login_debug.html)")
    form = pw.find_parent("form")
    user_box = next((i for i in form.find_all("input")
                     if (i.get("type") or "text").lower() == "text" and i.get("name")), None)
    if user_box is None:
        raise RuntimeError("ไม่พบช่อง user (ดู sgs_login_debug.html)")
    over = {user_box["name"]: SGS_USER, pw["name"]: SGS_PASS}

    btn = find_ok_button(form)
    if btn is not None:
        href = btn.get("href") or ""
        code = href + (btn.get("onclick") or "")
        # รองรับทั้ง __doPostBack('target','arg') และ WebForm_PostBackOptions("target", "arg", ...)
        m = (re.search(r"__doPostBack\(\s*['\"]([^'\"]*)['\"]\s*,\s*['\"]([^'\"]*)['\"]", code)
             or re.search(r"WebForm_PostBackOptions\(\s*['\"]([^'\"]*)['\"]\s*,\s*['\"]([^'\"]*)['\"]", code))
        if m:
            over["__EVENTTARGET"], over["__EVENTARGUMENT"] = m.group(1), m.group(2)
        elif btn.get("name"):
            over[btn["name"]] = btn.get("value", "ตกลง")
            if (btn.get("type") or "").lower() == "image":
                over[btn["name"] + ".x"], over[btn["name"] + ".y"] = "1", "1"

    action = urljoin(r.url, form.get("action") or r.url)
    r2 = s.post(action, data=form_data(form, over), headers=HEADERS, timeout=TIMEOUT)
    r2.raise_for_status()
    if DEBUG:
        open("sgs_login_debug.html", "w", encoding="utf-8").write(r2.text)

    if DEBUG:
        txt = BeautifulSoup(r2.text, "html.parser").get_text(" ", strip=True)
        print("[login] status:", r2.status_code, "url:", r2.url)
        print("[login] หน้าที่ตอบกลับ:", txt[:300])
        print("[login] ส่ง field:", [k for k in form_data(form, over) if not k.startswith("__")])
        print("[login] ปุ่มที่เจอ:", str(btn)[:200] if btn is not None else None)

    # ยืนยันว่าล็อกอินสำเร็จ: หน้าผลการเรียนต้องไม่ขึ้น "คุณยังไม่ได้เข้าระบบ"
    check = s.get(TARGET_URL, headers=HEADERS, timeout=TIMEOUT)
    check.raise_for_status()
    return NOT_LOGGED_IN not in check.text


def fetch_term_page(s: requests.Session) -> str:
    r = s.get(TARGET_URL, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    sel = None
    chosen = None
    for sl in soup.find_all("select"):
        for opt in sl.find_all("option"):
            text = " ".join(opt.get_text().split())
            if f"ปีที่ {YEAR}" in text and f"ภาคเรียนที่ {SEMESTER}" in text:
                sel, chosen = sl, opt.get("value", "")
                break
        if sel:
            break
    if sel is None:
        open("sgs_debug.html", "w", encoding="utf-8").write(r.text)
        print("[page] url:", r.url)
        print("[page] ข้อความในหน้า:", soup.get_text(" ", strip=True)[:300])
        for sl in soup.find_all("select"):
            opts = [" ".join(o.get_text().split()) for o in sl.find_all("option")]
            print("[page] select", sl.get("name"), "->", opts)
        if not soup.find_all("select"):
            print("[page] ไม่มี <select> ในหน้านี้เลย")
        raise RuntimeError("ไม่พบตัวเลือก ปีที่ 2 ภาคเรียนที่ 1 (ดู sgs_debug.html)")

    form = sel.find_parent("form")
    over = {sel["name"]: chosen, "__EVENTTARGET": sel["name"], "__EVENTARGUMENT": ""}
    action = urljoin(r.url, form.get("action") or r.url)
    r2 = s.post(action, data=form_data(form, over), headers=HEADERS, timeout=TIMEOUT)
    r2.raise_for_status()
    return r2.text


def check_once() -> bool:
    """True = ไม่เจอข้อความ alert (ดูข้อมูลได้)"""
    s = requests.Session()
    if not login(s):
        raise RuntimeError("ล็อกอินไม่สำเร็จ ตรวจ user/password")
    html = fetch_term_page(s)
    if DEBUG:
        open("sgs_debug.html", "w", encoding="utf-8").write(html)
    # กันแจ้งเตือนผิด: หน้าที่ตอบกลับต้องเป็นหน้าที่เลือกเทอมที่ต้องการจริงๆ (ไม่ใช่หน้า error)
    chk = BeautifulSoup(html, "html.parser")
    sel_ok = False
    for sl in chk.find_all("select"):
        o = sl.find("option", selected=True)
        if o:
            t = " ".join(o.get_text().split())
            if f"ปีที่ {YEAR}" in t and f"ภาคเรียนที่ {SEMESTER}" in t:
                sel_ok = True
    if not sel_ok:
        raise RuntimeError("หน้าที่ตอบกลับไม่ใช่หน้าของเทอมที่เลือก (เว็บอาจมีปัญหา) ข้ามรอบนี้")
    found = ALERT_KEYWORD in html
    if DEBUG:
        soup = BeautifulSoup(html, "html.parser")
        sel = next((sl for sl in soup.find_all("select")
                    if any("ภาคเรียนที่" in o.get_text() for o in sl.find_all("option"))), None)
        chosen = sel.find("option", selected=True) if sel else None
        print("[check] dropdown ที่เลือกอยู่ในหน้าที่ตอบกลับ:",
              " ".join(chosen.get_text().split()) if chosen else None)
        print("[check] จำนวนแถวข้อมูลในตาราง (tr ที่มี td):", len([t for t in soup.find_all("tr") if t.find("td")]))
        hits = [m.start() for m in re.finditer(re.escape(ALERT_KEYWORD), html)]
        print("[check] พบคำว่า alert กี่ที่:", len(hits))
        for i in hits[:3]:
            print("[check] บริบท:", html[max(0, i - 200): i + 120].replace("\n", " "))
    print(time.strftime("%H:%M:%S"), "เจอข้อความ alert:", found)
    return not found


def check_with_retry() -> bool:
    for attempt in range(1, RETRIES + 1):
        try:
            return check_once()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.HTTPError) as e:
            print(f"[retry {attempt}/{RETRIES}] เว็บช้าหรือเชื่อมต่อไม่ได้: {type(e).__name__}")
            if attempt == RETRIES:
                raise
            time.sleep(20 * attempt)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        notify("📢 ทดสอบแจ้งเตือนจาก sgs monitor")
        return
    while True:
        try:
            if check_with_retry():
                notify(f"📢 SGS ประกาศผลแล้ว (ปี {YEAR} เทอม {SEMESTER}) ลองเข้าดูได้เลย\n{TARGET_URL}")
                break
        except Exception as e:
            print("error:", e, "(จะลองใหม่รอบถัดไป)" if CHECK_EVERY_MIN else "")
        if not CHECK_EVERY_MIN:
            break
        time.sleep(CHECK_EVERY_MIN * 60)


if __name__ == "__main__":
    main()
