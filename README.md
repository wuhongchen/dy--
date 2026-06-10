# 🎵 DyD 智能下载分析系统

> 抖音视频下载 + Whisper 语音识别 + AI 深度分析，一站式创作辅助工具

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110-green)
![License](https://img.shields.io/badge/License-MIT-green)

> ⚠️ **法律声明**：本软件仅供学习交流和研究用途，严禁用于商业目的或侵犯他人权益。使用前请仔细阅读[免责声明](#-免责声明)。

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

## ⚙️ 配置

### 配置文件

首次运行会自动生成配置文件。你也可以手动复制示例配置：

```bash
# 复制配置模板
cp config.example.json config.json
cp llm_config.example.json llm_config.json
```

### LLM 配置

编辑 `llm_config.json` 或在 Web 界面中配置：

```json
{
  "llm_api_base": "https://api.openai.com/v1",
  "llm_api_key": "your-api-key",
  "llm_model": "gpt-4o"
}
```

支持任何 OpenAI 兼容的 API 接口（OpenAI、DeepSeek、Qwen 等）。

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
├── prompts/                  # LLM 提示词模板
│   ├── analyze_video.txt     # 单视频分析
│   ├── analyze_anchor.txt    # 主播蒸馏分析
│   ├── extract_knowledge.txt # 知识点提取
│   └── analyze_visual.txt    # 画面分析
├── dyd_analysis.py           # AI 分析引擎
├── dyd_download.py           # 下载器核心（命令行版）
├── dyd_gui.py                # PyQt5 GUI（备用）
├── config.example.json       # 下载配置模板
├── llm_config.example.json   # LLM 配置模板
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

**本软件仅供学习交流和研究用途，使用前请仔细阅读以下条款：**

### 1. 用途限制
- 本软件仅限用于**个人学习、研究、数据分析**等合法用途
- **严禁**用于任何商业目的、大规模爬取、侵犯他人权益等违法行为
- 用户不得利用本软件从事违反《中华人民共和国网络安全法》《中华人民共和国著作权法》等法律法规的行为

### 2. 用户责任
- 用户应自行判断使用本软件的合法性，并承担相应的法律责任
- 用户应遵守抖音平台的服务条款和社区规范
- 用户下载的内容版权归原作者/平台所有，请尊重知识产权

### 3. 技术说明
- 本软件通过浏览器自动化技术获取公开数据，**不破解、不绕过**任何技术保护措施
- 本软件**不存储、不传输、不分享**任何用户数据，所有数据仅保存在用户本地
- 本软件**不获取**用户的账号密码、Cookie 等敏感信息

### 4. 责任限制
- 本软件按"现状"提供，不提供任何形式的明示或暗示担保
- 开发者不对因使用本软件而产生的任何直接、间接、偶然、特殊损害承担责任
- 因用户违规使用导致的任何法律后果，由用户自行承担

### 5. 合规建议
- 下载前请确认已获得内容创作者的授权或属于合理使用范围
- 请勿批量下载他人作品用于二次创作或商业用途
- 如需商业使用，请联系原作者获取授权

**使用本软件即表示您已阅读并同意上述条款。**

---

## 💬 联系方式

如有问题或建议，欢迎扫码联系：

<p align="center">
  <img src="联系我.png" alt="联系我" width="200">
</p>

<p align="center">
  <img src="请我抽包华子.jpg" alt="请我抽包华子" width="200">
</p>

---

## 📄 License

MIT License - 详见 [LICENSE](LICENSE) 文件。
