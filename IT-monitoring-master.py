#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
speech_watcher.py  (2025-06-23 改訂版)

デジタル庁「大臣等会見」ページから過去4日以内の会見を抽出し、
各会見ページに埋め込まれた YouTube 動画のリンクと再生時間を取得して表示します。

再生時間が取得できない場合はプレースホルダーを出力し、
その下に該当の会見ページリンクも表示します。
"""

import importlib.util
import json
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ───────── 定数
JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
LOOKBACK_DAYS = 4
WINDOW_START = TODAY - timedelta(days=LOOKBACK_DAYS)

BASE_URL = "https://www.digital.go.jp"
LIST_URL = f"{BASE_URL}/speech"
YOUTUBE_CHANNEL_VIDEOS_URL = (
    "https://www.youtube.com/channel/UCKmJk25wcPwCecf7nV9HwCw/videos?hl=ja&gl=JP"
)
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    )
}

REIWA_RE = re.compile(r"令和(\d+)年(\d+)月(\d+)日")
MANUAL_SPEECH_DURATIONS = {
    "minister-260630-01": 18 * 60 + 41,
}


def md_link(url: str) -> str:
    return f"[{url}]({url})"


def format_updated_at(now: datetime | None = None) -> str:
    now = now or datetime.now(JST)
    return f"更新日時：{now.year}年{now.month}月{now.day}日 {now:%H:%M}（JST）"


def md_link(url: str) -> str:
    return f"[{url}]({url})"


def format_updated_at(now: datetime | None = None) -> str:
    now = now or datetime.now(JST)
    return f"更新日時：{now.year}年{now.month}月{now.day}日 {now:%H:%M}（JST）"

def parse_iso8601_duration(duration: str) -> int:
    m = re.match(r'PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?', duration)
    if not m:
        return 0
    h = int(m.group('h') or 0)
    mi = int(m.group('m') or 0)
    s = int(m.group('s') or 0)
    return h * 3600 + mi * 60 + s

def fetch_speech_items():
    resp = requests.get(LIST_URL, headers=UA, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    for a in soup.select("a[href^='/speech/minister']"):
        text = a.get_text(" ", strip=True)
        m = REIWA_RE.search(text)
        if not m:
            continue
        era, month, day = map(int, m.groups())
        year = 2018 + era
        dt = datetime(year, month, day, tzinfo=JST)
        if not (WINDOW_START <= dt <= TODAY):
            continue

        title = re.sub(r"（.*?）", "", text)
        prefix = title[title.find("大臣"):] if "大臣" in title else title
        url = urljoin(BASE_URL, a["href"])
        items.append({"date": dt, "prefix": prefix, "page_url": url})
    return items

def extract_youtube_id(html: str, soup: BeautifulSoup) -> str | None:
    iframe = soup.find("iframe", src=re.compile(r"youtube\.com/embed/"))
    if iframe:
        src = iframe["src"]
        if src.startswith("//"):
            src = "https:" + src
        return src.rsplit("/", 1)[-1].split("?")[0]

    a = soup.find("a", href=re.compile(r"(youtu\.be/|youtube\.com/watch)"))
    if a:
        href = a["href"]
        if "youtu.be/" in href:
            return href.split("youtu.be/")[1].split("?")[0]
        if "v=" in href:
            return href.split("v=")[1].split("&")[0]

    patterns = [
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})",
        r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"',
    ]
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            return m.group(1)
    return None


def extract_youtube_duration_seconds(html: str) -> int | None:
    meta = BeautifulSoup(html, "html.parser").find("meta", itemprop="duration")
    if meta and meta.get("content"):
        return parse_iso8601_duration(meta["content"])

    m = re.search(r'"lengthSeconds"\s*:\s*"?(\d+)"?', html)
    if m:
        return int(m.group(1))
    return None


def parse_accessible_duration(text: str) -> int | None:
    m = re.search(
        r"(?:(?P<h>\d+)\s*時間\s*)?"
        r"(?:(?P<m>\d+)\s*分\s*)?"
        r"(?P<s>\d+)\s*秒",
        text,
    )
    if not m:
        return None
    return (
        int(m.group("h") or 0) * 3600
        + int(m.group("m") or 0) * 60
        + int(m.group("s"))
    )


def normalize_video_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title)
    return re.sub(r"[\W_]+", "", normalized)


def extract_youtube_initial_data(html: str) -> dict | None:
    for marker in ("var ytInitialData = ", "ytInitialData = "):
        start = html.find(marker)
        if start == -1:
            continue
        start = html.find("{", start + len(marker))
        if start == -1:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(html[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def find_values(node, key: str):
    if isinstance(node, dict):
        if key in node:
            yield node[key]
        for value in node.values():
            yield from find_values(value, key)
    elif isinstance(node, list):
        for value in node:
            yield from find_values(value, key)


def extract_channel_video(html: str, speech_title: str) -> tuple[str, int] | None:
    data = extract_youtube_initial_data(html)
    if not data:
        return None

    expected_title = normalize_video_title(speech_title)
    for lockup in find_values(data, "lockupViewModel"):
        titles = [
            value.get("content")
            for value in find_values(lockup, "title")
            if isinstance(value, dict) and isinstance(value.get("content"), str)
        ]
        if expected_title not in map(normalize_video_title, titles):
            continue

        video_ids = [
            value
            for value in find_values(lockup, "videoId")
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", value)
        ]
        labels = [
            value.get("label")
            for value in find_values(lockup, "accessibilityContext")
            if isinstance(value, dict) and isinstance(value.get("label"), str)
        ]
        duration = next(
            (
                seconds
                for label in labels
                if (seconds := parse_accessible_duration(label)) is not None
            ),
            None,
        )
        if video_ids and duration is not None:
            return video_ids[0], duration
    return None


def lookup_youtube_on_official_channel(speech_title: str) -> tuple[str, int] | None:
    try:
        response = requests.get(
            YOUTUBE_CHANNEL_VIDEOS_URL,
            headers=UA,
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException:
        return None
    return extract_channel_video(response.text, speech_title)


def fetch_rendered_html(url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=UA["User-Agent"])
        page.goto(url, wait_until="networkidle", timeout=30000)
        html = page.content()
        browser.close()
        return html


def manual_speech_duration(page_url: str) -> int | None:
    for slug, seconds in MANUAL_SPEECH_DURATIONS.items():
        if slug in page_url:
            return seconds
    return None


def fetch_youtube_duration(vid: str) -> int | None:
    for url in (
        f"https://www.youtube.com/watch?v={vid}",
        f"https://www.youtube.com/embed/{vid}",
    ):
        try:
            r = requests.get(url, headers=UA, timeout=10)
            r.raise_for_status()
        except requests.RequestException:
            continue
        duration = extract_youtube_duration_seconds(r.text)
        if duration is not None:
            return duration
    return None


def lookup_youtube_in_speech(page_url: str):
    resp = requests.get(page_url, headers=UA, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    vid = extract_youtube_id(resp.text, soup)
    if not vid and importlib.util.find_spec("playwright") is not None:
        try:
            rendered_html = fetch_rendered_html(page_url)
        except Exception:
            rendered_html = ""
        vid = extract_youtube_id(rendered_html, BeautifulSoup(rendered_html, "html.parser"))

    if not vid:
        h1 = soup.find("h1")
        speech_title = h1.get_text(" ", strip=True) if h1 else ""
        channel_video = lookup_youtube_on_official_channel(speech_title)
        if channel_video:
            channel_vid, duration = channel_video
            return f"https://youtu.be/{channel_vid}", duration
        return None, manual_speech_duration(page_url)

    short_url = f"https://youtu.be/{vid}"
    return short_url, fetch_youtube_duration(vid) or manual_speech_duration(page_url)

def format_duration(sec: int) -> str:
    m, s = divmod(sec or 0, 60)
    return f"{m}分{s}秒"

def main():
    print(format_updated_at())
    print()
    print("【松本尚デジタル大臣】<br>")
    items = fetch_speech_items()
    if not items:
        print("該当データなし\n")
        return 

    for it in items:
        date_str = f"{it['date'].month}月{it['date'].day}日"
        prefix = it["prefix"]
        page_url = it["page_url"]
        yt_url, length = lookup_youtube_in_speech(page_url)

        if length is not None:
            print(f"○{date_str}の{prefix}（{format_duration(length)}）<br>")
            print(f"　{md_link(page_url)}\n")
        elif yt_url:
            print(f"○{date_str}の{prefix}（再生時間情報を自分で取得してください）<br>")
            print(f"　{md_link(page_url)}\n")
        else:
            print(f"○{date_str}の{prefix}（！再生時間情報を自分で取得してください！）<br>")
            print(f"　{md_link(page_url)}\n")

        time.sleep(0.2)

if __name__ == "__main__":
    main()

#============自民党＝＝＝＝＝＝＝＝＝＝＝＝＝＝
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#============自民党＝＝＝＝＝＝＝＝＝＝＝＝＝＝
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ldp_watcher.py  rev‑4.6‑LDP‑r4  (2025‑06‑17)

■ 自民党サイト（/activity）を巡回し，
   過去 4 日＋当日＋未来 10 日の 15 日分から
   デジタル政策関連イベントのみ抽出して表示。
   ─ 重複タイトルは「本文が詳しい方」を優先して 1 行に集約。
"""

