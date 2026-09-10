#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云刷听歌记录脚本（配合 GitHub Actions 使用，支持多账号）

登录方式（优先级从高到低）：
1. NETEASE_COOKIES     —— 多账号：每行一个 MUSIC_U（推荐，多账号分摊刷）
2. NETEASE_COOKIE      —— 单账号：网页版 Cookie 中的 MUSIC_U 值

两种刷记录模式（NETEASE_MODE）：
- real   （默认）真实听歌：获取歌曲 128kbps 音频流，按真实播放速度边听边计时，
         听完后再上报播放记录。服务器端有真实拉流流量，记录更可信。
         非 VIP 试听片段不足 35s 时会"单曲循环"补足流量再上报。
- report 仅上报：不做真实拉流，直接按模拟时长上报播放记录，速度快。

其他可选环境变量：
- NETEASE_COUNT         每账号每批刷的次数（real 模式默认 20）
- NETEASE_GOAL          所有账号总目标次数，按账号数分摊配额，默认 800
- NETEASE_ACCOUNT_GAP   账号切换之间的平均休息秒数，默认 45
- NETEASE_PLAYLIST      歌单 ID，从该歌单取歌；默认取云音乐热歌榜(3778678)
- NETEASE_SONG_IDS      自定义歌曲 ID 列表（英文逗号分隔），优先级高于歌单
- NETEASE_INTERVAL      每首歌之间的平均间隔秒数，默认 5
- NETEASE_SPEED         real 模式听歌倍速，默认 1.0（真实速度）
- NETEASE_MAX_DURATION  real 模式单首最长听歌秒数，默认 600

