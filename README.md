# 网易云听歌记录助手（GitHub Actions 版）

利用 GitHub Actions 每天定时自动"听歌"，为你的网易云音乐账号累积听歌量，并通过 GitHub Pages 展示播放数据看板。

> ⚠️ **风险提示**：自动刷听歌记录属于非官方行为，可能违反网易云音乐用户协议，极端情况下存在账号被风控/限制的风险，请自行评估。本项目仅供个人学习交流使用。

## 两种运行模式（NETEASE_MODE）

| 模式 | 说明 | 默认每日次数 | 预计耗时 |
|---|---|---|---|
| `real`（默认） | **真实听歌**：获取歌曲 128kbps 音频流，按真实播放速度边拉流边计时，听完一首上报一条记录。服务器端有真实播放流量，记录更可信 | 20 | 约 80 分钟 |
| `report` | 仅上报：不做真实拉流，直接按模拟时长上报，速度快 | 100 | 约 10 分钟 |

> 真实听歌模式如需提速，可配置 `NETEASE_SPEED`（如 `4` = 4 倍速拉流，80 分钟压缩到 20 分钟，拉流流量仍然真实）。
> 真实听歌模式只会播放**免费/非 VIP** 歌曲，VIP 歌曲会自动过滤。

## 项目结构

```
├── main.py                     # 核心脚本：登录 + 上报播放记录 + 写入数据
├── requirements.txt            # Python 依赖
├── .github/workflows/brush.yml # GitHub Actions 定时任务
└── docs/                       # GitHub Pages 展示目录
    ├── index.html              # 数据看板页面
    └── data.json               # 运行记录（Actions 自动提交）
```

## 部署步骤（约 5 分钟）

### 1. 创建仓库

1. 在 GitHub 新建一个仓库（建议 **Private**，避免泄露听歌偏好）；
2. 将本项目所有文件原样上传到仓库根目录。

### 2. 配置 Secrets

进入仓库 **Settings → Secrets and variables → Actions → New repository secret**，添加以下任意一组：

| Secret 名称 | 必填 | 说明 |
|---|---|---|
| `NETEASE_COOKIE` | 二选一 | 网页版 Cookie 中的 `MUSIC_U` 值（**推荐**，稳定） |
| `NETEASE_PHONE` + `NETEASE_PASSWORD` | 二选一 | 手机号 + 明文密码（脚本自动 MD5，易触发验证码） |

可选 Secret：

| Secret 名称 | 默认值 | 说明 |
|---|---|---|
| `NETEASE_COUNTRY_CODE` | `86` | 手机号国际区号 |
| `NETEASE_MODE` | `real` | `real` 真实听歌 / `report` 仅上报 |
| `NETEASE_COUNT` | real:20 / report:100 | 每天刷的播放次数 |
| `NETEASE_SPEED` | `1.0` | real 模式拉流倍速（`4` = 4 倍速，流量仍真实） |
| `NETEASE_MAX_DURATION` | `600` | real 模式单首最长听歌秒数 |
| `NETEASE_PLAYLIST` | `3778678` | 取歌的歌单 ID（默认云音乐热歌榜） |
| `NETEASE_SONG_IDS` | 无 | 自定义歌曲 ID，英文逗号分隔，优先于歌单 |
| `NETEASE_INTERVAL` | `5` | 每首歌之间的平均间隔秒数 |

**如何获取 MUSIC_U（Cookie 方式）：**

1. 电脑浏览器打开 [music.163.com](https://music.163.com) 并登录；
2. 按 `F12` 打开开发者工具 → **Application（应用）** → **Cookies** → `https://music.163.com`；
3. 找到名为 `MUSIC_U` 的条目，复制其 **Value**，填入 Secret `NETEASE_COOKIE`。

### 3. 开启 Actions

上传后进入仓库 **Actions** 标签页，若提示禁用请点击 **Enable workflows**。

工作流默认 **每天北京时间 09:30** 自动运行（GitHub 定时可能有 5~30 分钟延迟），也支持手动触发：

**Actions → Brush Listening Records → Run workflow**

### 4. 开启 Pages（数据看板）

1. 进入 **Settings → Pages**；
2. Source 选择 **Deploy from a branch**；
3. Branch 选择 `main` 分支、目录选 **`/docs`**，保存；
4. 稍等 1~2 分钟，访问 `https://<你的用户名>.github.io/<仓库名>/` 即可看到数据看板。

> 注意：若仓库为 Private，免费账户无法使用 GitHub Pages，可将仓库设为 Public（data.json 中只包含播放数字，不含账号信息），或不开 Pages、直接在 Actions 日志查看结果。

## 本地运行

```bash
pip install -r requirements.txt
python main.py --dry-run   # 试运行（不联网）

# 正式运行（本地环境变量方式）
NETEASE_COOKIE=xxx NETEASE_COUNT=50 python main.py
```

## 常见问题

- **登录返回 code 408 / -461 / 需要验证码？**
  密码登录风控较严，请改用 `NETEASE_COOKIE`（MUSIC_U）方式。
- **real 模式提示"没有任何可播放地址"后回退到仅上报？**
  多为 Cookie 失效或风控，请更新 MUSIC_U；也可能是歌单全是 VIP 歌曲，换个免费歌单 ID。
- **MUSIC_U 过期了怎么办？**
  Cookie 有效期一般数月，失效后重新登录网页版并更新 Secret 即可。
- **上报全部失败？**
  检查 Cookie 是否正确、是否被风控；可调低 `NETEASE_COUNT`、调高 `NETEASE_INTERVAL` 后重试。
- **看板没有数据？**
  确认 Actions 已成功运行一次，且 `docs/data.json` 已被自动提交。
