#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网易云刷听歌记录脚本（配合 GitHub Actions 使用）

支持两种登录方式（优先级从高到低）：
1. NETEASE_COOKIE      —— 直接填网页版 Cookie 中的 MUSIC_U 值（推荐，最稳定）
2. NETEASE_PHONE + NETEASE_PASSWORD —— 手机号 + 明文密码（脚本会自动 MD5）

两种刷记录模式（NETEASE_MODE）：
- real   （默认）真实听歌：获取歌曲 128kbps 音频流，按真实播放速度边听边计时，
         听完后再上报播放记录。服务器端有真实拉流流量，记录更可信。
- report 仅上报：不做真实拉流，直接按模拟时长上报播放记录，速度快。

其他可选环境变量：
- NETEASE_COUNTRY_CODE  国家区号，默认 86
- NETEASE_COUNT         每次运行刷的播放次数
                        real 模式默认 20（1 首约 4 分钟），report 模式默认 100
- NETEASE_PLAYLIST      歌单 ID，从该歌单取歌；默认取云音乐热歌榜(3778678)
- NETEASE_SONG_IDS      自定义歌曲 ID 列表（英文逗号分隔），优先级高于歌单
- NETEASE_INTERVAL      每首歌之间的平均间隔秒数，默认 5
- NETEASE_SPEED         real 模式听歌倍速，默认 1.0（真实速度）；设为 2 则快进一倍拉流
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
        """批量获取 128kbps 播放地址，返回 {id: url}（VIP 歌曲无 url 会被过滤）"""
        urls = {}
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
        except Exception as exc:  # noqa: BLE001
            log(f"获取播放地址失败：{exc}")
        return urls

    # ---- 真实听歌 ----
    def stream_song(self, url: str, duration_s: int, speed: float = 1.0) -> int:
        """
        以接近真人听歌节奏拉取音频流（边听边计时）：
        - 前 15% 稍微慢一点（前奏找人声）
        - 中段 70% 匀速推进
        - 尾段 15% 稍慢（落下尾奏/操作手机）
        返回实际"听"的秒数；异常中断时返回已听秒数。
        """
        start = time.time()
        target = max(30, int(duration_s))
        got = 0
        try:
            with self.session.get(
                url, stream=True, timeout=(10, 30),
                headers={"Range": "bytes=0-"},
            ) as resp:
                if resp.status_code not in (200, 206):
                    return 0
                for chunk in resp.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    got += len(chunk)
                    # 已拉取数据对应的音频时长（128kbps ≈ 16KB/s）
                    audio_time = got / BYTES_PER_SEC
                    if audio_time >= target:
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


def save_data(success: int, listened_seconds: int = 0) -> dict:
    """把本次成功次数写入 docs/data.json（同一天累加）"""
    data = load_data()
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    data["totalPlays"] = int(data.get("totalPlays", 0)) + success
    data["totalListenedSeconds"] = (
        int(data.get("totalListenedSeconds", 0)) + listened_seconds
    )
    records = data.get("records", [])
    today_rec = next((r for r in records if r.get("date") == today), None)
    if today_rec:
        today_rec["plays"] = int(today_rec.get("plays", 0)) + success
        today_rec["seconds"] = int(today_rec.get("seconds", 0)) + listened_seconds
    else:
        records.append({"date": today, "plays": success, "seconds": listened_seconds})
    # 只保留最近 90 天
    records = sorted(records, key=lambda r: r["date"])[-90:]
    data["records"] = records
    data["updatedAt"] = datetime.now(TZ).isoformat(timespec="seconds")
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