运行：
- python main.py            正常运行（CI 中使用）
- python main.py --dry-run  本地试运行，不联网、不写文件
"""

import argparse
import base64
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from Crypto.Cipher import AES

# ---------------- 网易云 weapi 加密常量 ----------------
MODULUS = (
    "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b7251"
    "52b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecb"
    "da92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813c"
    "fe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7"
)
NONCE = b"0CoJUm6Qyw8W8jud"
PUBKEY = "010001"
IV = b"0102030405060708"
BASE = "https://music.163.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
HOT_PLAYLIST = "3778678"  # 云音乐热歌榜
BITRATE_BPS = 128000      # 128kbps 标准音质（免费歌曲可用）
BYTES_PER_SEC = BITRATE_BPS / 8  # ≈16000 B/s，用于真实速度拉流计时

# 兜底歌曲 ID（歌单拉取失败时使用，可自行替换为自己常听的歌）
DEFAULT_SONGS = [
    186016, 25906124, 436514312, 347230, 28949444,
    5264842, 1492502522, 516076896, 29806077, 1488745236,
    1370166846, 483057332, 444989022, 29781192, 27944092,
    26094983, 31654455, 495373412, 447206531, 2034742057,
]

ROOT = Path(__file__).resolve().parent
DATA_FILE = ROOT / "docs" / "data.json"
TZ = timezone(timedelta(hours=8))


def log(msg: str) -> None:
    print(f"[{datetime.now(TZ).strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------- weapi 加密 ----------------
def aes_encrypt(text: str, key: bytes) -> str:
    pad = 16 - len(text.encode("utf-8")) % 16
    text = text + chr(pad) * pad
    cipher = AES.new(key, AES.MODE_CBC, IV)
    return base64.b64encode(cipher.encrypt(text.encode("utf-8"))).decode()


def rsa_encrypt(secret_key: str) -> str:
    rs = pow(
        int(secret_key[::-1].encode("utf-8").hex(), 16),
        int(PUBKEY, 16),
        int(MODULUS, 16),
    )
    return format(rs, "x").zfill(256)


def weapi(data: dict) -> dict:
    text = json.dumps(data)
    secret_key = "".join(
        random.sample(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", 16
        )
    )
    params = aes_encrypt(aes_encrypt(text, NONCE), secret_key.encode("utf-8"))
    return {"params": params, "encSecKey": rsa_encrypt(secret_key)}


# ---------------- API 客户端 ----------------
class NeteaseClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Referer": "https://music.163.com",
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                # 海外服务器（GitHub Actions 等）访问会被风控：播放地址接口
                # 对海外 IP 返回空。加 X-Real-IP 伪装国内出口（社区通用做法）。
                "X-Real-IP": random.choice(["116.25.146.177", "218.76.205.99",
                                            "112.45.28.101", "183.232.170.42"]),
            }
        )
        self.csrf = ""

    def _post(self, path: str, data: dict):
        url = BASE + path
        payload = weapi(data)
        if self.csrf:
            url += f"?csrf_token={self.csrf}"
        resp = self.session.post(url, data=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ---- 登录 ----
    def login_by_password(self, phone: str, password: str, country_code: str = "86"):
        """手机号 + 密码登录，返回是否成功"""
        md5_pwd = hashlib.md5(password.encode("utf-8")).hexdigest()
        result = self._post(
            "/weapi/login/cellphone",
            {
                "phone": phone,
                "countrycode": country_code,
                "password": md5_pwd,
                "rememberLogin": "true",
            },
        )
        code = result.get("code")
        if code != 200:
            log(f"登录失败，接口返回 code={code}，message={result.get('message', result.get('msg', ''))}")
            log("提示：密码登录容易触发验证码/风控，建议改用 Cookie(MUSIC_U) 方式，详见 README。")
            return False
        music_u = None
        for c in self.session.cookies:
            if c.name == "MUSIC_U":
                music_u = c.value
                break
        if not music_u:
            log("登录成功但未获取到 MUSIC_U Cookie")
            return False
        self.csrf = result.get("cookie", "")
        if "__csrf=" in self.csrf:
            self.csrf = self.csrf.split("__csrf=")[-1].split(";")[0]
        else:
            self.csrf = ""
        nickname = (result.get("profile") or {}).get("nickname", "")
        log(f"登录成功：{nickname or phone}（密码方式）")
        return True

    def set_cookie(self, music_u: str):
        """直接使用网页版 Cookie 中的 MUSIC_U"""
        self.session.cookies.set("MUSIC_U", music_u, domain="music.163.com")
        log("已使用 Cookie(MUSIC_U) 方式初始化")

    # ---- 歌曲信息 ----
    def get_playlist_song_ids(self, playlist_id: str):
        """拉取歌单内歌曲 ID 列表"""
        try:
            result = self._post(
                "/weapi/v6/playlist/detail",
                {"id": str(playlist_id), "n": "500", "s": "8", "v": "6"},
            )
            track_ids = [
                t["id"] for t in (result.get("playlist") or {}).get("trackIds", [])
            ]
            if track_ids:
                log(f"已从歌单 {playlist_id} 获取 {len(track_ids)} 首歌曲")
                return track_ids
            log(f"歌单 {playlist_id} 未返回歌曲，接口 code={result.get('code')}")
        except Exception as exc:  # noqa: BLE001
            log(f"拉取歌单失败：{exc}")
        return []

    def get_song_infos(self, song_ids: list) -> dict:
        """批量获取歌曲信息（名称、时长），返回 {id: {"name":.., "duration": 秒}}"""
        infos = {}
        try:
            result = self._post(
                "/weapi/v3/song/detail",
                {
                    "c": json.dumps([{"id": sid} for sid in song_ids]),
                    "ids": json.dumps(song_ids),
                },
            )
            for s in result.get("songs", []):
                infos[s["id"]] = {
                    "name": s.get("name", ""),
                    "duration": int(s.get("dt", 240000) / 1000),
                }
        except Exception as exc:  # noqa: BLE001
            log(f"获取歌曲详情失败：{exc}")
        return infos

    def get_playable_urls(self, song_ids: list) -> dict:
        """批量获取 128kbps 播放地址，返回 {id: url}（VIP 歌曲无 url 会被过滤）
        海外 IP 偶发被风控返回空，最多重试 3 次。"""
        urls = {}
        for attempt in range(3):
            try:
                result = self._post(
                    "/weapi/song/enhance/player/url",
                    {
                        "ids": json.dumps(song_ids),
                        "br": str(BITRATE_BPS),
                        "csrf_token": self.csrf,
                    },
                )
                for d in result.get("data", []):
                    if d.get("url"):
                        urls[d["id"]] = d["url"]
                if urls:
                    return urls
                log(f"获取播放地址为空（第 {attempt + 1}/3 次），5s 后重试…")
            except Exception as exc:  # noqa: BLE001
                log(f"获取播放地址失败（第 {attempt + 1}/3 次）：{exc}")
            time.sleep(5)
        return urls

    # ---- 真实听歌 ----
    def stream_song(self, url: str, duration_s: int, speed: float = 1.0,
                    min_listen: int = 35) -> int:
        """
        以接近真人听歌节奏拉取音频流（边听边计时）：
        - 前 15% 稍微慢一点（前奏找人声）
        - 中段 70% 匀速推进
        - 尾段 15% 稍慢（落下尾奏/操作手机）
        - 流不足 min_listen 秒（如非 VIP 只有 18s 试听片段）时，
          像"单曲循环"一样重新发起请求补足真实流量，凑够服务端
          计入听歌记录的时长阈值（time>=30s）。
        返回实际"听"的秒数；异常中断时返回已听秒数。
        """
        start = time.time()
        target = max(min_listen, int(duration_s))
        got = 0          # 累计拉到的字节数（可跨多次请求）
        loop_count = 0   # 第几遍"循环"
        try:
            while True:
                loop_count += 1
                if loop_count > 6:  # 防御：最多循环 6 遍
                    break
                done = False
                with self.session.get(
                    url, stream=True, timeout=(10, 30),
                    headers={"Range": "bytes=0-"},
                ) as resp:
                    if resp.status_code not in (200, 206):
                        return 0 if loop_count == 1 else got // int(BYTES_PER_SEC)
                    for chunk in resp.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        got += len(chunk)
                        # 已拉取数据对应的音频时长（128kbps ≈ 16KB/s）
                        audio_time = got / BYTES_PER_SEC
                        if audio_time >= target:
                            done = True
                            break
                        # 节奏因子：pace>1 表示拉相同字节时睡眠更久，更像真人
                        if audio_time < target * 0.15:
                            pace = 1.35   # 前奏：把人声哼出来
                        elif audio_time > target * 0.85:
                            pace = 1.20   # 尾奏：稍微留一下
                        else:
                            pace = 1.00   # 中段接近实时
                        desired_elapsed = (audio_time / speed) * pace
                        now = time.time() - start
                        if desired_elapsed > now:
                            time.sleep(min(desired_elapsed - now, 3))
                if done:
                    break
                # 本遍流已到 EOF 但还没听够 → 单曲循环，补一小段间隔再听一遍
                if got / BYTES_PER_SEC < target and loop_count < 6:
                    time.sleep(random.uniform(0.5, 1.5))
                    continue
                break
            audio_time = got / BYTES_PER_SEC
            return int(min(target, max(audio_time, 0)))
        except Exception as exc:  # noqa: BLE001
            log(f"拉流中断：{exc}")
            return int(time.time() - start)

    # ---- 上报播放记录 ----
    def scrobble(self, song_id: int, duration_s: int, played_seconds: int = None) -> bool:
        """
        仿官方客户端的 4 段进度上报（playstart / 30% / 60% / playend）。
        真实网易云客户端在播放过程中会分批上报进度，最后一条 end="playend"。
        仅当最后一条的 time>=30s 且 end=playend 时，服务端才会把这条记录写入
        "最近听过/累计听歌"统计。一次性把这些批次发完，能显著提高入库成功率。
        """
        if played_seconds is None:
            played_seconds = duration_s
        d = max(int(played_seconds), 35)  # 服务端的"听完"阈值

        def _entry(action_time: int, end: str = "") -> dict:
            return {
                "action": "play",
                "json": {
                    "type": "song",
                    "wifi": 0,
                    "download": 0,
                    "time": int(action_time),
                    "end": end,
                    "sourceId": "",
                    "id": song_id,
                    "mMids": [],
                    "hash": "",
                },
            }

        logs = [
            _entry(5),                                # 进入播放器第 5s
            _entry(d * 0.30),                         # 进度 30%
            _entry(d * 0.60),                         # 进度 60%
            _entry(d, end="playend"),                 # 结束（关键：服务端统计这条）
        ]
        try:
            result = self._post(
                "/weapi/feedback/weblog",
                {"logs": json.dumps(logs)},
            )
            if result.get("code") != 200:
                log(
                    f"上报返回 code={result.get('code')}, "
                    f"message={result.get('message', '')[:120]}"
                )
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"上报异常：{exc}")
            return False


# ---------------- 记录文件维护 ----------------
def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {"totalPlays": 0, "updatedAt": "", "records": []}


def _migrate(data: dict) -> dict:
    """把旧格式（无 accounts）迁移为分账号结构：旧数据归入第一个账号"""
    if not isinstance(data.get("accounts"), list) or not data["accounts"]:
        old_plays = int(data.get("totalPlays", 0))
        old_secs = int(data.get("totalListenedSeconds", 0))
        old_records = data.get("records", [])
        data["accounts"] = [
            {
                "id": "0",
                "nickname": "默认账号",
                "totalPlays": old_plays,
                "totalListenedSeconds": old_secs,
                "records": old_records,
            }
        ]
    return data


def _merge_records(records: list, today: str, plays: int, seconds: int) -> list:
    today_rec = next((r for r in records if r.get("date") == today), None)
    if today_rec:
        today_rec["plays"] = int(today_rec.get("plays", 0)) + plays
        today_rec["seconds"] = int(today_rec.get("seconds", 0)) + seconds
    else:
        records.append({"date": today, "plays": plays, "seconds": seconds})
    return sorted(records, key=lambda r: r["date"])[-90:]


def save_data(account_results: list) -> dict:
    """
    把本次各账号成功次数写入 docs/data.json（同一天累加）。
    account_results: [{"id": "0", "nickname": "xx", "success": n, "listened": s}, ...]
    顶层 totalPlays 保持为所有账号总和（self-continue 用它对账目标）。
    """
    data = _migrate(load_data())
    today = datetime.now(TZ).strftime("%Y-%m-%d")

    for res in account_results:
        acc = next(
            (a for a in data["accounts"] if str(a.get("id")) == str(res["id"])), None
        )
        if acc is None:
            acc = {
                "id": str(res["id"]),
                "nickname": res.get("nickname", str(res["id"])),
                "totalPlays": 0,
                "totalListenedSeconds": 0,
                "records": [],
            }
            data["accounts"].append(acc)
        acc["nickname"] = res.get("nickname") or acc.get("nickname") or str(res["id"])
        acc["totalPlays"] = int(acc.get("totalPlays", 0)) + res["success"]
        acc["totalListenedSeconds"] = (
            int(acc.get("totalListenedSeconds", 0)) + res["listened"]
        )
        acc["records"] = _merge_records(
            acc.get("records", []), today, res["success"], res["listened"]
        )

    # 顶层汇总（兼容旧看板 / self-continue）
    total_plays = sum(int(a.get("totalPlays", 0)) for a in data["accounts"])
    total_secs = sum(int(a.get("totalListenedSeconds", 0)) for a in data["accounts"])
    merged_daily = {}
    for a in data["accounts"]:
        for r in a.get("records", []):
            d = r["date"]
            merged_daily.setdefault(d, {"plays": 0, "seconds": 0})
            merged_daily[d]["plays"] += int(r.get("plays", 0))
            merged_daily[d]["seconds"] += int(r.get("seconds", 0))
    data["records"] = [
        {"date": d, "plays": v["plays"], "seconds": v["seconds"]}
        for d, v in sorted(merged_daily.items())[-90:]
    ]
    data["totalPlays"] = total_plays
    data["totalListenedSeconds"] = total_secs
    data["updatedAt"] = datetime.now(TZ).isoformat(timespec="seconds")
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def _brush_one_account(idx: int, music_u: str, songs: list, infos: dict,
                       mode: str, speed: float, max_duration: int,
                       interval: float, quota_left: int, count: int,
                       dry: bool = False) -> dict:
    """为单个账号刷歌。返回 {"id","nickname","success","listened"}"""
    client = NeteaseClient()
    client.set_cookie(music_u)
    nickname = f"账号{idx + 1}"

    # 校验 MUSIC_U 是否有效（失效则跳过该账号，避免白跑）
    try:
        me = client._post("/weapi/nuser/account/get", {})
        if me.get("code") != 200:
            log(f"⚠ 账号{idx + 1} Cookie 校验失败：code={me.get('code')}，跳过")
            return {"id": str(idx), "nickname": nickname, "success": 0, "listened": 0}
        nickname = (me.get("profile") or {}).get("nickname", nickname)
        log(f"✅ 账号{idx + 1}（{nickname}）Cookie 有效")
    except Exception as exc:  # noqa: BLE001
        log(f"账号{idx + 1} Cookie 校验异常：{exc}，跳过")
        return {"id": str(idx), "nickname": nickname, "success": 0, "listened": 0}

    # 每个账号独立获取播放地址（VIP / 版权状态可能不同）
    my_playable = {}
    if mode == "real":
        batch = songs[:200]
        my_playable = client.get_playable_urls(batch)
        log(f"账号{idx + 1}（{nickname}）可播放歌曲：{len(my_playable)} 首")
        if not my_playable:
            log(f"⚠ 账号{idx + 1} 拿不到播放地址（可能被海外 IP 风控），回退仅上报模式")
            mode = "report"
    my_songs = list(my_playable.keys()) if mode == "real" and my_playable else list(songs)

    target = min(count, max(0, quota_left))
    if target <= 0:
        log(f"账号{idx + 1}（{nickname}）配额已满，跳过")
        return {"id": str(idx), "nickname": nickname, "success": 0, "listened": 0}

    success = 0
    listened = 0
    for i in range(target):
        song_id = random.choice(my_songs)
        name = infos.get(song_id, {}).get("name", str(song_id))
        duration = min(infos.get(song_id, {}).get("duration", 240), max_duration)

        played = 0
        if mode == "real":
            url = my_playable.get(song_id)
            # 真人不会每次都听到最后一秒：在 0~min(30,duration/6) 秒内随机缩短
            short = random.randint(0, min(30, max(1, duration // 6)))
            target_seconds = max(60, duration - short)
            played = client.stream_song(url, target_seconds, speed)
            if played <= 0:
                log(f"[{nickname}] 第 {i + 1} 首《{name}》拉流失败，跳过")
                time.sleep(2)
                continue
            listened += played
            log(f"[{nickname}] ({i + 1}/{target}) 听完《{name}》 {played}s，4 段上报中…")
        else:
            played = random.randint(60, min(300, max(duration, 60)))
            log(f"[{nickname}] ({i + 1}/{target}) 模拟播放《{name}》 {played}s")

        if client.scrobble(song_id, duration, played):
            success += 1
        else:
            log(f"[{nickname}] 第 {i + 1} 次上报失败（song_id={song_id}）")

        if i < target - 1:
            # 真人节奏：8% 概率出现一次"走神"长停顿；其余在 interval±60% 抖动
            if random.random() < 0.08:
                delay = random.uniform(interval * 3, interval * 6)
            else:
                delay = random.uniform(interval * 0.6, interval * 1.6)
            time.sleep(max(1.0, delay))

    return {"id": str(idx), "nickname": nickname, "success": success, "listened": listened}


# ---------------- 主流程 ----------------
def main() -> int:
    parser = argparse.ArgumentParser(description="网易云刷听歌记录")
    parser.add_argument("--dry-run", action="store_true", help="试运行：不联网、不写文件")
    args = parser.parse_args()

    # 多账号：NETEASE_COOKIES 每行一个 MUSIC_U（优先）；兼容旧 NETEASE_COOKIE 单账号
    cookies_env = os.getenv("NETEASE_COOKIES", "").strip()
    cookies = [c.strip() for c in cookies_env.replace("\r", "\n").split("\n") if c.strip()]
    single = os.getenv("NETEASE_COOKIE", "").strip()
    if not cookies and single:
        cookies = [single]

    mode = os.getenv("NETEASE_MODE", "real").strip().lower() or "real"
    speed = float(os.getenv("NETEASE_SPEED", "1.0") or "1.0")
    max_duration = int(os.getenv("NETEASE_MAX_DURATION", "600") or "600")
    interval = float(os.getenv("NETEASE_INTERVAL", "5") or "5")
    playlist = os.getenv("NETEASE_PLAYLIST", "").strip() or HOT_PLAYLIST
    song_ids_env = os.getenv("NETEASE_SONG_IDS", "").strip()
    default_count = 20 if mode == "real" else 100
    count = int(os.getenv("NETEASE_COUNT", str(default_count)) or default_count)
    goal_total = int(os.getenv("NETEASE_GOAL", "800") or "800")
    account_switch_gap = float(os.getenv("NETEASE_ACCOUNT_GAP", "45") or "45")

    if mode not in ("real", "report"):
        log(f"未知模式 {mode}，已回退为 real")
        mode = "real"
    count = max(1, min(count, 500))
    speed = max(0.5, min(speed, 16.0))

    n_accounts = len(cookies)
    if args.dry_run:
        per = min(count, 400)
        est = n_accounts * per * (240 / speed if mode == "real" else interval)
        log(f"[试运行] 模式 = {mode}，账号数 = {n_accounts}，每账号每批 ≤ {per} 首")
        log(f"[试运行] 总目标 {goal_total} 次（分摊），本批预计耗时 {est / 60:.1f} 分钟")
        log("[试运行] 未联网、未写入 data.json。请在 GitHub Secrets 配置后正式运行。")
        return 0

    log(f"模式 = {mode}（{'真实拉流听歌' if mode == 'real' else '仅上报'}），"
        f"账号数 = {n_accounts}，每账号每批 {count} 首，间隔 {interval}s，倍速 {speed}x")

    if not cookies:
        log("错误：未配置 NETEASE_COOKIES / NETEASE_COOKIE")
        return 1

    # ---- 获取歌曲列表（用第一个账号的会话拉信息即可，歌曲元数据与账号无关）----
    probe = NeteaseClient()
    probe.set_cookie(cookies[0])
    if song_ids_env:
        base_songs = [int(s) for s in song_ids_env.split(",") if s.strip().isdigit()]
        log(f"使用自定义歌曲列表：{len(base_songs)} 首")
    else:
        base_songs = probe.get_playlist_song_ids(playlist)
    if not base_songs:
        base_songs = DEFAULT_SONGS
        log(f"使用内置兜底歌曲列表：{len(base_songs)} 首")

    infos = {}
    if mode == "real":
        infos = probe.get_song_infos(base_songs[:200])

    # ---- 计算每个账号的剩余配额（goal 总量分摊，已刷的从 data.json 读）----
    data = _migrate(load_data())
    per_quota = goal_total // max(1, n_accounts)
    log(f"总目标 {goal_total} 次分摊到 {n_accounts} 个账号：每账号 ≤ {per_quota} 次")

    results = []
    for idx, music_u in enumerate(cookies):
        # 已刷额度
        acc = next(
            (a for a in data["accounts"] if str(a.get("id")) == str(idx)), None
        )
        already = int(acc.get("totalPlays", 0)) if acc else 0
        quota_left = max(0, per_quota - already)
        log(f"── 账号 {idx + 1}/{n_accounts}：已刷 {already}，剩余配额 {quota_left}")

        if idx > 0:
            gap = random.uniform(account_switch_gap * 0.5, account_switch_gap * 1.5)
            log(f"切换账号，休息 {gap:.0f}s（像真人换设备/换人）")
            time.sleep(gap)

        results.append(
            _brush_one_account(
                idx, music_u, base_songs, infos, mode, speed, max_duration,
                interval, quota_left, count,
            )
        )

    data = save_data(results)
    total_success = sum(r["success"] for r in results)
    total_listened = sum(r["listened"] for r in results)
    hours = total_listened / 3600
    acc_summary = "，".join(
        f"{r['nickname']} +{r['success']}" for r in results if r["success"]
    )
    log(f"完成：本批成功 {total_success}（{acc_summary}）"
        + (f"，真实听歌 {hours:.2f} 小时" if total_listened else "")
        + f"，累计总播放量 {data['totalPlays']}/{goal_total}")
    return 0 if total_success > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
