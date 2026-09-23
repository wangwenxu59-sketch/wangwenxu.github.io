#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全网招投标信息抓取器 —— 数据源：中国政府采购网搜索接口（覆盖全国各省公告）

工作原理：
  search.ccgp.gov.cn/bxsearch?searchtype=1&kw=<关键词>&page_index=<页码>
  - kw 为空时返回全站最新公告（所有省份、所有类型）
  - 每页约 20 条，含标题/摘要/时间/采购人/代理机构/公告类型/省份/原文链接

分类规则：
  招标类（公开招标/磋商/谈判/询价/邀请/单一来源）→ data/bids.json
  中标类（中标/成交/结果）                        → data/winners.json
  其他（更正/废标/流标/终止）                      → data/bids.json（type 标注）

用法：
  python3 scripts/scrape.py                # 抓最新 5 页（约100条）
  python3 scripts/scrape.py --pages 10     # 抓 10 页
  python3 scripts/scrape.py --kw 信息化,医疗  # 额外按关键词抓取
  python3 scripts/scrape.py --dry-run      # 只打印不写文件
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# ---- 常量 ----
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(ROOT, "data")
BIDS_FILE = os.path.join(DATA_DIR, "bids.json")
WINNERS_FILE = os.path.join(DATA_DIR, "winners.json")

SEARCH_URL = "https://search.ccgp.gov.cn/bxsearch?searchtype=1&page_index={page}&bidSort=0&pinMu=0&bidType=0&dbselect=bidx&kw={kw}"
MAX_RECORDS = 800          # 每个文件最多保留条数（控制仓库体积）
PAGE_DELAY = 2.5           # 每页抓取间隔（秒），礼貌抓取
TIMEOUT = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.ccgp.gov.cn/",
    "Connection": "keep-alive",
}

# 公告类型 → 目标文件 & 类型标签
BID_TYPES = ["公开招标", "竞争性磋商", "竞争性谈判", "询价", "邀请招标", "单一来源", "竞价", "框架协议"]
WIN_TYPES = ["中标", "成交", "结果", "合同公告"]


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="ignore")


# ---------- 解析 ----------
PROVINCES = ["北京", "天津", "河北", "山西", "内蒙古", "辽宁", "大连", "吉林", "黑龙江",
             "上海", "江苏", "浙江", "宁波", "安徽", "福建", "厦门", "江西", "山东", "青岛",
             "河南", "湖北", "湖南", "广东", "深圳", "广西", "海南", "重庆", "四川", "贵州",
             "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆", "台湾", "香港", "澳门"]


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s).strip()


