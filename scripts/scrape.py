#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓取中国政府采购网公开招标公告的脚本（best-effort）。
将新公告追加到 data/bids.json。

特点：
- 仅使用 Python 标准库（urllib + html.parser），无需额外安装
- 多端点 fallback，抓不到不会报错退出（exit 0），方便 Action 周期运行
- 自动去重：以 (title, publishDate) 作为唯一键
- 仅抓取公告日期 >= DATA_MIN_DATE 的记录，默认 30 天内

用法：
  python3 scripts/scrape.py            # 抓取最近 30 天
  python3 scripts/scrape.py --days 7   # 抓取最近 7 天
  python3 scripts/scrape.py --dry-run  # 只打印，不写文件
"""
import argparse
import datetime as dt_mod
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from html.parser import HTMLParser

# ---- 常量 ----
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DATA_FILE = os.path.join(ROOT, "data", "bids.json")

# 候选数据端点（按优先级），如全部失败则优雅退出
SOURCES = [
    # RSS / 列表页
    ("ccgp_list", "http://www.ccgp.gov.cn/cggg/zyfg/index.htm", "list"),
    ("ccgp_gongcheng", "http://www.ccgp.gov.cn/cggg/zygg/index.htm", "list"),
    ("ccgp_all", "http://www.ccgp.gov.cn/cggg/index.htm", "list"),
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TIMEOUT = 15
MAX_RECORDS = 50


# ---- HTML 解析 ----
class ListPageParser(HTMLParser):
    """从 ccgp 列表页提取 (title, url, date) 三元组。"""
    def __init__(self):
        super().__init__()
        self.items = []
        self._in_li = False
        self._current = None
        self._capture_title = False

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "li" and "list_item" in (attrs_d.get("class") or "").replace(" ", ""):
            self._in_li = True
            self._current = {"title": "", "url": "", "date": "", "_attrs": attrs_d}
        if self._in_li and tag == "a":
            href = attrs_d.get("href", "")
            if href and not href.startswith("javascript"):
                self._current["url"] = "http://www.ccgp.gov.cn" + href if href.startswith("/") else href
                self._capture_title = True
        if self._in_li and tag == "span":
            self._capture_title = True

    def handle_data(self, data):
        if not self._in_li or not self._current:
            return
        data = data.strip()
        if not data:
            return
        if self._capture_title and not self._current["title"]:
            self._current["title"] += data
            self._capture_title = False
        elif re.match(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}", data):
            self._current["date"] = data

    def handle_endtag(self, tag):
        if tag == "li" and self._in_li:
            if self._current and self._current["title"]:
                self.items.append({
                    "title": re.sub(r"\s+", " ", self._current["title"]).strip(),
                    "url": self._current["url"],
                    "date": self._current["date"],
                })
            self._in_li = False
            self._current = None


# ---- 抓取 ----
def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="ignore")


def scrape():
    """依次尝试每个数据源，返回标准化记录数组。"""
    for name, url, kind in SOURCES:
        try:
            html = fetch(url)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"[{name}] 网络失败：{e}", file=sys.stderr)
            continue

        parser = ListPageParser()
        try:
            parser.feed(html)
        except Exception as e:
            print(f"[{name}] 解析失败：{e}", file=sys.stderr)
            continue

        records = []
        for raw in parser.items[:MAX_RECORDS]:
            if not raw["title"] or not raw["date"]:
                continue
            date_iso = normalize_date(raw["date"])
            if not date_iso:
                continue
            records.append({
                "id": f"BID-{date_iso.replace('-', '')}-{len(records)+1:03d}",
                "title": raw["title"],
                "type": "招标公告",
                "category": guess_category(raw["title"]),
                "region": guess_region(raw["title"]),
                "buyer": "",
                "agency": "",
                "budget": 0,
                "budgetText": "",
                "publishDate": date_iso,
                "deadline": "",
                "url": raw["url"],
                "source": "中国政府采购网",
                "summary": "",
                "tags": [],
            })

        if records:
            print(f"[{name}] 抓取到 {len(records)} 条", file=sys.stderr)
            return records

        print(f"[{name}] 未解析到记录", file=sys.stderr)

    return []


def normalize_date(s):
    m = re.search(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
    if not m:
        return ""
    y, mo, d = m.groups()
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def guess_category(title):
    if any(k in title for k in ["工程", "建设", "施工", "改造", "安装"]):
        return "工程"
    if any(k in title for k in ["设备", "采购", "物资", "材料", "服务器", "电脑"]):
        return "货物"
    if any(k in title for k in ["服务", "运维", "咨询", "监理", "设计", "课程"]):
        return "服务"
    return "政府采购"


def guess_region(title):
    provinces = ["北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
                 "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
                 "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海",
                 "内蒙古", "广西", "西藏", "宁夏", "新疆"]
    for p in provinces:
        if p in title:
            return p
    return "全国"


# ---- 数据合并 ----
def load_existing():
    if not os.path.exists(DATA_FILE):
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def merge(existing, scraped, threshold_days=30):
    cutoff = dt_mod.date.today() - dt_mod.timedelta(days=threshold_days)
    keys = {(x.get("title", ""), x.get("publishDate", "")) for x in existing}
    new = []
    for rec in scraped:
        key = (rec["title"], rec["publishDate"])
        if key in keys:
            continue
        try:
            d = dt_mod.date.fromisoformat(rec["publishDate"])
        except ValueError:
            continue
        if d < cutoff:
            continue
        keys.add(key)
        new.append(rec)
    return new


# ---- 入口 ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    existing = load_existing()
    print(f"现有 {len(existing)} 条记录", file=sys.stderr)

    scraped = scrape()
    if not scraped:
        print("本次未抓到任何新数据，退出。", file=sys.stderr)
        return 0

    new_records = merge(existing, scraped, args.days)
    if not new_records:
        print("没有新增数据。", file=sys.stderr)
        return 0

    merged = new_records + existing
    print(f"新增 {len(new_records)} 条，写入 {DATA_FILE}", file=sys.stderr)

    if args.dry_run:
        print(json.dumps(new_records, ensure_ascii=False, indent=2))
        return 0

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())