# ───────── Imports ──────────────────────────────────────────
import re, time, sys, requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

# ───────── Global settings ─────────────────────────────────
LOOKBACK          = 4           # 過去 4 日
AHEAD             = 10          # 未来 10 日
WAIT_SEC          = 1
DEBUG             = True
DEBUG_SOU         = True
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")

# ───────── キーワード ────────────────────────────────────
KEYWORDS = [
    # ── 政治・政策 ──
    "デジタル社会推進本部","経済安全保障対策本部","経済安全保障推進本部",
    "情報通信戦略調査会","経済成長戦略本部","知的財産戦略調査会",
    "競争政策調査会","プラットフォームサービス","特定利用者情報",
    "web3","web3.0研究会","デジタル社会構想会議","デジタル臨時行政調査会",
    "デジタル社会推進会議",
    # 注意：「政調」は kw_hit() 関数内で優先的に処理される
    # ── 技術一般 ──
    "デジタル","情報通信","サイバー","AI","ＤＸ","DX","IT","5g",
    # ── 行政関連 ──
    "標準仕様","ガイドライン","無線局","免許状","光ファイバ",
    # ── 大臣会見 ──
    "平デジタル大臣",
]
SHORT_ASCII = {"ai", "it", "dx"}          # 2 文字英語は単語境界を意識
norm  = lambda s: re.sub(r"\s+", "", s).lower()
def kw_hit(text: str) -> bool:
    t = norm(text)
    
    # 「政調」が含まれていれば必ず取得
    if "政調" in t:
        return True
    if "デジタル社会推進本部" in t:
        return True
    
    # その他のキーワードチェック
    for k in KEYWORDS:
        kl = k.lower()
        if kl in SHORT_ASCII:
            if re.search(rf"(?:^|[^a-z0-9]){kl}(?:[^a-z0-9]|$)", t):
                return True
        elif kl in t:
            return True
    return False

# ───────── 日付ユーティリティ ─────────────────────────
JST   = timezone(timedelta(hours=9))
today = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)

# 過去 LOOKBACK 日 ～ 当日 ～ 未来 AHEAD 日
DATES = [today - timedelta(days=delta)
         for delta in range(-AHEAD, LOOKBACK + 1)]

DATE_TAG = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
DATE_TXT = re.compile(r"(\d{4})年\s*0?(\d{1,2})月\s*0?(\d{1,2})日")
TRAIL_RE = re.compile(r"\s*(?:政策|会議等|法令|採用)?\s*20\d{2}年\d{1,2}月\d{1,2}日$")

dbg  = lambda *m: print(*m, file=sys.stderr, flush=True) if DEBUG else None
sdbg = lambda *m: print("[SOU]", *m, file=sys.stderr, flush=True) if DEBUG_SOU else None

# ════════════════════════════════════════════════════════════════
#                         自 民 党
# ════════════════════════════════════════════════════════════════
HEAD_TAGS = ("dt", "h1", "h2", "h3", "h4", "li")
EXCLUDE_LDP = re.compile(r"^記者会見$")      # 除外ワード

def better(record_new, record_old):
    """どちらを残すか判定（本文がタイトルと同じなら劣る）"""
    body_n, body_o = record_new["body"], record_old["body"]
    ttl = record_new["title"]
    # 本文が空 or タイトルと同じ → 情報量 0
    score_n = len(body_n) if body_n and body_n != ttl else 0
    score_o = len(body_o) if body_o and body_o != ttl else 0
    return record_new if score_n > score_o else record_old

def scrape_ldp():
    # key=(日付, タイトル) で最良レコードを保持
    best = {}

    with sync_playwright() as p:
        ctx = (p.chromium
               .launch(headless=True,
                       args=["--disable-blink-features=AutomationControlled"])
               .new_context(user_agent=UA))
        page = ctx.new_page()

        for d in DATES:
            url = f"https://www.jimin.jp/activity/?day={d.year}.{d.month}.{d.day}"
            #dbg("[LDP] goto", url) #<- デバックを見たければここを有効化
            try:
                page.goto(url, wait_until="networkidle", timeout=25_000)
            except Exception:
                continue
            soup = BeautifulSoup(page.content(), "html.parser")

            for tag in soup.find_all(HEAD_TAGS):
                ttl = tag.get_text(" ", strip=True)
                if not ttl or EXCLUDE_LDP.match(ttl):
                    continue
                if not kw_hit(ttl):
                    continue
                sib = tag.find_next_sibling() or tag
                body = sib.get_text(" ", strip=True)
                if body.startswith("今日の 自民党"):
                    body = ""

                rec = {
                    "date": f"{d.month}月{d.day}日",
                    "title": ttl,
                    "body": body.replace("Google Calenderに予定を追加", "").strip()
                }
                key = (rec["date"], rec["title"])
                if key in best:
                    best[key] = better(rec, best[key])
                else:
                    best[key] = rec
                #dbg(" 🔹LDP-HIT", ttl[:60])　<- デバックを見たければここを有効化

            time.sleep(WAIT_SEC)

    return list(best.values())

# ════════════════════════════════════════════════════════════════
def main():
    ldp = scrape_ldp()

    #print(f"\n===== {today.strftime('%-m月%-d日')} データ取得開始 =====\n")

    print("【自由民主党】<br>")
    if ldp:
        # 文字列の日付を並び替えやすく整数化してソート
        def dt_key(r):
            m, d = map(int, r["date"].rstrip("日").split("月"))
            return (m, d)
        for r in sorted(ldp, key=dt_key, reverse=False):
            print(f"○{r['date']}　{r['title']}")
            if r['body'] and r['body'] != r['title']:
                print(f"　{r['body']}\n")
            else:
                print()          # 本文が空またはタイトルと同じなら 1 行で
    else:
        print("政調、デジタル社会推進本部開催予定なし\n")

# ───────────────────────────────────────────
if __name__ == "__main__":
    main()

#デジタル庁
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
digital_watcher.py  rev-4.7-DIGITAL  (2025-07-15)

■ 役割
  デジタル庁サイトの「プレスリリース」「ニュース」から、
  デジタル政策関連キーワードを含む記事を一定期間内に抽出し一覧表示。
  ニュースページ内の「幹部一覧（/about/member）」リンクも取得。