def parse_page(html):
    """解析一页搜索结果，返回记录列表。

    页面结构（结果区）：
      <ul class="vT-srch-result-list-bid">
        <li>
          <a href="...">标题（含<font>高亮）</a>
          <p>摘要（可能为空）</p>
          <span>2026.09.23 12:30:10 | 采购人：X | 代理机构：Y <br/>
            <strong>公告类型</strong> | 省份 | <strong>品目</strong>
          </span>
        </li>...
      </ul>
      页面底部 var ohtmlurls="url1,url2,..." 为真实链接（按顺序对应每个 li）
    """
    records = []

    # 1. 真实链接列表（按顺序对应每个 li）
    m = re.search(r'var\s+ohtmlurls\s*=\s*"([^"]+)"', html)
    true_urls = [u for u in m.group(1).strip(",").split(",") if u] if m else []

    # 2. 限定在结果列表容器内解析
    ul_m = re.search(r'<ul class="vT-srch-result-list-bid">(.*?)</ul>', html, re.S)
    if not ul_m:
        return records
    lis = re.findall(r"<li>(.*?)</li>", ul_m.group(1), re.S)

    for idx, li in enumerate(lis):
        # 标题 + 链接
        a_m = re.search(r'<a href="([^"]*)"[^>]*>(.*?)</a>', li, re.S)
        if not a_m:
            continue
        title = re.sub(r"\s+", " ", strip_tags(a_m.group(2)))
        if not title or len(title) > 150:   # 过滤侧栏/异常长标题
            continue

        # 摘要
        p_m = re.search(r"<p>(.*?)</p>", li, re.S)
        summary = re.sub(r"\s+", " ", strip_tags(p_m.group(1))) if p_m else ""
        summary = summary[:220]

        # span 元数据
        span_m = re.search(r"<span>(.*?)</span>", li, re.S)
        span_html = span_m.group(1) if span_m else ""
        meta = strip_tags(span_html)

        date_m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}:\d{2})", meta)
        publish_date = f"{date_m.group(1)}-{date_m.group(2)}-{date_m.group(3)}" if date_m else ""
        publish_time = date_m.group(4) if date_m else ""

        buyer = agency = ""
        b_m = re.search(r"采购人[：:]\s*([^|]+?)\s*(?:\||$)", meta)
        if b_m:
            buyer = b_m.group(1).strip()
        a_m2 = re.search(r"代理机构[：:]\s*([^|]+?)\s*(?:\||$)", meta)
        if a_m2:
            agency = a_m2.group(1).strip()

        # 公告类型（第一个 strong）与品目（第二个 strong）
        strongs = [strip_tags(s) for s in re.findall(r"<strong[^>]*>(.*?)</strong>", span_html, re.S)]
        notice_type = strongs[0] if strongs else ""
        item_class = strongs[1] if len(strongs) > 1 else ""   # 货物类/工程类/服务类

        # 省份：从 meta 中匹配省级行政区名
        region = ""
        for p in PROVINCES:
            if p in meta:
                region = p
                break

        # 真实 URL：优先用 ohtmlurls 按索引对齐
        url = true_urls[idx] if idx < len(true_urls) else a_m.group(1)
        if not url.startswith("http"):
            url = "http://www.ccgp.gov.cn" + url

        records.append({
            "title": title,
            "summary": summary,
            "publishDate": publish_date,
            "publishTime": publish_time,
            "buyer": buyer,
            "agency": agency,
            "noticeType": notice_type,
            "region": region,
            "itemClass": item_class,
            "url": url,
        })
    return records


def classify(rec):
    """根据公告类型决定写入哪个文件，并补充展示字段。"""
    t = rec.get("noticeType", "") or ""
    title = rec.get("title", "")

    if any(k in t for k in WIN_TYPES) or any(k in title[:12] for k in WIN_TYPES):
        target = "winners"
    else:
        target = "bids"

    # 类型标签
    display_type = t if t else "公告"
    if any(k in display_type for k in WIN_TYPES):
        display_type = "中标公示"
    elif any(k in display_type for k in BID_TYPES):
        display_type = "招标公告"

    # 分类：优先用来源标注的品目（货物类/工程类/服务类）
    item_class = (rec.get("itemClass") or "").strip()
    if "工程" in item_class:
        cat = "工程"
    elif "货物" in item_class:
        cat = "货物"
    elif "服务" in item_class:
        cat = "服务"
    else:
        cat = guess_category(title + " " + rec.get("summary", ""))

    return target, display_type, cat


def guess_category(text):
    if any(k in text for k in ["工程", "施工", "建设", "改造", "安装", "装修", "修缮", "市政", "公路", "水利", "建筑"]):
        return "工程"
    if any(k in text for k in ["设备", "物资", "采购", "材料", "服务器", "电脑", "硬件", "软件产品", "医疗器械"]):
        return "货物"
    if any(k in text for k in ["服务", "运维", "咨询", "监理", "设计", "租赁", "维护", "物业", "保险", "审计"]):
        return "服务"
    return "政府采购"


def make_id(rec, seq):
    d = rec.get("publishDate", "").replace("-", "")
    return f"CG-{d or 'NA'}-{seq:04d}"


