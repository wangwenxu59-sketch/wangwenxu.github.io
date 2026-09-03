# 📄 共享文件打印站

一个**零依赖、纯静态**的共享文件打印网站，专为 GitHub + GitHub Pages 设计。
把文件丢进 `files/` 文件夹，推送后自动上线，访客在线预览、一键打印。

## ✨ 功能

- 📁 文件列表自动生成（GitHub Actions 扫描 `files/` 文件夹）
- 🔍 实时搜索文件名
- 👁 在线预览：PDF / 图片 / 文本
- 🖨 一键打印：PDF 直接调起打印，图片/文本自动排版打印
- ⬇ 一键下载
- 📱 手机、电脑自适应

## 🚀 部署到 GitHub（3 步）

1. **上传代码**：把本项目所有文件推送到一个 GitHub 仓库（如 `print-share`）
   ```bash
   git init
   git add .
   git commit -m "init: 共享文件打印站"
   git branch -M main
   git remote add origin https://github.com/<你的用户名>/print-share.git
   git push -u origin main
   ```

2. **开启 GitHub Pages**：仓库页面 → `Settings` → `Pages`
   - Source 选择 **Deploy from a branch**
   - 分支选 `main`，目录选 `/ (root)`，保存

3. **完成**：访问 `https://<你的用户名>.github.io/print-share/` 即可 🎉

## 📂 日常使用

- **共享文件**：把文件放进 `files/` 文件夹 → 推送 → 网站自动更新
  （也可以直接在 GitHub 网页上点 `Add file → Upload files` 上传到 `files/`）
- **修改站名**：编辑 `index.html` 顶部的 `CONFIG`（siteName / subtitle）

## 💻 本地预览

```bash
python generate_manifest.py    # 生成文件清单
python -m http.server 8000     # 启动本地服务
# 浏览器打开 http://localhost:8000
```

> 注意：直接双击 index.html 打开无法加载文件清单，需通过本地服务访问。

## 📁 目录结构

```
print-share-site/
├── index.html                    # 网站主页面（全部逻辑都在这里）
├── files.json                    # 文件清单（自动生成，无需手改）
├── generate_manifest.py          # 清单生成脚本
├── files/                        # ← 共享文件放这里
│   └── 使用说明.txt
└── .github/workflows/
    └── update-manifest.yml       # 推送时自动更新清单
```

## 💡 建议

- 需要保格式打印的文件（合同、表单、图纸）推荐转成 **PDF** 上传
- 大文件建议控制在 50MB 以内（GitHub 单文件限制 100MB）