"""
import re
import time
import unicodedata
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup

# ───────── 基本設定
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36")
JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)

LOOKBACK = 5      # 今日 + 過去4日
AHEAD    = 7      # 未来 (開催案内など)
DIG_PAGES = 15    # 各カテゴリで深掘りするページ数
WAIT      = 0.3   # 秒

# ───────── キーワード定義
RAW_KW = [
    "デジタル","情報通信","サイバー","AI","DX","ＤＸ","IT","SNS",
    "標準仕様","ガイドライン","無線局","免許状","光ファイバ","幹部",
    "人事","組織情報"
]
SHORT = {"ai", "it", "dx"}

# 全角数字→半角化して NFKC 正規化、lower 化
def normalize_text(s: str) -> str:
    s2 = unicodedata.normalize("NFKC", s)
    return ''.join(chr(ord(c)-0xFEE0) if '０' <= c <= '９' else c for c in s2).lower()

norm_kw = [normalize_text(k) for k in RAW_KW]

def kw_hit(txt: str) -> bool:
    t = normalize_text(txt)
    return any(
        (re.search(rf"(?:^|[^a-z0-9]){k}(?:[^a-z0-9]|$)", t) if k in SHORT else k in t)
        for k in norm_kw
    )

# ───────── 日付判定
WIN_FROM = TODAY - timedelta(days=LOOKBACK - 1)
WIN_TO   = TODAY + timedelta(days=AHEAD)
in_window = lambda d: WIN_FROM <= d <= WIN_TO

DIG_ROOT = ["https://www.digital.go.jp/press", "https://www.digital.go.jp/news"]
dt_re = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")

def article_date(html: str):
    soup = BeautifulSoup(html, "html.parser")
    # <time datetime="YYYY-MM-DD">
    if t := soup.find("time", datetime=True):
        y, m, d = map(int, t["datetime"][ :10].split("-"))
        return datetime(y, m, d, tzinfo=JST)
    # 本文中の「YYYY年M月D日」
    if m := dt_re.search(soup.text):
        return datetime(*map(int, m.groups()), tzinfo=JST)

def extract_update_date(html: str):
    # 「YYYY年M月D日更新」を抽出
    if m := re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日更新", html):
        return datetime(*map(int, m.groups()), tzinfo=JST)

def scrape_digital():
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    hits, seen = [], set()

    for root in DIG_ROOT:
        for pg in range(1, DIG_PAGES + 1):
            url = root if pg == 1 else f"{root}?page={pg}"
            resp = sess.get(url, timeout=20)
            soup = BeautifulSoup(resp.text, "html.parser")
            page_has_hit = False

            # プレスは press/news のみ、ニュースは about も対象
            selectors = ["a[href^='/press/']", "a[href^='/news/']"]
            if root.endswith("/news"):
                selectors.append("a[href^='/about/']")
            for a in soup.select(",".join(selectors)):
                link = urljoin(url, a["href"])
                title = a.get_text(" ", strip=True)
                # press/news 特有の末尾「分類＋日付」は削除
                title = re.sub(r'\s+\S+\s+\d{4}年\d{1,2}月\d{1,2}日$', '', title)

                if not title or link in seen:
                    continue

                art_resp = sess.get(link, timeout=20)
                art_html = art_resp.text
                body_text = BeautifulSoup(art_html, "html.parser").get_text(" ", strip=True)

                # タイトル or 本文 にキーワードヒットがなければスキップ
                if not (kw_hit(title) or kw_hit(body_text)):
                    continue

                # 日付判定とタイトル調整
                if "/about/member" in link:
                    dt = extract_update_date(art_html)
                    display_title = "幹部一覧を更新しました"
                else:
                    dt = article_date(art_html)
                    display_title = title

                if not dt or not in_window(dt):
                    continue

                hits.append({
                    "date": dt.strftime("%-m月%-d日"),
                    "title": display_title,
                    "url": link
                })
                seen.add(link)
                page_has_hit = True

            if not page_has_hit:
                break

            time.sleep(WAIT)

    return hits


def main():
    print("【デジタル庁】<br>")
    results = scrape_digital()
    if not results:
        print("DXやデジタル化に関連する新着情報および審議会等の開催はいずれもなし\n")
        return
    for r in results:
        print(f"⚪︎{r['date']}　{r['title']}<br>\n{md_link(r['url'])}\n")

if __name__ == "__main__":
    main()


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
soumu_watcher.py  rev‑4.6‑SOU  (2025‑06‑12)

■ 役割
  総務省サイト「What's New」インデックスを走査し、
  デジタル・情報通信政策に関する告知のうち、LOOKBACK〜AHEAD 期間に
  該当するものを抽出して一覧表示する。
"""
import re, unicodedata
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# ───────── 基本設定
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36")
JST   = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)

LOOKBACK = 5      # 今日 + 過去4日
AHEAD    = 7      # 未来 (開催案内など)

# ───────── キーワード定義
RAW_KW = [
    # 技術・行政一般
    "デジタル","情報通信","サイバー","AI","DX","ＤＸ","IT","SNS",
    "無線局","免許状","光ファイバ","標準仕様","ガイドライン",
    # 審議会関連
    "情報通信審議会","郵政政策部会","電気通信事業部会",
    "技術分科会","陸上無線通信委員会","IPネットワーク設備委員会",
]
SHORT = {"ai", "it", "dx"}

half = lambda s: ''.join(chr(ord(c)-0xFEE0) if '０' <= c <= '９' else c for c in s)
norm_kw = [half(unicodedata.normalize("NFKC", k)).lower() for k in RAW_KW]

def kw_hit(txt: str) -> bool:
    t = half(unicodedata.normalize("NFKC", txt)).lower()
    return any(
        re.search(rf"(?:^|[^a-z0-9]){k}(?:[^a-z0-9]|$)", t) if k in SHORT else k in t
        for k in norm_kw
    )

# ───────── 日付解析
jp_re  = re.compile(r"令和(\d+)年(\d{1,2})月(\d{1,2})日")
ymd_re = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
slash  = re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})")

def parse_dt(text: str):
    """タイトルまたは本文内から日付を抽出して datetime に変換"""
    t = half(unicodedata.normalize("NFKC", text))
    if m := jp_re.search(t):
        return datetime(2018 + int(m[1]), int(m[2]), int(m[3]), tzinfo=JST)
    if m := ymd_re.search(t):
        return datetime(*map(int, m.groups()), tzinfo=JST)
    if m := slash.search(t):
        return datetime(*map(int, m.groups()), tzinfo=JST)

WIN_FROM = TODAY - timedelta(days=LOOKBACK - 1)
WIN_TO   = TODAY + timedelta(days=AHEAD)
in_window = lambda d: WIN_FROM <= d <= WIN_TO

