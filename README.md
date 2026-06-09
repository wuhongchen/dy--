# 🎵 DyD 智能下载分析系统

> 抖音视频下载 + Whisper 语音识别 + AI 深度分析，一站式创作辅助工具

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110-green)
![License](https://img.shields.io/badge/License-Proprietary-red)

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 📥 **视频下载** | 主页作品、喜欢列表、收藏列表、单个视频一键下载 |
| 📝 **逐字稿提取** | Whisper 语音识别，自动生成 .txt + .srt 字幕文件 |
| 👤 **主播蒸馏** | 自动采集 + 提取逐字稿 + AI 10维度深度分析 + 生成知识库 |
| 🤖 **AI 问答** | 基于主播知识库的智能问答，支持内容策略、爆款分析、标题优化等 |
| 📋 **下载历史** | 完整的下载记录管理，支持搜索、筛选、快捷操作 |
| ⚙️ **灵活配置** | LLM API 配置、Whisper 模型配置、提示词自定义 |

---

## 🚀 快速开始

### 方式一：一键安装（推荐）

1. 确保电脑已安装 [Python 3.11+](https://www.python.org/downloads/)
2. 确保电脑已安装 [Google Chrome](https://www.google.com/chrome/)
3. 双击 `一键安装.bat`
4. 等待自动安装完成，浏览器将自动打开

### 方式二：手动安装

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 安装 Whisper 语音识别依赖
pip install faster-whisper

# 3. 启动服务
python -m backend.main
```

### 方式三：启动（已安装依赖）

```bash
# 双击 start.bat 或运行：
python -m backend.main
```

启动后打开浏览器访问 **http://localhost:8080**

---

## 🔑 授权说明

| 状态 | 功能限制 |
|------|---------|
| ✅ 已授权 | 全功能可用，永久有效 |
| ⏳ 试用中 | 首次启动后 24 小时内全功能可用 |
| ❌ 未授权 | 所有功能锁定 |

**授权码：`DYD-VIP-2024`**

在页面顶部输入授权码，点击"激活"即可。

---

## ⚙️ LLM 配置

首次使用需要配置 LLM（大语言模型）接口：

1. 打开 http://localhost:8080
2. 切换到「设置」Tab
3. 填写 API 配置：
   - **API 地址**：你的 OpenAI 兼容接口地址（如 `https://api.openai.com/v1`）
   - **API Key**：你的 API 密钥
   - **模型名称**：如 `gpt-4o`、`qwen-plus` 等
4. 点击保存

支持任何 OpenAI 兼容的 API 接口。

---

## 📖 使用教程

### 下载视频

1. 启动程序，点击「启动 Chrome」
2. 在 Chrome 中登录抖音账号
3. 切换到「下载管理」页面
4. 选择下载类型（主页作品/喜欢/收藏/单个视频）
5. 粘贴链接或输入 URL
6. 点击「开始下载」

### 主播蒸馏

1. 先下载主播的全部视频
2. 切换到「主播蒸馏」页面
3. 选择要分析的主播
4. 选择输出类型（分析报告/知识库/都要）
5. 点击「开始蒸馏」
6. 系统自动：提取逐字稿 → AI 分析 → 生成知识库
7. 完成后可查看报告、知识库、进行 AI 问答

### 逐字稿提取

1. 切换到「逐字稿」页面
2. 选择已下载的视频
3. 点击「开始提取」
4. 自动生成 .txt 和 .srt 文件

---

## 📁 项目结构

```
DyD下载器/
├── 一键安装.bat              # 一键安装启动
├── start.bat                 # 启动脚本
├── requirements.txt          # Python 依赖
├── backend/
│   ├── main.py               # FastAPI 服务端（主入口）
│   └── services/
│       ├── database.py       # SQLite 数据库
│       └── transcript.py     # Whisper 语音识别
├── webui/
│   └── index.html            # 前端页面
├── app/
│   ├── auth.py               # 授权验证
│   └── gen_license.py        # 授权码生成（作者用）
├── prompts/                  # LLM 提示词模板
│   ├── analyze_video.txt     # 单视频分析
│   ├── analyze_anchor.txt    # 主播蒸馏分析
│   ├── extract_knowledge.txt # 知识点提取
│   └── analyze_visual.txt    # 画面分析
├── dyd_analysis.py           # AI 分析引擎
├── dyd_download.py           # 下载器核心（命令行版）
├── dyd_gui.py                # PyQt5 GUI（备用）
├── config.json               # 下载配置
├── llm_config.json           # LLM 配置
└── data/                     # 数据存储（下载视频/逐字稿/报告）
```

---

## 🛠️ 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | FastAPI + Uvicorn + SQLite |
| 前端 | 原生 HTML/CSS/JS |
| 视频下载 | Playwright CDP + Requests |
| 语音识别 | faster-whisper (CTranslate2) |
| AI 分析 | OpenAI 兼容 API (httpx) |
| 浏览器 | Google Chrome (CDP 协议) |
| 音频处理 | ffmpeg |

---

## 📋 API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/auth/status` | 授权状态 |
| POST | `/api/auth/activate` | 激活授权码 |
| POST | `/api/download` | 开始下载 |
| GET | `/api/task/{id}` | 任务进度 |
| GET | `/api/downloads` | 下载历史 |
| POST | `/api/transcript` | 提取逐字稿 |
| GET | `/api/transcripts` | 逐字稿列表 |
| POST | `/api/distill` | 开始蒸馏 |
| GET | `/api/distill/{author}/reports` | 分析报告 |
| GET | `/api/distill/{author}/knowledge` | 知识库 |
| POST | `/api/distill/{author}/qa` | AI 问答 |
| GET/PUT | `/api/config/llm` | LLM 配置 |

---

## ❓ 常见问题

**Q: 启动后浏览器打不开？**
A: 确保安装了 Google Chrome，程序会自动检测并启动。

**Q: 逐字稿提取失败？**
A: 首次使用需要下载 Whisper 模型，运行 `pip install faster-whisper` 安装依赖。

**Q: AI 分析报错连接失败？**
A: 在设置页面检查 LLM API 配置是否正确，确保 API 地址和 Key 有效。

**Q: 下载速度慢？**
A: 程序会自动尝试多个 CDN 节点，如果都慢可能是网络问题。

---

## ⚖️ 免责声明

1. 本软件仅供学习交流和研究用途，**不得用于商业目的**。
2. 本软件不提供任何形式的担保，使用风险由用户自行承担。
3. 用户应遵守所在地区的法律法规，合理使用本软件。
4. 本软件开发者不对用户使用本软件所产生的任何后果承担责任。
5. 本软件不存储、不传播任何用户数据，所有数据均保存在用户本地。
6. 下载的内容版权归原作者所有，请尊重知识产权，仅用于个人学习和研究。

---

## 📄 License

MIT License - 详见 [LICENSE](LICENSE) 文件。