# ---------- 存储 ----------
def load(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def merge(existing, new_records):
    """按 URL 去重合并，保留最新。"""
    by_url = {x["url"]: x for x in existing if x.get("url")}
    seq = len(existing) + 1
    added = 0
    for rec in new_records:
        u = rec.get("url")
        if not u or u in by_url:
            continue
        by_url[u] = rec
        added += 1
    merged = sorted(by_url.values(),
                    key=lambda x: (x.get("publishDate", ""), x.get("publishTime", "")),
                    reverse=True)
    return merged[:MAX_RECORDS], added


def to_site_record(rec, seq):
    """转成站点 schema。"""
    target, display_type, cat = classify(rec)
    out = {
        "id": make_id(rec, seq),
        "title": rec["title"],
        "type": display_type,
        "category": cat,
        "region": rec.get("region", "") or "全国",
        "buyer": rec.get("buyer", ""),
        "agency": rec.get("agency", ""),
        "budgetText": "",
        "publishDate": rec.get("publishDate", ""),
        "deadline": "",
        "url": rec.get("url", ""),
        "source": "中国政府采购网",
        "summary": rec.get("summary", ""),
        "tags": guess_tags(rec["title"]),
    }
    return out, target


def guess_tags(title):
    tags = []
    rules = [
        ("信息化", ["信息化", "数字化", "智慧", "系统"]), ("医疗", ["医院", "医疗", "卫生", "妇幼"]),
        ("教育", ["学校", "教育", "大学", "中学", "小学"]), ("市政", ["市政", "道路", "管网", "路灯"]),
        ("安防", ["公安", "消防", "警务", "监控", "应急"]), ("乡村振兴", ["乡村", "农村", "农业", "扶贫"]),
        ("环保", ["环保", "生态", "污水", "垃圾", "监测"]), ("交通", ["交通", "公路", "铁路", "机场", "轨道"]),
    ]
    for tag, kws in rules:
        if any(k in title for k in kws):
            tags.append(tag)
    return tags[:3]


# ---------- 主流程 ----------
def scrape_keyword(kw, pages):
    """抓取一个关键词（可为空）的多页结果。"""
    all_recs = []
    for page in range(1, pages + 1):
        url = SEARCH_URL.format(page=page, kw=urllib.parse.quote(kw))
        try:
            html = fetch(url)
        except Exception as e:
            print(f"  [第{page}页] 抓取失败：{e}", file=sys.stderr)
            continue
        if "频繁访问" in html or "vT-srch-result-list" not in html:
            print(f"  [第{page}页] 被限流或无结果，跳过", file=sys.stderr)
            time.sleep(PAGE_DELAY)
            continue
        recs = parse_page(html)
        print(f"  [第{page}页] 解析 {len(recs)} 条", file=sys.stderr)
        all_recs.extend(recs)
        if page < pages:
            time.sleep(PAGE_DELAY)
    return all_recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=5, help="每个关键词抓取页数")
    ap.add_argument("--kw", default="", help="额外关键词，逗号分隔（空关键词=全站最新）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    keywords = [""]  # 空关键词 = 全站最新公告
    if args.kw:
        keywords += [k.strip() for k in args.kw.split(",") if k.strip()]

    bids = load(BIDS_FILE)
    winners = load(WINNERS_FILE)
    print(f"现有数据：bids={len(bids)}, winners={len(winners)}", file=sys.stderr)

    raw_new = []
    for kw in keywords:
        label = kw or "（全站最新）"
        print(f"\n>>> 抓取关键词：{label}（{args.pages} 页）", file=sys.stderr)
        raw_new.extend(scrape_keyword(kw, args.pages))

    # 去重（URL）
    seen = set()
    unique = []
    for r in raw_new:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)
    print(f"\n合计抓取 {len(raw_new)} 条，去重后 {len(unique)} 条", file=sys.stderr)

    if not unique:
        print("没有抓到任何数据，退出。", file=sys.stderr)
        return 0

    if args.dry_run:
        for r in unique[:10]:
            print(json.dumps(r, ensure_ascii=False))
        return 0

    # 按类型分桶
    new_bids, new_winners = [], []
    seq = 1
    for rec in unique:
        site_rec, target = to_site_record(rec, seq)
        seq += 1
        if target == "winners":
            new_winners.append(site_rec)
        else:
            new_bids.append(site_rec)

    merged_bids, add_b = merge(bids, new_bids)
    merged_winners, add_w = merge(winners, new_winners)

    save(BIDS_FILE, merged_bids)
    save(WINNERS_FILE, merged_winners)
    print(f"\n✅ bids.json: +{add_b}（总 {len(merged_bids)}）", file=sys.stderr)
    print(f"✅ winners.json: +{add_w}（总 {len(merged_winners)}）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())