# ───────── 低レベル fetch（エンコーディング自動判定）
def fetch(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    enc = r.apparent_encoding or "utf-8"
    if enc.lower() == "utf-8" and b"\x82" in r.content[:300]:   # SJIS誤判定対策
        enc = "shift_jis"
    return r.content.decode(enc, "replace")

# ───────── What's New インデックス候補抽出
def list_candidates():
    idx = "https://www.soumu.go.jp/menu_kyotsuu/whatsnew/index.html"
    with sync_playwright() as p:
        page = p.chromium.launch(headless=True).new_context().new_page()
        page.goto(idx, wait_until="networkidle", timeout=30000)
        soup = BeautifulSoup(page.content(), "html.parser")

    links = []
    for a in soup.find_all("a"):
        ttl = a.get_text(" ", strip=True)
        if ttl and kw_hit(ttl):
            links.append({"title": ttl, "url": urljoin(idx, a["href"])})
    return links

# ───────── 総務省スクレイプ
def scrape_soumu():
    results = []
    for rec in list_candidates():
        try:
            html = fetch(rec["url"])
        except Exception:
            continue
        dt = parse_dt(rec["title"]) or parse_dt(html)
        if not dt or not in_window(dt):
            continue
        results.append({"date": dt.strftime("%-m月%-d日"), **rec})

    # 重複排除
    uniq, filtered = set(), []
    for r in results:
        key = (r["date"], r["title"])
        if key in uniq:
            continue
        uniq.add(key)
        filtered.append(r)
    return filtered

# ───────── エントリポイント
def main():
    #print(f"===== 総務省 What's New Watch ({TODAY:%-m/%-d}) =====\n")
    print("【総務省】<br>")
    results = scrape_soumu()
    if not results:
        print("DXやデジタル化に関連する新着情報および審議会等の開催はいずれもなし")
        return
    for r in results:
        print(f"○{r['date']}　{r['title']}<br>\n　{md_link(r['url'])}\n")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
METI press release watcher.

経済産業省「ニュースリリース」から、指定キーワードにマッチする直近の
プレスリリースを抽出して表示します。

外部パッケージに依存せず、この Codex 環境でも `--self-test` で解析ロジックを
検証できるよう、HTTP 取得と HTML 解析は Python 標準ライブラリで実装しています。
"""
import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

# ───────── Settings ──────────────────────────────────────
BASE_URL = "https://www.meti.go.jp/press/"
LOOKBACK = 14
MEETING_LOOKBACK = 4
JST = timezone(timedelta(hours=9))
TODAY = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
WIN_FROM = TODAY - timedelta(days=LOOKBACK)
MEETING_WIN_FROM = TODAY - timedelta(days=MEETING_LOOKBACK)
READ_TIMEOUT = 20
REQUEST_RETRIES = 1
REQUEST_BACKOFF = 0.5
DEBUG = os.getenv("IT_MONITORING_DEBUG", "").lower() in {"1", "true", "yes", "on"}
READER_BASE_URL = os.getenv("METI_READER_BASE_URL", "https://r.jina.ai/")
MEETING_URLS = [
    "https://wwws.meti.go.jp/interface/honsho/committee/index.cgi/committee",
    "https://www.meti.go.jp/shingikai/index.html",
]

# ───────── Keywords ──────────────────────────────────────
KEYWORDS = [
    "投資", "成長投資", "設備投資", "対内直接投資", "海外投資", "投資促進",
    # ── 政治・政策 ──
    "デジタル社会推進本部", "経済安全保障対策本部", "経済安全保障推進本部",
    "情報通信戦略調査会", "経済成長戦略本部", "知的財産戦略調査会",
    "競争政策調査会", "プラットフォームサービス", "特定利用者情報",
    "web3", "web3.0研究会", "デジタル社会構想会議", "デジタル臨時行政調査会",
    "デジタル社会推進会議",
    # 注意：「政調」は kw_hit() 関数内で優先的に処理される
    # ── 技術一般 ──
    "デジタル", "情報通信", "サイバー", "AI", "ＤＸ", "DX", "IT", "5g",
    # ── 行政関連 ──
    "標準仕様", "ガイドライン", "無線局", "免許状", "光ファイバ",
    "クラウド", "ガバメントクラウド", "データセンター",
    "経済安全保障", "QUAD", "サプライチェーン", "セキュリティクリアランス",
    "電気通信事業法", "サイバーセキュリティ", "Web3", "半導体",
    "GIGAスクール構想", "量子コンピューター", "スーパーコンピュータ",
    "スマホ新法", "青少年インターネット環境整備法", "Fintech",
    "中央銀行デジタル通貨", "知的財産", "個人情報保護", "医療DX",
    "新年度予算（デジタル関連）", "環境",
]
SHORT_ASCII = {"ai", "it", "dx", "5g"}
DATE_RE = re.compile(r"(\d{4})年\s*0?(\d{1,2})月\s*0?(\d{1,2})日")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


@dataclass(frozen=True)
class PressRelease:
    dt: datetime
    title: str
    url: str

    @property
    def date_label(self) -> str:
        return f"{self.dt.month}月{self.dt.day}日"


@dataclass(frozen=True)
class AnchorCandidate:
    href: str
    title: str
    context_before: str
    context_after: str = ""


def debug(message: str) -> None:
    if DEBUG:
        print(f"[DBG] {message}", file=sys.stderr)


def warn(message: str) -> None:
    print(f"[WARN] {message}", file=sys.stderr)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def kw_hit(text: str) -> bool:
    normalized = normalize(text)
    # 「政調」は他の短い英数字キーワードより先に判定する。
    if "政調" in normalized:
        return True

    for kw in KEYWORDS:
        keyword = normalize(kw)
        if keyword in SHORT_ASCII:
            if re.search(rf"(?:^|[^a-z0-9]){re.escape(keyword)}(?:[^a-z0-9]|$)", normalized):
                return True
        elif keyword in normalized:
            return True
    return False


def parse_date(text: str) -> datetime | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    return datetime(year, month, day, tzinfo=JST)


def month_urls(today: datetime, lookback_days: int) -> list[str]:
    """Return month archive URLs that can overlap the target window."""
    urls: list[str] = []
    current = today.replace(day=1)
    lower_bound = today - timedelta(days=lookback_days)
    while current >= lower_bound.replace(day=1):
        urls.append(urljoin(BASE_URL, f"archive_{current.year}{current.month:02d}.html"))
        previous_month_last_day = current - timedelta(days=1)
        current = previous_month_last_day.replace(day=1)
    return urls


def candidate_urls() -> list[str]:
    # Reader URLs are tried first because Codespaces may time out when reading
    # directly from www.meti.go.jp, while the reader service can fetch the same
    # public METI page and return lightweight Markdown quickly. Direct METI URLs
    # remain as fallbacks so the script is not dependent on the reader service.
    direct_urls = [*month_urls(TODAY, LOOKBACK), BASE_URL]
    return urls_with_reader_fallbacks(direct_urls)


def meeting_candidate_urls() -> list[str]:
    return urls_with_reader_fallbacks(MEETING_URLS)


def urls_with_reader_fallbacks(direct_urls: list[str]) -> list[str]:
    reader_urls = [reader_url(url) for url in direct_urls if READER_BASE_URL]
    return [*reader_urls, *direct_urls]


def reader_url(url: str) -> str:
    return f"{READER_BASE_URL.rstrip('/')}/{url}"


def original_url(url: str) -> str:
    if READER_BASE_URL and url.startswith(READER_BASE_URL):
        return url[len(READER_BASE_URL):]
    return url


def request_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
        "Connection": "close",
    }


def fetch_html(url: str) -> str:
    """Fetch HTML with short retries and a curl fallback for restricted environments."""
    errors: list[str] = []
    for attempt in range(REQUEST_RETRIES + 1):
        debug(f"GET {url} attempt={attempt + 1} timeout={READ_TIMEOUT}")
        start = time.monotonic()
        try:
            request = Request(url, headers=request_headers())
            with urlopen_ipv4(request, timeout=READ_TIMEOUT) as response:
                body = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                elapsed = time.monotonic() - start
                debug(f"HTTP {response.status} {url} {len(body)} bytes in {elapsed:.1f}s")
                return body.decode(charset, errors="replace")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            errors.append(f"urllib attempt {attempt + 1}: {exc}")
            debug(f"request failed: {url}: {exc}")
            if attempt < REQUEST_RETRIES:
                time.sleep(REQUEST_BACKOFF * (attempt + 1))

    curl_html = fetch_html_with_curl(url, errors)
    if curl_html is not None:
        return curl_html

    raise RuntimeError(f"failed to fetch {url}: {'; '.join(errors)}")


def urlopen_ipv4(request: Request, timeout: int):
    """Open a URL while preferring IPv4 to avoid IPv6 stalls in Codespaces."""
    original_getaddrinfo = socket.getaddrinfo

    def getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = getaddrinfo_ipv4
    try:
        return urlopen(request, timeout=timeout)
    finally:
        socket.getaddrinfo = original_getaddrinfo


def fetch_html_with_curl(url: str, errors: list[str]) -> str | None:
    """Fallback for environments where urllib is blocked but curl works."""
    curl = shutil.which("curl")
    if not curl:
        errors.append("curl fallback unavailable: curl command not found")
        return None

    command = [
        curl,
        "--location",
        "--fail",
        "--silent",
        "--show-error",
        "--compressed",
        "--http1.1",
        "--ipv4",
        "--max-time",
        str(READ_TIMEOUT),
    ]
    for name, value in request_headers().items():
        command.extend(["--header", f"{name}: {value}"])
    command.append(url)

    debug(f"curl fallback GET {url} timeout={READ_TIMEOUT}")
    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=READ_TIMEOUT + 5,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip()
        detail = stderr or f"exit status {exc.returncode}"
        errors.append(f"curl fallback: {detail}")
        debug(f"curl fallback failed: {url}: {detail}")
        return None
    except subprocess.TimeoutExpired as exc:
        detail = f"timed out after {exc.timeout}s"
        errors.append(f"curl fallback: {detail}")
        debug(f"curl fallback failed: {url}: {detail}")
        return None
    except OSError as exc:
        errors.append(f"curl fallback: {exc}")
        debug(f"curl fallback failed: {url}: {exc}")
        return None

    elapsed = time.monotonic() - start
    debug(f"curl fallback OK {url} {len(completed.stdout)} bytes in {elapsed:.1f}s")
    return completed.stdout.decode("utf-8", errors="replace")


class PressListParser(HTMLParser):
    """Collect anchor text and nearby preceding text from METI list HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[AnchorCandidate] = []
        self._events: list[tuple[str, str, str]] = []
        self._recent_text: list[str] = []
        self._current_href: str | None = None
        self._current_title: list[str] = []
        self._current_context_before = ""

    def candidates(self) -> list[AnchorCandidate]:
        candidates: list[AnchorCandidate] = []
        for index, event in enumerate(self._events):
            kind, title, href = event
            if kind != "anchor":
                continue
            before = " ".join(text for text_kind, text, _ in self._events[max(0, index - 40):index] if text_kind == "text")
            after = " ".join(text for text_kind, text, _ in self._events[index + 1:index + 41] if text_kind == "text")
            candidates.append(AnchorCandidate(href=href, title=title, context_before=before, context_after=after))
        return candidates

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._current_href = href
            self._current_title = []
            self._current_context_before = " ".join(self._recent_text[-40:])

    def handle_data(self, data: str) -> None:
        text = " ".join(unescape(data).split())
        if not text:
            return
        self._recent_text.append(text)
        self._events.append(("text", text, ""))
        if len(self._recent_text) > 80:
            self._recent_text = self._recent_text[-80:]
        if self._current_href:
            self._current_title.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._current_href:
            return
        title = " ".join(self._current_title).strip()
        if title:
            self.anchors.append(AnchorCandidate(
                href=self._current_href,
                title=title,
                context_before=self._current_context_before,
            ))
            self._events.append(("anchor", title, self._current_href))
        self._current_href = None
        self._current_title = []
        self._current_context_before = ""


def iter_press_releases(
    html: str,
    base_url: str,
    win_from: datetime = WIN_FROM,
    win_to: datetime | None = TODAY,
) -> Iterable[PressRelease]:
    seen_links: set[str] = set()
    source_base_url = original_url(base_url)

    for anchor in iter_anchor_candidates(html):
        title = anchor.title
        if not title or not kw_hit(title):
            continue

        date = parse_date(anchor.context_before) or parse_date(anchor.context_after)
        if not date or date < win_from:
            continue
        if win_to is not None and date > win_to:
            continue

        url = urljoin(source_base_url, anchor.href)
        if url in seen_links:
            continue
        seen_links.add(url)
        yield PressRelease(dt=date, title=title, url=url)


def iter_anchor_candidates(content: str) -> Iterable[AnchorCandidate]:
    parser = PressListParser()
    parser.feed(content)
    yield from parser.candidates()
    yield from iter_markdown_anchor_candidates(content)


def iter_markdown_anchor_candidates(markdown: str) -> Iterable[AnchorCandidate]:
    current_date_text = ""
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if parse_date(stripped):
            current_date_text = stripped
        for match in MARKDOWN_LINK_RE.finditer(stripped):
            title = " ".join(unescape(match.group(1)).split())
            href = match.group(2).strip()
            if title and href:
                after_text = markdown[match.end():match.end() + 300]
                yield AnchorCandidate(href=href, title=title, context_before=current_date_text, context_after=after_text)


def scrape_press_releases() -> list[PressRelease]:
    errors: list[str] = []
    for url in candidate_urls():
        try:
            html = fetch_html(url)
            releases = list(iter_press_releases(html, url))
            if releases:
                debug(f"matched {len(releases)} item(s) from {url}")
                return dedupe_and_sort(releases)
            debug(f"no matching item from {url}; trying next candidate")
        except RuntimeError as exc:
            errors.append(str(exc))
            debug(str(exc))

    if errors:
        warn("経済産業省ニュースリリースを取得できませんでした。")
        warn("取得先URLとエラーを確認するには IT_MONITORING_DEBUG=1 で再実行してください。")
        for error in errors[:3]:
            warn(error)
        for error in errors[3:]:
            debug(error)
    return []


def scrape_meetings() -> list[PressRelease]:
    errors: list[str] = []
    items: list[PressRelease] = []
    for url in meeting_candidate_urls():
        try:
            html = fetch_html(url)
            releases = list(iter_press_releases(html, url, win_from=MEETING_WIN_FROM, win_to=None))
            if releases:
                debug(f"matched {len(releases)} meeting item(s) from {url}")
                items.extend(releases)
            else:
                debug(f"no matching meeting item from {url}; trying next candidate")
        except RuntimeError as exc:
            errors.append(str(exc))
            debug(str(exc))

    if errors and not items:
        warn("経済産業省の審議会・研究会等を取得できませんでした。")
        for error in errors[:3]:
            warn(error)
        for error in errors[3:]:
            debug(error)
    return dedupe_and_sort(items, reverse=False)


def dedupe_and_sort(releases: Iterable[PressRelease], reverse: bool = True) -> list[PressRelease]:
    seen: set[tuple[str, str]] = set()
    output: list[PressRelease] = []
    for release in sorted(releases, key=lambda item: item.dt, reverse=reverse):
        key = (release.title, release.url)
        if key in seen:
            continue
        seen.add(key)
        output.append(release)
    return output


def print_section(title: str, releases: Iterable[PressRelease]) -> None:
    print(f"{title}<br>")
    releases = list(releases)
    if not releases:
        print("該当データなし")
        return
    for release in releases:
        print(f"○{release.date_label}　{release.title}<br>")
        print(f"　{md_link(release.url)}\n")


def print_releases(press_releases: Iterable[PressRelease], meeting_releases: Iterable[PressRelease]) -> None:
    print_section("【経済産業省ニュースリリース（投資・IT関連）】", press_releases)
    print()
    print_section("【審議会・研究会等】", meeting_releases)


def run_self_test() -> None:
    sample_html = """
    <html><body><ul>
      <li><span>2026年6月12日</span>
        <a href="/press/2026/06/20260612001/20260612001.html">成長投資ガイダンス（案）を公表しました</a>
      </li>
      <li><span>2026年6月11日</span>
        <a href="/press/2026/06/ignored.html">関係ない発表</a>
      </li>
      <li><span>2026年6月10日</span>
        <a href="/press/2026/06/ai.html">AI政策に関するガイドラインを改定しました</a>
      </li>
    </ul></body></html>
    """
    sample_markdown = """
    2026年6月15日
    [AI分野を中心とした新たな五庁協力について合意しました](https://www.meti.go.jp/press/2026/06/20260615002/20260615002.html)
    """
    sample_meeting_html = """
    <html><body><ul>
      <li><a href="/interface/honsho/committee/detail.cgi?committee_id=1">第１回デジタルプラットフォームの透明性・公正性に関するモニタリング会合</a>
      <span>2026年6月30日(火)</span></li>
    </ul></body></html>
    """
    releases = dedupe_and_sort([
        *iter_press_releases(sample_html, BASE_URL),
        *iter_press_releases(sample_markdown, reader_url(BASE_URL)),
    ])
    meeting_releases = [
        *iter_press_releases(sample_meeting_html, MEETING_URLS[0], win_from=MEETING_WIN_FROM, win_to=None)
    ]
    assert len(releases) == 3, releases
    assert len(meeting_releases) == 1, meeting_releases
    assert meeting_releases[0].title == "第１回デジタルプラットフォームの透明性・公正性に関するモニタリング会合"
    assert releases[0].title == "AI分野を中心とした新たな五庁協力について合意しました"
    assert releases[0].url == "https://www.meti.go.jp/press/2026/06/20260615002/20260615002.html"
    assert releases[1].title == "成長投資ガイダンス（案）を公表しました"
    assert releases[1].url == "https://www.meti.go.jp/press/2026/06/20260612001/20260612001.html"
    assert month_urls(datetime(2026, 6, 17, tzinfo=JST), LOOKBACK)[0] == "https://www.meti.go.jp/press/archive_202606.html"
    assert candidate_urls()[0].startswith("https://r.jina.ai/https://www.meti.go.jp/press/archive_")
    assert kw_hit("政調でデジタル政策を議論")
    assert kw_hit("ローカル5Gの無線局免許状を交付")
    assert not kw_hit("baitという英単語だけでは一致しない")
    print("self-test ok")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="METI press release watcher")
    parser.add_argument("--self-test", action="store_true", help="run offline parser tests and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        run_self_test()
        return

    debug("経済産業省プレスリリース『投資・IT』関連情報取得開始")
    press_releases = scrape_press_releases()
    meeting_releases = scrape_meetings()
    print_releases(press_releases, meeting_releases)


if __name__ == "__main__":
    main()


#内閣府    
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cao_press_watcher_rss_etree.py  rev-1.6  (2025-06-19)

■ 内閣府「報道発表新着情報」RSSフィードを標準ライブラリだけで取得・解析し、
  過去 4 日間に掲載された “DX／デジタル関連＋食品・環境” の
  リリースを抽出して一覧表示します。

・requests で RSS(XML) を取得
・xml.etree.ElementTree でパース
・email.utils.parsedate_to_datetime + datetime.fromisoformat で日付変換
依存:
    pip install requests
"""

import re
import unicodedata
import requests

from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime

# ───────── Settings ──────────────────────────────────────
RSS_URL       = "https://www.cao.go.jp/rss/news.rdf"
LOOKBACK_DAYS = 4

# ───────── Date window ───────────────────────────────────
JST      = timezone(timedelta(hours=9))
NOW      = datetime.now(JST)
TODAY    = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
WIN_FROM = TODAY - timedelta(days=LOOKBACK_DAYS)

# ───────── Keywords ─────────────────────────────────────
KEYWORDS = [
    "環境",
    "DX", "デジタル", "クラウド", "ガバメントクラウド", "データセンター",
    "経済安全保障", "QUAD", "サプライチェーン", "セキュリティクリアランス",
    "電気通信事業法", "サイバーセキュリティ", "Web3", "半導体", "AI",
    "GIGAスクール構想", "量子コンピューター", "スーパーコンピュータ",
    "スマホ新法", "青少年インターネット環境整備法", "Fintech",
    "中央銀行デジタル通貨", "知的財産", "個人情報保護", "医療DX",
    "新年度予算（デジタル関連）"
]
SHORT_ASCII = {"ai", "it", "dx"}

def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()

def kw_hit(text: str) -> bool:
    t = normalize(text)
    for kw in KEYWORDS:
        k = normalize(kw)
        if k in SHORT_ASCII:
            if re.search(rf"(?:^|[^a-z0-9]){k}(?:[^a-z0-9]|$)", t):
                return True
        elif k in t:
            return True
    return False

# ───────── Fetch RSS ─────────────────────────────────────
def fetch_rss(url: str) -> str:
    resp = requests.get(url, timeout=(10, 30))
    resp.raise_for_status()
    return resp.text

# ───────── Parse and filter ─────────────────────────────
def scrape_cao_rss():
    xml = fetch_rss(RSS_URL)
    root = ET.fromstring(xml)

    # define namespaces
    ns = {
        'rss': 'http://purl.org/rss/1.0/',
        'dc':  'http://purl.org/dc/elements/1.1/'
    }

    items = root.findall('rss:item', ns)

    results = []
    for itm in items:
        title_el = itm.find('rss:title', ns)
        link_el  = itm.find('rss:link',  ns)
        date_el  = itm.find('dc:date',   ns)
        if title_el is None or link_el is None or date_el is None:
            continue

        title = title_el.text.strip()
        link  = link_el.text.strip()
        date_text = date_el.text.strip()

        # parse RFC822 or ISO8601 date
        dt = None
        try:
            dt = parsedate_to_datetime(date_text)
        except Exception:
            try:
                dt = datetime.fromisoformat(date_text)
            except Exception:
                continue

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        dt_jst = dt.astimezone(JST)
        dt0    = dt_jst.replace(hour=0, minute=0, second=0, microsecond=0)

        # date window filter
        if not (WIN_FROM <= dt0 <= TODAY):
            continue

        # keyword filter
        if not kw_hit(title):
            continue

        results.append({
            'dt':   dt0,
            'date': dt0.strftime('%-m月%-d日'),
            'title': title,
            'url':   link
        })

    # dedupe & sort descending
    seen = set()
    out = []
    for r in sorted(results, key=lambda x: x['dt'], reverse=True):
        key = (r['date'], r['title'])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)

    return out

# ───────── CLI ─────────────────────────────────────────
def main():
    recs = scrape_cao_rss()
    print("【内閣府】<br>")
    if not recs:
        print("DXやデジタル化に関連する新着情報および審議会等の開催はいずれもなし\n")
        return
    for r in recs:
        print(f"○{r['date']}　{r['title']}<br>\n")
        print(f"　{md_link(r['url'])}\n")

if __name__ == "__main__":
    main()

#ーーーーーーーーNISCーーーーーーーーーーーーー
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import unicodedata
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

def to_ascii(s: str) -> str:
    """
    全角数字などを半角に正規化するヘルパー
    """
    return unicodedata.normalize('NFKC', s)

def fetch_recent_nisc_news(days: int = 4):
    BASE_URL = 'https://www.nisc.go.jp'
    # 抽出対象とするキーワード
    KEYWORDS = [
        "デジタル", "情報通信", "サイバー", "AI", "DX", "ＤＸ", "IT", "SNS",
        "標準仕様", "ガイドライン", "無線局", "免許状", "光ファイバ"
    ]

    today     = datetime.now()
    threshold = today - timedelta(days=days)
    results   = []

    # —— デバッグ出力 ——
    #print(f'DEBUG: today     = {today.strftime("%Y-%m-%d")}')
    #print(f'DEBUG: threshold = {threshold.strftime("%Y-%m-%d")}')

    # 閾値〜今日までの各日付ページをチェック
    for delta in range((today - threshold).days + 1):
        dt  = threshold + timedelta(days=delta)
        url = f'{BASE_URL}/news/{dt.strftime("%Y%m%d")}.html'
        #print(f'DEBUG: checking URL = {url}')

        resp = requests.get(url)
        #print(f'DEBUG: raw status_code = {resp.status_code}')
        if resp.status_code != 200:
            continue

        # 1) バイト列でパースして<meta charset>を探す
        soup_bytes = BeautifulSoup(resp.content, 'html.parser')
        meta_charset = soup_bytes.find('meta', attrs={'charset': True})
        if meta_charset:
            encoding = meta_charset['charset']
        else:
            encoding = resp.encoding or resp.apparent_encoding or 'utf-8'
        #print(f'DEBUG: detected encoding = {encoding}')

        # 2) 正しいエンコーディングでテキストに変換し直し
        resp.encoding = encoding
        page_text = resp.text
        soup      = BeautifulSoup(page_text, 'html.parser')

        # 3) キーワードフィルタ
        full_text = soup.get_text()
        matched = any(kw in full_text for kw in KEYWORDS)
        #print(f'DEBUG: keyword match = {matched}')
        if not matched:
            continue

        # 4) タイトル取得
        h2 = soup.find('h2')
        title = h2.get_text(strip=True) if h2 else '[タイトル不明]'
        #print(f'DEBUG: title = {title}')

        # 5) ページ内から公開日をパース
        raw_date = ''
        elem = soup.find(string=re.compile(r'\d{4}年'))
        if elem:
            raw_date = elem.strip()
        #print(f'DEBUG: raw date_text = "{raw_date}"')

        ascii_date = to_ascii(raw_date)
        m = re.search(r'(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日', ascii_date)
        if m:
            y, mth, d = map(int, m.groups())
            dt_pub = datetime(y, mth, d)
            #print(f'DEBUG: parsed dt_pub = {dt_pub.strftime("%Y-%m-%d")}')
        else:
            # うまくパースできなければ URL 日付をそのまま使う
            dt_pub = dt
            #print(f'DEBUG: date parse failed, using URL date = {dt_pub.strftime("%Y-%m-%d")}')

        results.append((dt_pub, title, url))

    #print(f'DEBUG: total matched results = {len(results)}\n')

    # —— 最終出力 ——
    print('【国家サイバー統括室・NCO】<br>')
    if results:
        for dt_pub, title, url in sorted(results):
            print(f'○{dt_pub.month}月{dt_pub.day}日　「{title}」<br>')
            print(f'　{md_link(url)}\n')
    else:
        print(f'{threshold.month}月{threshold.day}日〜{today.month}月{today.day}日　'
              'DXやデジタル化に関連する新着情報および審議会等の開催はいずれもなし\n')


if __name__ == '__main__':
    fetch_recent_nisc_news(4)

#-----------金融庁ーーーーーーーーーーーー
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import unicodedata
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

def to_ascii(s: str) -> str:
    """
    全角数字などを半角に正規化
    """
    return unicodedata.normalize('NFKC', s)

def fetch_fsa_news(days: int = 4):
    BASE_URL = 'https://www.fsa.go.jp'
    # 抽出対象とするキーワード（人事・人事異動も追加）
    KEYWORDS = [
        "デジタル", "情報通信", "サイバー", "AI", "DX", "ＤＸ", "IT", "SNS",
        "標準仕様", "ガイドライン", "無線局", "免許状", "光ファイバ",
        "人事", "人事異動"
    ]

    today     = datetime.now()
    threshold = today - timedelta(days=days)
    results   = []

    # —— デバッグ出力 ——
    #print(f'DEBUG: today     = {today.strftime("%Y-%m-%d")}')
    #print(f'DEBUG: threshold = {threshold.strftime("%Y-%m-%d")}')

    # ① /inter/etc/YYYYMMDD/YYYYMMDD.html のループチェック
    for delta in range((today - threshold).days + 1):
        dt = threshold + timedelta(days=delta)
        subpath = f'/inter/etc/{dt.strftime("%Y%m%d")}/{dt.strftime("%Y%m%d")}.html'
        url     = BASE_URL + subpath
        #print(f'DEBUG: checking URL = {url}')

        resp = requests.get(url)
        #print(f'DEBUG: status_code = {resp.status_code}')
        if resp.status_code != 200:
            continue

        # エンコーディング検出＆再デコード
        soup_bytes   = BeautifulSoup(resp.content, 'html.parser')
        meta_charset = soup_bytes.find('meta', attrs={'charset': True})
        encoding = (meta_charset['charset']
                    if meta_charset
                    else resp.encoding or resp.apparent_encoding or 'utf-8')
        #print(f'DEBUG: detected encoding = {encoding}')
        resp.encoding = encoding
        soup = BeautifulSoup(resp.text, 'html.parser')

        # 公開日（令和表記）を本文からパース
        raw_date = ''
        date_elem = soup.find(string=re.compile(r'令和'))
        if date_elem:
            raw_date = date_elem.strip()
        #print(f'DEBUG: raw_date = "{raw_date}"')

        m = re.search(r'令和(\d+)年\s*(\d+)月\s*(\d+)日',
                      to_ascii(raw_date))
        if m:
            era, mth, d = map(int, m.groups())
            year = 2018 + era
            dt_pub = datetime(year, mth, d)
        else:
            dt_pub = dt
        #print(f'DEBUG: parsed dt_pub = {dt_pub.strftime("%Y-%m-%d")}')

        # タイトル取得
        title_tag = soup.find('title') or soup.find('h1') or soup.find('h2')
        title     = title_tag.get_text(strip=True) if title_tag else '[タイトル不明]'
        #print(f'DEBUG: title = {title}')

        # キーワードフィルタ（本文全体）
        full_text = soup.get_text()
        matched   = any(kw in full_text for kw in KEYWORDS)
        #print(f'DEBUG: keyword match = {matched}')
        if not matched:
            continue

        results.append((dt_pub, title, url))

    # ② 人事異動ページ （キーワードフィルタを適用）
    j_url = BASE_URL + '/common/about/jinji/index.html'
    #print(f'DEBUG: checking HR URL = {j_url}')
    resp = requests.get(j_url)
    #print(f'DEBUG: status_code = {resp.status_code}')
    if resp.status_code == 200:
        soup_bytes   = BeautifulSoup(resp.content, 'html.parser')
        meta_charset = soup_bytes.find('meta', attrs={'charset': True})
        encoding = (meta_charset['charset']
                    if meta_charset
                    else resp.encoding or resp.apparent_encoding or 'utf-8')
        #print(f'DEBUG: detected encoding HR = {encoding}')
        resp.encoding = encoding
        soup = BeautifulSoup(resp.text, 'html.parser')

        full_text = soup.get_text()
        matched   = any(kw in full_text for kw in KEYWORDS)
        #print(f'DEBUG: HR keyword match = {matched}')
        if matched:
            # 発令日をすべて抽出＆範囲チェック
            text_ascii = to_ascii(full_text)
            hr_dates = re.findall(r'令和7年\s*(\d+)月\s*(\d+)日発令', text_ascii)
            #print(f'DEBUG: HR raw matches = {hr_dates}')
            for mth_str, day_str in hr_dates:
                mth, day = int(mth_str), int(day_str)
                dt_pub = datetime(2018 + 7, mth, day)
                in_range = threshold <= dt_pub <= today
                #print(f'DEBUG: HR dt_pub = {dt_pub.strftime("%Y-%m-%d")}, in_range = {in_range}')
                if in_range:
                    title = f'人事異動（令和７年{mth}月{day}日付）について公表しました。'
                    results.append((dt_pub, title, j_url))

    #print(f'DEBUG: total matched results = {len(results)}\n')

    # —— 最終出力 ——
    print('【金融庁】<br>')
    if results:
        for dt_pub, title, url in sorted(results):
            print(f'○{dt_pub.month}月{dt_pub.day}日　「{title}」<br>')
            print(f'　{md_link(url)}\n')
    else:
        print(f'{threshold.month}月{threshold.day}日〜{today.month}月{today.day}日　'
              'DXやデジタル化に関連する新着情報および審議会等の開催はいずれもなし\n')


if __name__ == '__main__':
    fetch_fsa_news(4)


#-------公取--------
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JFTC.py - Enhanced version
・JFTC「スマホソフトウェア競争促進法」検討会 最近の開催状況ページから
  過去7日以内の「議事次第」PDFを抽出して出力します。
  取得できなければ「該当データなし」と表示します。
"""
import re
import unicodedata
import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from datetime import datetime, timedelta, timezone
import logging
import argparse
from typing import List, Dict, Optional, Tuple

# JST タイムゾーン
JST = timezone(timedelta(hours=9))

# ブラウザ風 User-Agent
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36")

def setup_logging(debug: bool = False) -> None:
    """ログ設定"""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

def fetch_jftc_activities(days_back: int = 7) -> Tuple[str, List[Dict]]:
    """
    JFTCの活動状況ページから議事次第PDFを抽出
    
    Args:
        days_back: 何日前までのデータを取得するか
        
    Returns:
        Tuple[str, List[Dict]]: (イベント名, 活動リスト)
    """
    url = "https://www.jftc.go.jp/soshiki/kyotsukoukai/kenkyukai/smlaw/katsudoujoukyou.html"
    headers = {
        "User-Agent": UA,
        "Referer": "https://www.jftc.go.jp/"
    }
    
    logging.info(f"Requesting URL: {url}")
    
    try:
        res = requests.get(url, headers=headers, timeout=30)
        logging.info(f"Response status: {res.status_code}")
        res.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Request failed: {e}")
        return "", []
    
    soup = BeautifulSoup(res.content, "html.parser")
    
    # イベント名取得
    evt = soup.find("a", href=re.compile(r"smlaw/katsudoujoukyou\.html"))
    event_name = evt.get_text(strip=True) if evt else \
        "スマートフォンにおいて利用される特定ソフトウェアに係る競争の促進に関する検討会"
    
    logging.info(f"Event name: {event_name}")
    
    cutoff = datetime.now(JST) - timedelta(days=days_back)
    logging.info(f"Cutoff ({days_back} days ago): {cutoff.isoformat()}")
    
    activities = []
    
    # 「議事次第」PDFリンクを抽出
    links = soup.find_all("a", href=re.compile(r"gijishidai.*\.pdf$"))
    logging.info(f"Found {len(links)} PDF links matching 'gijishidai'")
    
    for idx, a in enumerate(links, 1):
        raw_href = a["href"]
        href = raw_href if not raw_href.startswith("/") else "https://www.jftc.go.jp" + raw_href
        text = a.get_text(strip=True)
        
        logging.debug(f"Link #{idx}: text='{text}', href='{href}'")
        
        # 表の行（tr）を遡って日付を探す
        current_tr = a.find_parent("tr")
        if not current_tr:
            logging.debug("No parent tr found, skipping")
            continue
        
        # 表の行から日付情報を抽出
        cells = current_tr.find_all(["td", "th"])
        date_text = ""
        round_text = ""
        
        for cell in cells:
            cell_text = cell.get_text(strip=True)
            normalized_text = unicodedata.normalize("NFKC", cell_text)
            
            # 回数を抽出
            if re.search(r"第\d+回", normalized_text):
                round_text = normalized_text
            
            # 日付を抽出
            if re.search(r"令和\d+年\d+月\d+日", normalized_text):
                date_text = normalized_text
        
        logging.debug(f"Found date_text: '{date_text}', round_text: '{round_text}'")
        
        # 日付を解析
        if date_text:
            parsed_date = parse_japanese_date(date_text)
            if parsed_date:
                dt, _ = parsed_date
                
                # 回数が見つからない場合は行から推定
                if not round_text:
                    round_match = re.search(r"第(\d+)回", normalized_text)
                    if round_match:
                        round_text = f"第{round_match.group(1)}回"
                
                logging.debug(f"Parsed datetime: {dt.isoformat()}")
                
                if dt < cutoff:
                    logging.debug("Older than cutoff, skipping")
                    continue
                
                activities.append({
                    "date": f"{dt.month}月{dt.day}日",
                    "round": round_text or "不明",
                    "url": href,
                    "datetime": dt
                })
                logging.debug("Activity added")
            else:
                logging.debug("Could not parse date, skipping")
        else:
            logging.debug("No date found in row, skipping")
    
    # 日付順でソート（新しい順）
    activities.sort(key=lambda x: x["datetime"], reverse=True)
    
    logging.info(f"Total activities found: {len(activities)}")
    return event_name, activities

def parse_japanese_date(text: str) -> Optional[Tuple[datetime, str]]:
    """
    日本語の日付テキストを解析
    
    Args:
        text: 解析対象のテキスト
        
    Returns:
        Optional[Tuple[datetime, str]]: (日付, 回数ラベル) or None
    """
    # 日付パターン（令和X年Y月Z日）
    date_pattern = r"令和(\d+)年(\d+)月(\d+)日"
    date_match = re.search(date_pattern, text)
    
    if not date_match:
        return None
    
    era_year, month_str, day_str = date_match.groups()
    
    # 回数パターン（第X回）
    round_pattern = r"第(\d+)回"
    round_match = re.search(round_pattern, text)
    round_label = round_match.group(0) if round_match else "不明"
    
    # 令和を西暦に変換
    year = 2018 + int(era_year)
    month, day = int(month_str), int(day_str)
    
    try:
        dt = datetime(year, month, day, tzinfo=JST)
        return dt, round_label
    except ValueError as e:
        logging.error(f"Invalid date: {year}-{month}-{day}, error: {e}")
        return None

def format_output(event_name: str, activities: List[Dict], format_type: str = "text") -> str:
    """
    出力フォーマット
    
    Args:
        event_name: イベント名
        activities: 活動リスト
        format_type: 出力形式 ("text", "json", "markdown")
        
    Returns:
        str: フォーマットされた出力
    """
    if format_type == "json":
        import json
        return json.dumps({
            "event_name": event_name,
            "activities": activities
        }, ensure_ascii=False, indent=2)
    
    elif format_type == "markdown":
        output = f"# 【公正取引委員会】<br>\n\n"
        if not activities:
            output += "DXやデジタル化に関連する新着情報および審議会等の開催はいずれもなし\n"
        else:
            for a in activities:
                output += f"## {a['date']} {event_name}（{a['round']}）議事次第\n\n"
                output += f"[PDF]({a['url']})\n\n"
        return output
    
    else:  # text format - matches original output exactly
        output = "【公正取引委員会】<br>\n"
        if not activities:
            output += "DXやデジタル化に関連する新着情報および審議会等の開催はいずれもなし\n"
        else:
            for a in activities:
                output += f"○{a['date']}　{event_name}（{a['round']}）議事次第<br>\n"
                output += f"　{md_link(a['url'])}\n"
        return output

def get_jftc_activities(days_back: int = 7, format_type: str = "text", debug: bool = False) -> str:
    """
    JFTC活動情報を取得して文字列で返す（他のスクリプトから使用可能）
    
    Args:
        days_back: 何日前までのデータを取得するか
        format_type: 出力形式 ("text", "json", "markdown")
        debug: デバッグモードを有効にするか
        
    Returns:
        str: フォーマットされた活動情報
    """
    if debug:
        setup_logging(debug)
    
    try:
        event_name, activities = fetch_jftc_activities(days_back)
        return format_output(event_name, activities, format_type)
    except Exception as e:
        if debug:
            logging.error(f"Unexpected error: {e}")
        return f"エラーが発生しました: {e}"

def main():
    parser = argparse.ArgumentParser(description="JFTC議事次第PDFスクレイパー")
    parser.add_argument("--days", type=int, default=7, help="何日前までのデータを取得するか (default: 7)")
    parser.add_argument("--format", choices=["text", "json", "markdown"], default="text", help="出力形式")
    parser.add_argument("--debug", action="store_true", help="デバッグモードを有効にする")
    
    args = parser.parse_args()
    
    # デバッグモードが指定された場合のみログを表示
    if args.debug:
        setup_logging(args.debug)
    
    try:
        event_name, activities = fetch_jftc_activities(args.days)
        output = format_output(event_name, activities, args.format)
        print(output)
    except Exception as e:
        if args.debug:
            logging.error(f"Unexpected error: {e}")
        else:
            print(f"エラーが発生しました: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gov_dx_news_scraper.py  rev-2.9  (2025-07-22)

■ 指定キーワードで Google News RSS を検索し、
  ・キーワード一致チェックしたすべての記事を取得。
  ・SINCE_DAYS＝4日より古い記事は除外。
  ・取得元ソースを限定。
  ・不要なキーワードを含む記事を除外。
"""

import re, sys, html, time, hashlib, requests, xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

# ───────── 検索キーワード ──────────────────────────────────
KEYWORDS = [
    "DX","デジタル","クラウド","ガバメントクラウド","データセンター",
    "経済安全保障","QUAD","サプライチェーン","セキュリティクリアランス",
    "電気通信事業法","サイバーセキュリティ","Web3","半導体","AI",
    "GIGAスクール構想","量子コンピューター","スーパーコンピュータ",
    "スマホ新法","青少年インターネット環境整備法","Fintech",
    "中央銀行デジタル通貨","知的財産","個人情報保護","医療DX",
    "新年度予算 デジタル","スマホソフトウェア競争促進法",
    "apple","アップル","google","グーグル","相互運用性"
]

# ───────── 除外キーワード ──────────────────────────────────
EXCLUDE_KEYWORDS = [
    "Mrs. GREEN", "Mrs.GREEN", "ミセスグリーン",
    "プレスリリース", "PR TIMES", "PRTimes",
    "イベント", "セミナー", "講演会", "展示会",
    "求人", "採用", "募集中", "アルバイト",
    "占い", "星座", "運勢", "今日の運勢",
    "レシピ", "料理", "グルメ", "食べ物",
    "エンタメ", "芸能", "アイドル", "タレント",
    "スポーツ", "野球", "サッカー", "競技",
    "天気", "気象", "台風", "地震","フォトブック", "Mrs．GREEN ",
    "【ウェビナー】", "女優", "【火事】",
]

# ───────── フィルタ対象ニュースソース ────────────────────
FILTER_SOURCES = {
    "日経", "日本経済新聞", "日経ビジネス",
    "共同", "共同通信",
    "時事", "時事通信", "時事ドットコム",
    "朝日新聞", "朝日新聞デジタル",
    "読売新聞", "読売新聞オンライン",
    "毎日新聞",
    "産経", "産経新聞", "産経ニュース",
    "NHK", "NHKニュース",
    "ブルームバーグ", "Bloomberg",
    "東京新聞", "中日新聞",
    "東洋経済", "東洋経済オンライン"
    "日刊工業", "日刊工業新聞",
    "ロイター", "ロイター通信", "Reuters", "Reuters Japan",
}

# ───────── 設定 ──────────────────────────────────────────
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36")
JST = timezone(timedelta(hours=9))
SINCE_DAYS = 4
RSS_URL = "https://news.google.com/rss/search?hl=ja&gl=JP&ceid=JP:ja&q={}%20when:4d"


def strip_html(raw: str) -> str:
    return BeautifulSoup(html.unescape(raw), "html.parser").get_text(" ", strip=True)


def contains_exclude_keywords(text: str) -> bool:
    """除外キーワードが含まれているかチェック"""
    low_text = text.lower()
    for exclude_kw in EXCLUDE_KEYWORDS:
        if exclude_kw.lower() in low_text:
            return True
    return False


def fetch_hits(keyword: str):
    url = RSS_URL.format(quote_plus(keyword))
    headers = {"User-Agent": UA}
    xml_data = requests.get(url, headers=headers, timeout=30).content
    root = ET.fromstring(xml_data)

    for item in root.iterfind(".//item"):
        raw_title = strip_html(item.findtext("title", default=""))
        descr = strip_html(item.findtext("description", default=""))
        link = item.findtext("link", default="")
        source_elem = item.find("source")
        source_name = source_elem.text if source_elem is not None else ""

        # ── ソースフィルタ ──
        if source_name not in FILTER_SOURCES:
            continue

        # キーワード実在チェック
        low_kw = keyword.lower()
        low_txt = (raw_title + descr).lower()
        if low_kw not in low_txt:
            continue

        # ── 除外キーワードチェック ──
        if contains_exclude_keywords(raw_title + " " + descr):
            continue

        # タイトル補正：印刷画面
        title = raw_title
        if "印刷画面" in raw_title:
            try:
                page = requests.get(link, headers=headers, timeout=30).text
                soup2 = BeautifulSoup(page, "html.parser")
                meta_og = soup2.find("meta", property="og:title")
                if meta_og and meta_og.get("content"):
                    title = meta_og["content"]
                else:
                    h1 = soup2.find("h1")
                    title = h1.get_text(strip=True) if h1 else raw_title
            except Exception:
                title = raw_title.replace("印刷画面", "").strip()
        else:
            if source_name:
                title = re.sub(rf"\s*-\s*{re.escape(source_name)}$", "", title)
            title = title.replace("印刷画面", "").strip()

        # ── 補正後のタイトルでも除外キーワードチェック ──
        if contains_exclude_keywords(title):
            continue

        # 発行日時フィルタ
        try:
            dt = parsedate_to_datetime(item.findtext("pubDate", "")).astimezone(JST)
        except Exception:
            continue
        if dt < datetime.now(JST) - timedelta(days=SINCE_DAYS):
            continue

        yield {
            "dt": dt,
            "date": f"{dt.month}月{dt.day}日",
            "source": source_name,
            "title": title,
            "url": link
        }


def main():
    news, seen = [], set()
    excluded_count = 0  # 除外された記事数をカウント
    
    for kw in KEYWORDS:
        try:
            for hit in fetch_hits(kw):
                uid = hashlib.md5(hit["url"].encode()).hexdigest()
                if uid in seen:
                    continue
                seen.add(uid)
                news.append(hit)
        except Exception as e:
            print(f"[WARN] {kw}: {e}", file=sys.stderr)
        time.sleep(0.6)

    news.sort(key=lambda x: x["dt"])

    print("【ニュース】<br>")
    if not news:
        print("該当記事なし")
        return
    
    #print(f"取得記事数: {len(news)}件")
   # if excluded_count > 0:
    #    print(f"除外された記事数: {excluded_count}件")
    #print()
    
    for n in news:
        print(f"○{n['date']}　{n['title']}　{n['source']}<br>")
        print(f"　{md_link(n['url'])}\n")

if __name__ == "__main__":
    main()
