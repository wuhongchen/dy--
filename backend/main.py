"""
DyD 智能下载分析系统 - FastAPI 后端
"""
import os
import sys
import json
import time
import uuid
import threading
import traceback
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional, List

# 路径设置
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from backend.services.database import (
    init_db, get_downloads, get_download_by_aweme_id, save_download, delete_download,
    get_transcript, save_transcript, get_all_transcripts,
    save_analysis, get_analysis_history,
)
from backend.services.transcript import SpeechRecognizer
from dyd_analysis import AnalyzerEngine, load_prompt, save_prompt, reset_prompt

# ============================================================
# App
# ============================================================
app = FastAPI(title="DyD 智能下载分析系统")

# 静态文件
webui_dir = os.path.join(BASE_DIR, 'webui')
app.mount("/webui", StaticFiles(directory=webui_dir), name="webui")

# 数据目录
DATA_DIR = os.path.join(BASE_DIR, 'data')
TRANSCRIPTS_DIR = os.path.join(DATA_DIR, 'transcripts')
REPORTS_DIR = os.path.join(DATA_DIR, 'reports')
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

# 任务存储
_tasks = {}
_tasks_lock = threading.Lock()

# ============================================================
# 配置
# ============================================================
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')

def load_config():
    default = {
        'saveDir': os.path.join(os.environ.get('USERPROFILE', ''), 'Downloads', 'DyD'),
        'downloadCover': True,
        'downloadInfo': True,
        'retryCount': 3,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            for k, v in default.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
        except:
            pass
    return default

def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

LLM_CONFIG_FILE = os.path.join(BASE_DIR, 'llm_config.json')

def load_llm_config():
    default = {
        'llm_provider': 'openai', 'llm_api_base': '',
        'llm_api_key': '', 'llm_model': '',
        'llm_temperature': 0.7, 'llm_max_tokens': 4096,
        'whisper_model': 'base', 'whisper_device': 'cpu',
        'whisper_compute': 'int8', 'whisper_language': 'zh',
    }
    if os.path.exists(LLM_CONFIG_FILE):
        try:
            with open(LLM_CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            default.update(saved)
        except:
            pass
    return default

def save_llm_config(cfg):
    with open(LLM_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ============================================================
# Chrome 管理
# ============================================================
CHROME_PORT = 9222

def is_chrome_running():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', CHROME_PORT))
    sock.close()
    return result == 0

def find_chrome_exe():
    candidates = [
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None

def start_chrome():
    if is_chrome_running():
        return True
    chrome_exe = find_chrome_exe()
    if not chrome_exe:
        return False
    import subprocess
    user_data = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'DyD_Chrome')
    os.makedirs(user_data, exist_ok=True)
    subprocess.Popen([
        chrome_exe, f'--remote-debugging-port={CHROME_PORT}',
        f'--user-data-dir={user_data}', '--no-first-run', 'https://www.douyin.com'
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        if is_chrome_running():
            return True
    return False


# ============================================================
# 下载引擎
# ============================================================
from dyd_download import extract_video as _extract_video

class DownloadEngine:
    def __init__(self):
        self.pw = None
        self.browser = None
        self.page = None
        self.context = None

    def connect(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(f"http://localhost:{CHROME_PORT}")
        self.context = self.browser.contexts[0]
        self.page = self.context.pages[0]

    def disconnect(self):
        try:
            if self.pw:
                self.pw.stop()
        except:
            pass

    def check_login(self):
        try:
            return self.page.evaluate("""() => {
                const t = document.body.innerText;
                return t.includes('通知') && t.includes('私信');
            }""")
        except:
            return False

    def navigate(self, url, wait=5):
        try:
            self.page.goto(url, timeout=30000, wait_until='domcontentloaded')
        except:
            pass
        time.sleep(wait)

    def fetch_api(self, url):
        try:
            return self.page.evaluate(f"""async () => {{
                try {{
                    const res = await fetch('{url}', {{credentials: 'include'}});
                    const t = await res.text();
                    try {{ return JSON.parse(t); }} catch(e) {{ return {{error: 'json', raw: t.substring(0,300)}}; }}
                }} catch(e) {{ return {{error: e.message}}; }}
            }}""")
        except Exception as e:
            return {'error': str(e)}

    def fetch_bytes(self, url):
        import requests as req
        cookies = {}
        for c in self.context.cookies('https://www.douyin.com'):
            cookies[c['name']] = c['value']
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.douyin.com/',
        }
        try:
            resp = req.get(url, headers=headers, cookies=cookies, timeout=60)
            if resp.status_code == 200 and len(resp.content) > 10000:
                return resp.content
        except:
            pass
        return None

    def fetch_bytes_multi(self, urls):
        for url in urls:
            result = self.fetch_bytes(url)
            if result:
                return result
        return None


# ============================================================
# 下载任务
# ============================================================
def run_download_task(task_id, mode, url, save_dir):
    """后台下载任务"""
    engine = DownloadEngine()
    try:
        with _tasks_lock:
            _tasks[task_id]['status'] = 'running'
            _tasks[task_id]['step'] = '连接Chrome...'

        if not is_chrome_running():
            _tasks[task_id]['status'] = 'failed'
            _tasks[task_id]['step'] = 'Chrome未运行，请先启动Chrome'
            return

        engine.connect()
        _tasks[task_id]['step'] = '已连接Chrome'

        # 获取视频列表
        videos = []
        if mode == 'video':
            # 智能解析URL
            re_mod = __import__('re')
            vid_match = re_mod.search(r'video/(\d+)', url)
            if vid_match:
                vid = vid_match.group(1)
            else:
                # 检查是否有 v.douyin.com 短链接
                short_match = re_mod.search(r'https?://v\.douyin\.com/[^\s]+', url)
                if short_match:
                    _tasks[task_id]['step'] = '解析短链接...'
                    engine.navigate(short_match.group(), wait=6)
                    final_url = engine.page.url
                    vid_match = re_mod.search(r'video/(\d+)', final_url)
                    if vid_match:
                        vid = vid_match.group(1)
                    else:
                        _tasks[task_id]['status'] = 'failed'
                        _tasks[task_id]['step'] = f'短链接跳转后未找到视频: {final_url[:80]}'
                        return
                else:
                    _tasks[task_id]['status'] = 'failed'
                    _tasks[task_id]['step'] = f'无法识别视频，请粘贴完整链接或包含 video/数字 的链接'
                    return
            _tasks[task_id]['step'] = f'导航到视频页面...'
            engine.navigate(f'https://www.douyin.com/video/{vid}', wait=5)
            _tasks[task_id]['step'] = f'获取视频 {vid}...'
            result = engine.fetch_api(
                f'https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={vid}&aid=6383&cookie_enabled=true')
            if result and result.get('aweme_detail'):
                v = _extract_video(result['aweme_detail'])
                v['aweme_id'] = vid
                videos.append(v)

        elif mode == 'user':
            m = __import__('re').search(r'/user/([^\s?]+)', url)
            sec_user_id = m.group(1) if m else ''
            max_cursor = "0"
            page_num = 0
            collected = {}
            engine.navigate(url, wait=6)
            _tasks[task_id]['step'] = '采集作品列表...'

            while True:
                page_num += 1
                api_url = (f'https://www.douyin.com/aweme/v1/web/aweme/post/?device_platform=webapp&aid=6383'
                    f'&channel=channel_pc_web&sec_user_id={sec_user_id}&max_cursor={max_cursor}'
                    f'&locate_query=false&publish_video_strategy_type=2&pc_client_type=1'
                    f'&version_code=170400&version_name=17.4.0&cookie_enabled=true'
                    f'&screen_width=1920&screen_height=1080&browser_language=zh-CN'
                    f'&browser_platform=Win32&browser_name=Chrome&browser_version=126.0.0.0'
                    f'&browser_online=true&platform=PC')
                result = engine.fetch_api(api_url)
                if not result or result.get('error') or not result.get('aweme_list'):
                    break
                for item in result['aweme_list']:
                    aid = str(item.get('aweme_id', ''))
                    if aid and aid not in collected:
                        collected[aid] = _extract_video(item)
                has_more = result.get('has_more', False)
                max_cursor = str(result.get('max_cursor', ''))
                _tasks[task_id]['step'] = f'第{page_num}页: 总计{len(collected)}个'
                _tasks[task_id]['progress'] = min(50, page_num * 10)
                if not has_more or not max_cursor:
                    break
                time.sleep(0.5)
            videos = list(collected.values())

        elif mode in ('liked', 'collection'):
            sec_user_id = 'self'
            engine.navigate('https://www.douyin.com/user/self', wait=6)
            max_cursor = "0"
            collected = {}
            label = '喜欢' if mode == 'liked' else '收藏'
            _tasks[task_id]['step'] = f'采集{label}列表...'

            if mode == 'collection':
                # 导航到收藏页面
                engine.navigate('https://www.douyin.com/user/self?showTab=favorite_collection', wait=8)

                # 点击"视频"子tab
                engine.page.evaluate("""() => {
                    const tabs = document.querySelectorAll('span');
                    for (const t of tabs) {
                        if (t.textContent.trim() === '视频') { t.click(); return; }
                    }
                }""")
                time.sleep(5)

                _tasks[task_id]['step'] = '抓取收藏视频...'

                # 精确抓取：排除推荐区域（kxi8Yacg），只取收藏区域
                collect_ids = engine.page.evaluate("""() => {
                    const links = document.querySelectorAll('a[href*="/video/"]');
                    const ids = [];
                    for (const a of links) {
                        const m = a.href.match(/video\\/(\\d+)/);
                        if (!m) continue;
                        // 向上检查祖先是否包含推荐区域的 class
                        let p = a.parentElement;
                        let isRecommends = false;
                        for (let i = 0; i < 8 && p && p.tagName !== 'BODY'; i++) {
                            if (p.className && p.className.includes('kxi8Yacg')) {
                                isRecommends = true;
                                break;
                            }
                            p = p.parentElement;
                        }
                        if (!isRecommends) {
                            ids.push(m[1]);
                        }
                    }
                    return [...new Set(ids)];
                }""") or []
                _tasks[task_id]['step'] = f'收藏视频: {len(collect_ids)}个'

                for vid in collect_ids:
                    result = engine.fetch_api(
                        f'https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={vid}&aid=6383&cookie_enabled=true')
                    if result and result.get('aweme_detail'):
                        collected[vid] = _extract_video(result['aweme_detail'])
                    time.sleep(0.3)
            else:
                # 喜欢用 API
                while True:
                    api_url = (f'https://www.douyin.com/aweme/v1/web/aweme/favorite/?device_platform=webapp&aid=6383'
                        f'&channel=channel_pc_web&sec_user_id={sec_user_id}&max_cursor={max_cursor}'
                        f'&locate_query=false&count=20&pc_client_type=1&version_code=170400'
                        f'&version_name=17.4.0&cookie_enabled=true&screen_width=1920&screen_height=1080'
                        f'&browser_language=zh-CN&browser_platform=Win32&browser_name=Chrome'
                        f'&browser_version=126.0.0.0&browser_online=true&platform=PC')
                    result = engine.fetch_api(api_url)
                    if not result or result.get('error') or not result.get('aweme_list'):
                        break
                    for item in result['aweme_list']:
                        aid = str(item.get('aweme_id', ''))
                        if aid and aid not in collected:
                            collected[aid] = _extract_video(item)
                    has_more = result.get('has_more', False)
                    max_cursor = str(result.get('max_cursor', ''))
                    if not has_more or not max_cursor:
                        break
                    time.sleep(0.5)

            videos = list(collected.values())

        # 限制最多下载100个
        if len(videos) > 100:
            _tasks[task_id]['step'] = f'共{len(videos)}个视频，限制最多下载100个'
            videos = videos[:100]

        # 下载视频
        _tasks[task_id]['total'] = len(videos)
        ok = skip = fail = 0
        dl_file = os.path.join(save_dir, '_downloaded.json')
        downloaded = {}
        if os.path.exists(dl_file):
            with open(dl_file, 'r', encoding='utf-8') as f:
                downloaded = json.load(f)

        for idx, video in enumerate(videos):
            vid = video['aweme_id']
            _tasks[task_id]['progress'] = 50 + int(50 * idx / max(len(videos), 1))
            _tasks[task_id]['step'] = f'下载 {idx+1}/{len(videos)}: {video.get("desc", "")[:30]}'

            if vid in downloaded:
                skip += 1
                continue

            title = video.get('desc', vid)[:80]
            author = video.get('author', 'unknown')
            author_dir = os.path.join(save_dir, _clean_filename(author))
            os.makedirs(author_dir, exist_ok=True)
            save_path = os.path.join(author_dir, f"{_clean_filename(title)}.mp4")

            if os.path.exists(save_path):
                skip += 1
                continue

            video_urls = video.get('video_urls', [])
            if not video_urls:
                video_url = video.get('video_url', '')
                if video_url:
                    video_urls = [video_url]

            body = engine.fetch_bytes_multi(video_urls)
            if body and len(body) > 10000:
                with open(save_path, 'wb') as f:
                    f.write(body)
                downloaded[vid] = os.path.basename(save_path)
                with open(dl_file, 'w', encoding='utf-8') as f:
                    json.dump(downloaded, f, ensure_ascii=False, indent=2)
                # 保存到数据库
                save_download({
                    'aweme_id': vid, 'title': title, 'author': author,
                    'digg_count': video.get('digg_count', 0),
                    'comment_count': video.get('comment_count', 0),
                    'share_count': video.get('share_count', 0),
                    'create_time': video.get('create_time', 0),
                    'video_path': save_path,
                })
                ok += 1
            else:
                fail += 1
            time.sleep(0.3)

        with _tasks_lock:
            _tasks[task_id]['status'] = 'completed'
            _tasks[task_id]['progress'] = 100
            _tasks[task_id]['step'] = f'完成! 成功:{ok} 跳过:{skip} 失败:{fail}'
            _tasks[task_id]['result'] = {'ok': ok, 'skip': skip, 'fail': fail}

    except Exception as e:
        with _tasks_lock:
            _tasks[task_id]['status'] = 'failed'
            _tasks[task_id]['step'] = f'错误: {e}'
    finally:
        engine.disconnect()


def _clean_filename(name):
    import re
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', '_', name).strip()[:100]
    return name or 'untitled'


# ============================================================
# API 路由
# ============================================================

class DownloadRequest(BaseModel):
    mode: str
    url: str = ''

class TranscriptRequest(BaseModel):
    aweme_id: str

class LLMConfigRequest(BaseModel):
    llm_provider: str = 'ollama'
    llm_api_base: str = 'http://localhost:11434'
    llm_api_key: str = ''
    llm_model: str = 'qwen2.5:7b'
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    whisper_model: str = 'base'
    whisper_device: str = 'cpu'
    whisper_compute: str = 'int8'
    whisper_language: str = 'zh'

class PromptRequest(BaseModel):
    name: str
    content: str

# ---- 首页 ----
@app.get("/")
def index():
    return FileResponse(os.path.join(webui_dir, 'index.html'))



# ---- Chrome ----
@app.get("/api/chrome/status")
def chrome_status():
    return {"running": is_chrome_running()}

@app.post("/api/chrome/start")
def chrome_start():
    ok = start_chrome()
    return {"success": ok}

# ---- 下载 ----
@app.post("/api/download")
def start_download(req: DownloadRequest):
    task_id = f"dl_{uuid.uuid4().hex[:12]}"
    config = load_config()
    save_dir = config.get('saveDir', '')
    os.makedirs(save_dir, exist_ok=True)

    with _tasks_lock:
        _tasks[task_id] = {
            'task_id': task_id, 'type': 'download', 'status': 'starting',
            'progress': 0, 'step': '初始化...', 'result': None
        }

    t = threading.Thread(target=run_download_task, args=(task_id, req.mode, req.url, save_dir), daemon=True)
    t.start()
    return {"task_id": task_id}

@app.get("/api/download/status")
def download_status(task_id: str):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task

# ---- 下载历史 ----
@app.get("/api/downloads")
def list_downloads(keyword: str = ''):
    return get_downloads(keyword if keyword else None)

@app.delete("/api/downloads/{aweme_id}")
def del_download(aweme_id: str):
    delete_download(aweme_id)
    return {"ok": True}

# ---- 逐字稿 ----
@app.post("/api/transcript")
def start_transcript(req: TranscriptRequest):
    download = get_download_by_aweme_id(req.aweme_id)
    if not download or not download.get('video_path'):
        raise HTTPException(400, "视频文件不存在")
    video_path = download['video_path']
    if not os.path.exists(video_path):
        raise HTTPException(400, f"文件不存在: {video_path}")

    task_id = f"tr_{uuid.uuid4().hex[:12]}"
    llm_cfg = load_llm_config()

    with _tasks_lock:
        _tasks[task_id] = {
            'task_id': task_id, 'type': 'transcript', 'status': 'running',
            'progress': 0, 'step': '开始提取...'
        }

    def run():
        try:
            with _tasks_lock:
                _tasks[task_id]['step'] = '加载模型...'
                _tasks[task_id]['progress'] = 10
            recognizer = SpeechRecognizer(
                model_size=llm_cfg.get('whisper_model', 'base'),
                device=llm_cfg.get('whisper_device', 'cpu'),
                compute_type=llm_cfg.get('whisper_compute', 'int8'),
                language=llm_cfg.get('whisper_language', 'zh'),
            )
            with _tasks_lock:
                _tasks[task_id]['step'] = '转写中...'
                _tasks[task_id]['progress'] = 30
            result = recognizer.transcribe_video(video_path, TRANSCRIPTS_DIR,
                progress_callback=lambda msg: _tasks[task_id].__setitem__('step', msg) if task_id in _tasks else None)
            if result:
                save_transcript({
                    'aweme_id': req.aweme_id, 'text_content': result['text'],
                    'srt_path': result.get('srt_path', ''), 'duration': result.get('duration', 0),
                    'word_count': result.get('word_count', 0),
                })
                with _tasks_lock:
                    _tasks[task_id]['status'] = 'completed'
                    _tasks[task_id]['progress'] = 100
                    _tasks[task_id]['step'] = f'完成! {result["word_count"]}字'
            else:
                with _tasks_lock:
                    _tasks[task_id]['status'] = 'failed'
                    _tasks[task_id]['step'] = '提取失败'
        except Exception as e:
            with _tasks_lock:
                _tasks[task_id]['status'] = 'failed'
                _tasks[task_id]['step'] = str(e)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return {"task_id": task_id}

@app.get("/api/transcript/{aweme_id}")
def get_transcript_api(aweme_id: str):
    t = get_transcript(aweme_id)
    if not t:
        raise HTTPException(404, "逐字稿不存在")
    return t

@app.get("/api/transcripts")
def list_transcripts():
    items = get_all_transcripts()
    # 截断 text_content 避免传输过大，只返回摘要
    result = []
    for item in items:
        result.append({
            'aweme_id': item.get('aweme_id', ''),
            'title': item.get('title', ''),
            'author': item.get('author', ''),
            'word_count': item.get('word_count', 0),
            'duration': item.get('duration', 0),
            'created_at': str(item.get('created_at', '')),
        })
    return result

# ---- 任务状态 ----
@app.get("/api/task/{task_id}")
def get_task(task_id: str):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task

# ---- 主播蒸馏 ----
@app.post("/api/distill")
def start_distill(author: str, prompt: str = '', dimensions: str = '', output: str = 'both'):
    """启动主播蒸馏流水线"""
    task_id = f"ds_{uuid.uuid4().hex[:12]}"
    dim_list = [int(x) for x in dimensions.split(',') if x.strip().isdigit()] if dimensions else None

    with _tasks_lock:
        _tasks[task_id] = {
            'task_id': task_id, 'type': 'distill', 'author': author,
            'status': 'running', 'progress': 0, 'step': '初始化...'
        }

    t = threading.Thread(target=_run_distill_pipeline, args=(task_id, author, prompt, dim_list, output), daemon=True)
    t.start()
    return {"task_id": task_id, "author": author}


def _run_distill_pipeline(task_id, author, prompt, dimensions, output='both'):
    """主播蒸馏流水线"""
    def update(progress, step):
        with _tasks_lock:
            if task_id in _tasks:
                _tasks[task_id]['progress'] = progress
                _tasks[task_id]['step'] = step

    try:
        # === 步骤1: 检查数据 ===
        update(5, '检查数据...')
        downloads = get_downloads()
        author_videos = [d for d in downloads if d['author'] == author]
        if not author_videos:
            update(0, f'未找到作者「{author}」的下载记录')
            with _tasks_lock:
                _tasks[task_id]['status'] = 'failed'
            return

        # === 步骤2: 提取缺失逐字稿 ===
        recognizer = None
        missing_videos = []
        has_transcript = 0
        for d in author_videos:
            t = get_transcript(d['aweme_id'])
            if t and t.get('text_content') and len(t['text_content']) > 10:
                has_transcript += 1
            else:
                missing_videos.append(d)

        total = len(author_videos)
        update(10, f'共{total}个视频, {has_transcript}个已有逐字稿, {len(missing_videos)}个待提取')

        if missing_videos:
            llm_cfg = load_llm_config()
            recognizer = SpeechRecognizer(
                model_size=llm_cfg.get('whisper_model', 'base'),
                device=llm_cfg.get('whisper_device', 'cpu'),
                compute_type=llm_cfg.get('whisper_compute', 'int8'),
                language=llm_cfg.get('whisper_language', 'zh'),
            )
            transcripts_dir = os.path.join(TRANSCRIPTS_DIR, author)
            os.makedirs(transcripts_dir, exist_ok=True)

            for idx, d in enumerate(missing_videos):
                vid = d['aweme_id']
                video_path = d.get('video_path', '')
                progress = 10 + int(50 * idx / max(len(missing_videos), 1))
                update(progress, f'提取逐字稿 {idx+1}/{len(missing_videos)}: {d.get("title", "")[:30]}')

                if not video_path or not os.path.exists(video_path):
                    continue

                try:
                    result = recognizer.transcribe_video(video_path, transcripts_dir)
                    if result:
                        save_transcript({
                            'aweme_id': vid, 'text_content': result['text'],
                            'srt_path': result.get('srt_path', ''),
                            'duration': result.get('duration', 0),
                            'word_count': result.get('word_count', 0),
                        })
                        has_transcript += 1
                except Exception as e:
                    with _tasks_lock:
                        _tasks[task_id]['step'] = f'提取失败 {d.get("title","")[:20]}: {e}'

        update(60, f'逐字稿提取完成, 共{has_transcript}条')

        # === 步骤3: 汇总数据 ===
        update(62, '汇总元数据和逐字稿...')
        transcripts_parts = []
        for d in author_videos:
            t = get_transcript(d['aweme_id'])
            if t and t.get('text_content'):
                transcripts_parts.append(f"【{d['title']}】\n{t['text_content']}")
        full_transcripts = '\n\n'.join(transcripts_parts)

        # === 步骤4: AI 分析 ===
        update(65, 'AI 分析中...')
        llm_cfg = load_llm_config()
        engine = AnalyzerEngine(llm_cfg)

        prompt_template = prompt if prompt else None
        author_dir = os.path.join(REPORTS_DIR, author)
        os.makedirs(author_dir, exist_ok=True)
        results = {}

        # === 步骤4: AI 分析（仅报告或都要）===
        if output in ('report', 'both'):
            def analysis_progress(msg):
                import re as _re
                m = _re.search(r'(\d+)/10', msg)
                if m:
                    dim_num = int(m.group(1))
                    progress = 65 + int(30 * dim_num / 10)
                    update(progress, f'AI分析 {msg}')
                else:
                    update(65, f'AI分析: {msg}')

            results = engine.analyze_anchor(author_videos, full_transcripts, prompt_template, dimensions, progress_callback=analysis_progress)

            for name, content in results.items():
                with open(os.path.join(author_dir, f"{name}.md"), 'w', encoding='utf-8') as f:
                    f.write(content)
        else:
            update(65, '跳过分析报告（仅生成知识库）')

        # === 步骤5: 生成知识库（仅知识库或都要）===
        if output in ('knowledge', 'both'):
            update(95, '生成知识库...')

            knowledge_parts = []
            knowledge_parts.append(f"# {author} 知识库\n")
            knowledge_parts.append(f"## 基本信息\n- 视频数: {len(author_videos)}\n- 逐字稿数: {has_transcript}\n")

            likes = [v.get('digg_count', 0) for v in author_videos]
            knowledge_parts.append(f"## 数据统计\n- 总点赞: {sum(likes)}\n- 平均点赞: {int(sum(likes)/max(len(likes),1))}\n")

            if results:
                knowledge_parts.append("\n## 分析报告\n")
                for name, content in results.items():
                    knowledge_parts.append(f"### {name.split('_',1)[1]}\n{content}\n")

            knowledge_parts.append("\n## 逐字稿合集\n")
            for d in author_videos:
                t = get_transcript(d['aweme_id'])
                if t and t.get('text_content'):
                    knowledge_parts.append(f"### {d['title']}\n{t['text_content']}\n")

            knowledge_text = '\n'.join(knowledge_parts)
            with open(os.path.join(author_dir, 'knowledge_base.md'), 'w', encoding='utf-8') as f:
                f.write(knowledge_text)

            matched = []
            for d in author_videos:
                t = get_transcript(d['aweme_id'])
                matched.append({
                    'aweme_id': d['aweme_id'], 'title': d.get('title', ''),
                    'digg_count': d.get('digg_count', 0), 'comment_count': d.get('comment_count', 0),
                    'share_count': d.get('share_count', 0),
                    'transcript': t['text_content'] if t else '',
                })
            with open(os.path.join(author_dir, 'matched_data.json'), 'w', encoding='utf-8') as f:
                json.dump(matched, f, ensure_ascii=False, indent=2)
        else:
            update(95, '跳过知识库（仅生成分析报告）')

        update(100, '蒸馏完成!')
        with _tasks_lock:
            _tasks[task_id]['status'] = 'completed'

    except Exception as e:
        update(0, f'错误: {e}')
        with _tasks_lock:
            _tasks[task_id]['status'] = 'failed'


@app.get("/api/distill/{author}/status")
def distill_status(author: str):
    """查询主播分身状态"""
    with _tasks_lock:
        for tid, task in _tasks.items():
            if task.get('type') == 'distill' and task.get('author') == author:
                return task
    return {"status": "idle"}


@app.get("/api/distill/{author}/reports")
def distill_reports(author: str):
    """获取分析报告列表"""
    author_dir = os.path.join(REPORTS_DIR, author)
    if not os.path.exists(author_dir):
        return {"reports": []}
    reports = []
    for f in sorted(os.listdir(author_dir)):
        if f.endswith('.md') and f != 'knowledge_base.md':
            reports.append(f.replace('.md', ''))
    return {"reports": reports}


@app.get("/api/distill/{author}/report/{name}")
def distill_report(author: str, name: str):
    """获取单个报告内容"""
    path = os.path.join(REPORTS_DIR, author, f"{name}.md")
    if not os.path.exists(path):
        raise HTTPException(404, "报告不存在")
    with open(path, 'r', encoding='utf-8') as f:
        return {"content": f.read()}


@app.get("/api/distill/{author}/knowledge")
def distill_knowledge(author: str):
    """获取知识库"""
    path = os.path.join(REPORTS_DIR, author, 'knowledge_base.md')
    if not os.path.exists(path):
        raise HTTPException(404, "知识库不存在，请先完成分身")
    with open(path, 'r', encoding='utf-8') as f:
        return {"content": f.read()}


@app.post("/api/distill/{author}/qa")
def distill_qa(author: str, body: dict):
    """基于知识库 AI 问答"""
    question = body.get('question', '')
    if not question:
        raise HTTPException(400, "问题不能为空")

    knowledge_path = os.path.join(REPORTS_DIR, author, 'knowledge_base.md')
    if not os.path.exists(knowledge_path):
        raise HTTPException(404, "知识库不存在")

    with open(knowledge_path, 'r', encoding='utf-8') as f:
        knowledge = f.read()

    llm_cfg = load_llm_config()
    engine = AnalyzerEngine(llm_cfg)
    context = knowledge[:20000]
    prompt = f"基于以下知识库回答问题：\n\n{context}\n\n问题：{question}"
    answer = engine.call_llm(prompt, system_prompt="你是一个专业的抖音运营顾问，请用中文回答，输出 Markdown 格式。")

    return {"question": question, "answer": answer}


# ---- 下载历史筛选 ----
@app.get("/api/authors")
def list_authors():
    """获取所有作者列表"""
    downloads = get_downloads()
    authors = {}
    for d in downloads:
        a = d.get('author', '')
        if a:
            if a not in authors:
                authors[a] = 0
            authors[a] += 1
    return [{"name": a, "count": c} for a, c in sorted(authors.items(), key=lambda x: -x[1])]


# ---- 设置 ----
@app.get("/api/config/llm")
def get_llm_config():
    return load_llm_config()

@app.put("/api/config/llm")
def update_llm_config(req: LLMConfigRequest):
    save_llm_config(req.dict())
    return {"ok": True}

@app.get("/api/config/download")
def get_download_config():
    return load_config()

@app.put("/api/config/download")
def update_download_config(cfg: dict):
    save_config(cfg)
    return {"ok": True}

# ---- 提示词 ----
@app.get("/api/prompts/{name}")
def get_prompt(name: str):
    return {"content": load_prompt(name)}

@app.put("/api/prompts/{name}")
def update_prompt(name: str, req: PromptRequest):
    save_prompt(name, req.content)
    return {"ok": True}

@app.post("/api/prompts/{name}/reset")
def reset_prompt_api(name: str):
    reset_prompt(name)
    return {"ok": True, "content": load_prompt(name)}


# ============================================================
# 启动
# ============================================================
init_db()

if __name__ == '__main__':
    import uvicorn
    print("=" * 50)
    print("  DyD 智能下载分析系统")
    print("  访问: http://localhost:8080")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8080)
