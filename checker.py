# -*- coding: utf-8 -*-
# 売却メール見張り番 - BIGLOBEの受信箱を直接見て、売れたらスマホに通知する
# GitHub Actions で5分おきに自動実行される

import csv
import email
import email.policy
import imaplib
import json
import os
import re
import sys
import unicodedata
import urllib.request
from email.header import Header

# ================= 設定エリア =================
IMAP_HOST = "mail.biglobe.ne.jp"
IMAP_PORT = 993
MAIL_USER = "anmoriya@kce.biglobe.ne.jp"
# メールパスワードは環境変数 BIGLOBE_PASSWORD から読む(GitHub Secretsに登録する)
# 通知の宛先トピックは環境変数 NTFY_TOPIC から読む(GitHub Secretsに登録する)

# 台帳に見つからなかったときの予備リンク(一覧ページ)
LINK_YAHOO = "https://auctions.yahoo.co.jp/user/jp/show/mystatus?select=selling"  # ヤフオク マイオク(出品中)
LINK_RAKUMA = "https://fril.jp/mypage"  # ラクマ マイページ
LINK_MERCARI = "https://mercari-shops.com/"  # メルカリShops 管理画面
# ==============================================

STATE_FILE = "state.json"
DAICHO_FILE = "daicho.csv"

SITE_KEY = {"ヤフオク": "yahoo", "メルカリShops": "mercari", "ラクマ": "rakuma"}
SITE_NAME = {"yahoo": "ヤフオク", "mercari": "メルカリShops", "rakuma": "ラクマ"}
FALLBACK_LINK = {"yahoo": LINK_YAHOO, "mercari": LINK_MERCARI, "rakuma": LINK_RAKUMA}


def norm_title(t):
    t = unicodedata.normalize("NFKC", t or "")
    return re.sub(r"\s+", " ", t).strip().upper()


def load_daicho():
    """台帳(3サイト対応表)を読む。無ければ空リスト"""
    if not os.path.exists(DAICHO_FILE):
        return []
    rows = []
    with open(DAICHO_FILE, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def find_in_daicho(daicho, site_key, title):
    """売れた商品の台帳行を探す。完全一致→前方一致の順"""
    n = norm_title(title)
    if not n:
        return None
    col = site_key + "_title"
    for row in daicho:
        if norm_title(row.get(col, "")) == n:
            return row
    for row in daicho:
        rn = norm_title(row.get(col, ""))
        if rn and (rn.startswith(n) or n.startswith(rn)):
            return row
    return None


def parse_mail(subject, sender, body):
    """メール1通から「どのサイトで何が売れたか」を読み取る。売却メールでなければ None"""
    subject = (subject or "").replace("\r", "").replace("\n", "")
    sender = sender or ""
    body = body or ""

    # ヤフオク: 件名「Yahoo!オークション - 終了（落札者あり）：商品名(オークションID)」
    if "終了（落札者あり）" in subject or ("Yahoo!オークション" in subject and "落札されました" in subject):
        m = re.search(r"[：:](.+?)\(([a-z]\d+)\)\s*$", subject)
        title = m.group(1).strip() if m else ""
        if not title:
            m2 = re.search(r"商品[：:]\s*(.+)", body)
            title = m2.group(1).strip() if m2 else "(商品名を読み取れず)"
        return {"site": "ヤフオク", "title": title}

    # メルカリShops: 差出人 mercari-shops.com / 件名「【メルカリShops】「商品名」の発送をお願いします。」
    if "mercari-shops.com" in sender:
        m = re.search(r"「(.+)」の発送をお願いします", subject)
        if m:
            return {"site": "メルカリShops", "title": m.group(1).strip()}

    # ラクマ: 差出人 fril.jp / 件名「購入申請がありました」または「購入されました」
    if "fril.jp" in sender and ("購入申請" in subject or "購入されました" in subject):
        m = re.search(r"商品名\s*[:：]\s*(.+)", body)
        title = m.group(1).strip() if m else "(商品名を読み取れず)"
        info = {"site": "ラクマ", "title": title}
        if "購入申請" in subject:
            info["note"] = "(購入申請の段階です。承認前でも他サイトは止めておくのが安全)"
        return info

    return None


def build_message(info, daicho):
    sold_key = SITE_KEY[info["site"]]
    row = find_in_daicho(daicho, sold_key, info["title"])

    lines = [f"【{info['site']}で売れました】", info["title"]]
    if info.get("note"):
        lines.append(info["note"])
    lines.append("")
    lines.append("↓ 他サイトの取り下げ")
    for key in ("yahoo", "mercari", "rakuma"):
        if key == sold_key:
            continue
        url = (row or {}).get(key + "_url", "").strip()
        if url:
            lines.append(f"・{SITE_NAME[key]}(この商品): {url}")
        elif row:
            lines.append(f"・{SITE_NAME[key]}: 台帳では出品なし")
        else:
            lines.append(f"・{SITE_NAME[key]}: {FALLBACK_LINK[key]}")
    if not row:
        lines.append("※台帳に見つからなかったため一覧ページのリンクです")
    return "\n".join(lines)


def send_ntfy(message, title="商品が売れました"):
    topic = os.environ["NTFY_TOPIC"]
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={
            "Title": Header(title, "utf-8").encode(),  # 日本語タイトル対応
            "Priority": "high",
            "Tags": "moneybag",
        },
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30)


def get_body_text(msg):
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is not None:
            text = part.get_content()
            if part.get_content_type() == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
            return text
    except Exception:
        pass
    # 保険: 全パートを総当たり
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                return part.get_content()
            except Exception:
                continue
    return ""


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def main():
    password = os.environ["BIGLOBE_PASSWORD"]
    daicho = load_daicho()
    print(f"台帳: {len(daicho)}行")

    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(MAIL_USER, password)
    conn.select("INBOX", readonly=True)  # readonly: 既読/未読の状態を変えない

    typ, data = conn.uid("search", None, "ALL")
    uids = [int(u) for u in data[0].split()] if data and data[0] else []
    max_uid = max(uids) if uids else 0

    state = load_state()
    if state is None:
        # 初回実行: 過去メールには通知を出さず、現在位置だけ覚える
        save_state({"last_uid": max_uid})
        print(f"初回実行: last_uid={max_uid} を記録。次回から新着を監視します")
        conn.logout()
        return

    last_uid = state.get("last_uid", 0)
    new_uids = [u for u in uids if u > last_uid]
    print(f"新着 {len(new_uids)} 件 (last_uid={last_uid})")

    for uid in sorted(new_uids):
        typ, msgdata = conn.uid("fetch", str(uid), "(RFC822)")
        if typ != "OK" or not msgdata or msgdata[0] is None:
            continue
        raw = msgdata[0][1]
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        subject = str(msg.get("Subject", ""))
        sender = str(msg.get("From", ""))
        body = get_body_text(msg)
        info = parse_mail(subject, sender, body)
        if info:
            print(f"売却検知: {info['site']} / {info['title']}")
            send_ntfy(build_message(info, daicho), title=f"{info['site']}で売れました")
        else:
            print(f"対象外メール: {subject[:60]}")

    if new_uids:
        save_state({"last_uid": max(new_uids)})
    conn.logout()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 失敗したらスマホにも知らせる(通知が黙って止まるのを防ぐ)
        try:
            send_ntfy(f"見張り番でエラーが発生しました: {e}", title="見張り番エラー")
        except Exception:
            pass
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