# ---------------- 主流程 ----------------
def main() -> int:
    parser = argparse.ArgumentParser(description="网易云刷听歌记录")
    parser.add_argument("--dry-run", action="store_true", help="试运行：不联网、不写文件")
    args = parser.parse_args()

    cookie = os.getenv("NETEASE_COOKIE", "").strip()
    phone = os.getenv("NETEASE_PHONE", "").strip()
    password = os.getenv("NETEASE_PASSWORD", "").strip()
    country_code = os.getenv("NETEASE_COUNTRY_CODE", "86").strip() or "86"
    mode = os.getenv("NETEASE_MODE", "real").strip().lower() or "real"
    speed = float(os.getenv("NETEASE_SPEED", "1.0") or "1.0")
    max_duration = int(os.getenv("NETEASE_MAX_DURATION", "600") or "600")
    interval = float(os.getenv("NETEASE_INTERVAL", "5") or "5")
    playlist = os.getenv("NETEASE_PLAYLIST", "").strip() or HOT_PLAYLIST
    song_ids_env = os.getenv("NETEASE_SONG_IDS", "").strip()
    default_count = 20 if mode == "real" else 100
    count = int(os.getenv("NETEASE_COUNT", str(default_count)) or default_count)

    if mode not in ("real", "report"):
        log(f"未知模式 {mode}，已回退为 real")
        mode = "real"
    count = max(1, min(count, 500))
    speed = max(0.5, min(speed, 16.0))

    if args.dry_run:
        est = count * (240 / speed if mode == "real" else interval)
        log(f"[试运行] 模式 = {mode}（{'真实拉流听歌' if mode == 'real' else '仅上报'}）")
        log(f"[试运行] 目标 {count} 首，预计耗时 {est / 60:.1f} 分钟（speed={speed}x）")
        log("[试运行] 未联网、未写入 data.json。请在 GitHub Secrets 配置后正式运行。")
        return 0

    log(f"模式 = {mode}（{'真实拉流听歌' if mode == 'real' else '仅上报'}），"
        f"目标 {count} 首，间隔 {interval}s，倍速 {speed}x")

    if not cookie and not (phone and password):
        log("错误：未配置 NETEASE_COOKIE，也未配置 NETEASE_PHONE + NETEASE_PASSWORD")
        return 1

    client = NeteaseClient()
    if cookie:
        client.set_cookie(cookie)
    else:
        if not client.login_by_password(phone, password, country_code):
            return 1

    # 校验 MUSIC_U 是否有效（失效直接退出，避免后续全部白跑）
    try:
        me = client._post("/weapi/nuser/account/get", {})
        if me.get("code") != 200:
            log(f"⚠ Cookie 校验失败：code={me.get('code')}，请重新填 MUSIC_U")
            return 1
        nickname = (me.get("profile") or {}).get("nickname", "?")
        log(f"✅ Cookie 有效，登录用户：{nickname}")
    except Exception as exc:  # noqa: BLE001
        log(f"Cookie 校验异常：{exc}")

    # ---- 获取歌曲列表 ----
    if song_ids_env:
        base_songs = [int(s) for s in song_ids_env.split(",") if s.strip().isdigit()]
        log(f"使用自定义歌曲列表：{len(base_songs)} 首")
    else:
        base_songs = client.get_playlist_song_ids(playlist)
    if not base_songs:
        base_songs = DEFAULT_SONGS
        log(f"使用内置兜底歌曲列表：{len(base_songs)} 首")

    # 真实听歌模式：预取歌曲信息和可播地址，筛出能听的免费歌曲
    infos, playable = {}, {}
    if mode == "real":
        batch = base_songs[:200]
        infos = client.get_song_infos(batch)
        playable = client.get_playable_urls(batch)
        log(f"可播放歌曲（非 VIP / 有版权）：{len(playable)} 首")
        if not playable:
            log("没有任何可播放地址（Cookie 可能失效），回退到仅上报模式")
            mode = "report"

    songs = list(playable.keys()) if mode == "real" and playable else list(base_songs)
    if not songs:
        log("错误：可用歌曲列表为空")
        return 1

    success = 0
    listened_total = 0
    for i in range(count):
        song_id = random.choice(songs)
        name = infos.get(song_id, {}).get("name", str(song_id))
        duration = min(infos.get(song_id, {}).get("duration", 240), max_duration)

        played = 0
        if mode == "real":
            url = playable.get(song_id)
            # 真人不会每次都听到最后一秒：在 0~min(30,duration/6) 秒内随机缩短
            short = random.randint(0, min(30, max(1, duration // 6)))
            target_seconds = max(60, duration - short)
            played = client.stream_song(url, target_seconds, speed)
            if played <= 0:
                log(f"第 {i + 1} 首《{name}》拉流失败，跳过")
                time.sleep(2)
                continue
            listened_total += played
            log(f"({i + 1}/{count}) 听完《{name}》 {played}s，4 段上报中…")
        else:
            played = random.randint(60, min(300, max(duration, 60)))
            log(f"({i + 1}/{count}) 模拟播放《{name}》 {played}s")

        if client.scrobble(song_id, duration, played):
            success += 1
        else:
            log(f"第 {i + 1} 次上报失败（song_id={song_id}）")

        if i < count - 1:
            # 真人节奏：8% 概率出现一次"走神"长停顿；其余在 interval±60% 抖动
            if random.random() < 0.08:
                delay = random.uniform(interval * 3, interval * 6)
            else:
                delay = random.uniform(interval * 0.6, interval * 1.6)
            time.sleep(max(1.0, delay))

    data = save_data(success, listened_total)
    hours = listened_total / 3600
    log(f"完成：成功 {success}/{count}"
        + (f"，本次真实听歌 {hours:.2f} 小时" if listened_total else "")
        + f"，累计总播放量 {data['totalPlays']}")
    return 0 if success > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
