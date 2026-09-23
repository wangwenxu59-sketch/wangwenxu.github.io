# 📋 招投标信息聚合站

一个**零依赖、纯静态**的招投标信息聚合网站，部署在 GitHub Pages 上。
数据源：**中国政府采购网（ccgp.gov.cn）公开搜索接口**，覆盖全国各省级采购公告。
自动抓取招标公告、中标公示，支持搜索、筛选、详情查看。

## ✨ 功能

- 📦 **三大模块**：招标公告、中标公示、政策法规
- 🔍 **多维筛选**：关键词搜索、地区、类型、行业、时间范围
- 📊 **智能排序**：最新发布、截止时间最近
- 📄 **详情弹窗**：完整字段展示 + 跳转原文 + 打印 + 复制深链接
- 🤖 **自动更新**：每 3 小时抓取最新公告（GitHub Actions）
- 📱 **响应式**：电脑、手机自适应
- 🚀 **零依赖**：纯 HTML/CSS/JS + JSON 数据 + Python 标准库抓取器

## 🚀 部署到 GitHub（3 步）

1. **创建仓库**：在 GitHub 新建一个仓库（如 `bid-info`）
2. **上传代码**：把本项目所有文件推送到 main 分支
   ```bash
   git init && git add . && git commit -m "init: 招投标聚合站"
   git branch -M main
   git remote add origin https://github.com/<你的用户名>/bid-info.git
   git push -u origin main
   ```
3. **开启 GitHub Pages**：仓库 `Settings → Pages` → Source 选 `Deploy from a branch` → 分支 `main`、目录 `/ (root)` → 保存

> ⚠️ 仓库根目录必须保留 `.nojekyll` 文件，否则 Pages 构建会因 Jekyll 处理 JSON 文件失败。

## 📂 目录结构

```
bid-info-site/
├── index.html                  # 主页面（全部 UI + 逻辑）
├── .nojekyll                   # 禁用 Jekyll（必须）
├── data/
│   ├── bids.json               # 招标公告（自动抓取）
│   ├── winners.json            # 中标公示（自动抓取）
│   └── policies.json           # 政策法规（手动维护）
├── scripts/
│   └── scrape.py               # 中国政府采购网实时抓取器（标准库实现）
└── .github/workflows/
    └── update-data.yml         # 每 3 小时自动抓取
```

## ✏️ 如何更新数据

### 方式 A：自动抓取（已启用 ✅）

`.github/workflows/update-data.yml` 已配置为**每 3 小时**自动运行：
- 抓取中国政府采购网最新公告（默认 8 页 ≈ 160 条/次）
- 自动分类：招标类 → `bids.json`，中标/成交类 → `winners.json`
- 按 URL 去重合并，每文件最多保留 800 条（滚动更新）
- 自动提交并触发 Pages 重新部署

也可在 GitHub → Actions → Scrape bid data → Run workflow 手动触发（可指定页数）。

抓取脚本使用 Python 标准库（urllib + re），无需安装依赖。目标站改版时调整 `scripts/scrape.py` 中的正则即可。

### 方式 B：关键词定向抓取

```bash
python3 scripts/scrape.py --kw 信息化,医疗,教育 --pages 5
```

### 方式 C：手动编辑

直接编辑 `data/*.json`，按现有结构添加条目，提交推送即可生效。

**JSON 字段说明**（自动抓取生成的记录）：

```jsonc
{
  "id": "CG-20260923-0001",      // 唯一 ID
  "title": "项目名称",
  "type": "招标公告",             // 招标公告 / 中标公示 / 更正公告
  "category": "工程",            // 工程 / 货物 / 服务 / 政府采购
  "region": "浙江",              // 省级行政区
  "buyer": "采购人单位",
  "agency": "代理机构",
  "publishDate": "2026-09-23",
  "url": "http://www.ccgp.gov.cn/cggg/...",   // 原文链接
  "source": "中国政府采购网",
  "summary": "项目简介…",
  "tags": ["信息化", "医疗"]
}
```

> 政策法规（`policies.json`）为手动维护的静态参考内容，没有 deadline 字段。

## 💻 本地预览

```bash
python3 -m http.server 8000
# 浏览器打开 http://localhost:8000
```

> 注意：直接双击 `index.html` 打开无法加载 JSON（浏览器 file:// 协议受限），需通过本地服务访问。

## 🎨 个性化

- **站名**：编辑 `index.html` 顶部 `CONFIG.siteName`
- **主题色**：修改 CSS `:root` 里的 `--primary`、`--accent`
- **Logo**：改 `<link rel="icon" href="data:image/svg+xml...">` 里的 emoji

## ⚠️ 免责声明

本站数据来源于公开网络，仅供学习参考。
**最终招标结果以官方发布为准**，本站不承担任何因数据误差产生的责任。
