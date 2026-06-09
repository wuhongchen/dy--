#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
DyD 视频下载器 GUI v4
GUI 只负责控制和显示，浏览器使用 Playwright 连接的 Chrome
"""
import os
os.environ['QTWEBENGINE_DISABLE_SANDBOX'] = '1'
import sys
import re
import json
import time
import base64
import threading
import traceback
import subprocess
import webbrowser

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel,
    QLineEdit, QCheckBox, QSpinBox, QFileDialog, QProgressBar,
    QHeaderView, QMessageBox, QTextEdit, QFrame, QComboBox,
    QStackedWidget, QScrollArea
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QColor, QTextCursor

# 新增模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dyd_analysis import AnalyzerEngine, load_prompt, save_prompt, PROMPTS_DIR
from backend.services.database import (
    save_download, get_downloads, delete_download, get_download_by_aweme_id,
    save_transcript, get_transcript, get_all_transcripts,
    save_analysis, get_analysis_history,
)
from backend.services.transcript import SpeechRecognizer

# ============================================================
# 配置
# ============================================================
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
DEFAULT_CONFIG = {
    'saveDir': os.path.join(os.environ.get('USERPROFILE', ''), 'Downloads', 'DyD'),
    'downloadCover': True,
    'downloadInfo': True,
    'retryCount': 3,
}
CHROME_PORT = 9222


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
        except:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def clean_filename(name):
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', '_', name).strip()[:100]
    return name or 'untitled'


def extract_video(item):
    return {
        'aweme_id': str(item.get('aweme_id', '')),
        'desc': item.get('desc', ''),
        'author': (item.get('author') or {}).get('nickname', ''),
        'digg_count': (item.get('statistics') or {}).get('digg_count', 0),
        'comment_count': (item.get('statistics') or {}).get('comment_count', 0),
        'share_count': (item.get('statistics') or {}).get('share_count', 0),
        'create_time': item.get('create_time', 0),
        'video_url': ((item.get('video') or {}).get('play_addr') or {}).get('url_list', [''])[0],
        'video_urls': ((item.get('video') or {}).get('play_addr') or {}).get('url_list', []),
        'cover_url': ((item.get('video') or {}).get('cover') or {}).get('url_list', [''])[0],
    }


# ============================================================
# Chrome 管理
# ============================================================
def find_chrome_exe():
    """查找 Chrome 可执行文件路径"""
    candidates = [
        os.path.join(os.environ.get('PROGRAMFILES', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def is_chrome_running(port=CHROME_PORT):
    """检查 Chrome 调试端口是否已开启"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('localhost', port))
    sock.close()
    return result == 0


def start_chrome(port=CHROME_PORT):
    """启动 Chrome 并开启调试端口"""
    if is_chrome_running(port):
        return True

    chrome_exe = find_chrome_exe()
    if not chrome_exe:
        return False

    user_data = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'DyD_Chrome')
    os.makedirs(user_data, exist_ok=True)

    subprocess.Popen([
        chrome_exe,
        f'--remote-debugging-port={port}',
        f'--user-data-dir={user_data}',
        '--no-first-run',
        'https://www.douyin.com'
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 等待端口开启
    for _ in range(30):
        time.sleep(1)
        if is_chrome_running(port):
            return True
    return False


def kill_chrome(port=CHROME_PORT):
    """关闭 Chrome"""
    try:
        import urllib.request
        req = urllib.request.urlopen(f'http://localhost:{port}/json/version')
        data = json.loads(req.read())
        ws_url = data.get('webSocketDebuggerUrl', '')
        if ws_url:
            # 通过 CDP 关闭浏览器
            import websocket
            ws = websocket.create_connection(ws_url)
            ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
            ws.close()
    except:
        pass


# ============================================================
# Playwright 下载引擎
# ============================================================
class DownloadEngine:
    """使用 Playwright 连接 Chrome 执行下载"""

    def __init__(self, log_callback=None):
        self.log = log_callback or print
        self.pw = None
        self.browser = None
        self.page = None

    def connect(self):
        """连接到 Chrome"""
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(f"http://localhost:{CHROME_PORT}")
        self.context = self.browser.contexts[0]
        self.page = self.context.pages[0]
        self.log(f"已连接Chrome, 当前页面: {self.page.url}")

    def check_login(self):
        """检查是否已登录"""
        if not self.page:
            return False
        try:
            return self.page.evaluate("""
                (() => {
                    const loginBtns = document.querySelectorAll('[data-e2e="user-login"], .login-button');
                    if (loginBtns.length > 0) return false;
                    const text = document.body.innerText;
                    if (text.includes('通知') && text.includes('私信')) return true;
                    if (text.includes('投稿') && text.includes('我的')) return true;
                    return false;
                })()
            """)
        except:
            return False

    def navigate(self, url, wait=5):
        """导航到 URL"""
        if not self.page:
            return
        try:
            self.page.goto(url, timeout=30000, wait_until='domcontentloaded')
        except:
            pass
        time.sleep(wait)

    def fetch_api(self, url):
        """调用 API"""
        if not self.page:
            return None
        try:
            return self.page.evaluate(f"""
                (async () => {{
                    try {{
                        const res = await fetch('{url}', {{ credentials: 'include' }});
                        const text = await res.text();
                        try {{ return JSON.parse(text); }}
                        catch(e) {{ return {{ error: 'json', raw: text.substring(0, 300) }}; }}
                    }} catch(e) {{
                        return {{ error: e.message }};
                    }}
                }})()
            """)
        except Exception as e:
            return {'error': str(e)}

    def fetch_bytes(self, url):
        """用 requests 下载文件，返回 bytes"""
        try:
            import requests as req
            cookies = {}
            for c in self.context.cookies('https://www.douyin.com'):
                cookies[c['name']] = c['value']
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                'Referer': 'https://www.douyin.com/',
            }
            resp = req.get(url, headers=headers, cookies=cookies, timeout=60)
            if resp.status_code == 200 and len(resp.content) > 10000:
                return resp.content
        except:
            pass
        return None

    def fetch_bytes_multi(self, urls):
        """尝试多个 URL 下载，返回第一个成功的 bytes"""
        for url in urls:
            result = self.fetch_bytes(url)
            if result:
                return result
        return None

    def disconnect(self):
        """断开连接（不关闭 Chrome）"""
        try:
            if self.pw:
                self.pw.stop()
        except:
            pass
        self.pw = None
        self.browser = None
        self.page = None


# ============================================================
# 下载线程
# ============================================================
class DownloadWorker(QThread):
    progress = pyqtSignal(int, int, str)
    video_ready = pyqtSignal(dict)
    download_done = pyqtSignal(str, str)
    video_saved = pyqtSignal(dict)  # 新增：下载成功时传递视频元数据
    log = pyqtSignal(str)
    finished_all = pyqtSignal(int, int, int)
    need_login = pyqtSignal()
    chrome_started = pyqtSignal(bool)

    def __init__(self, mode, url, save_dir, config):
        super().__init__()
        self.mode = mode
        self.url = url
        self.save_dir = save_dir
        self.config = config
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        engine = DownloadEngine(log_callback=lambda msg: self.log.emit(msg))
        try:
            # 连接 Chrome
            self.log.emit("正在连接Chrome...")
            if not is_chrome_running():
                self.log.emit("Chrome未运行，正在启动...")
                ok = start_chrome()
                self.chrome_started.emit(ok)
                if not ok:
                    self.log.emit("启动Chrome失败，请手动启动Chrome后再试")
                    return

            engine.connect()

            if self.mode == 'short':
                self._resolve_short_link(engine)
            elif self.mode == 'user':
                self._download_user(engine)
            elif self.mode == 'liked':
                self._download_liked(engine)
            elif self.mode == 'collection':
                self._download_collection(engine)
            elif self.mode == 'video':
                self._download_single(engine)
        except Exception as e:
            self.log.emit(f"错误: {e}\n{traceback.format_exc()}")
        finally:
            engine.disconnect()

    def _api(self, engine, url):
        result = engine.fetch_api(url)
        if not result:
            self.log.emit("  API返回空，请检查是否已登录")
            return result
        if result.get('error'):
            self.log.emit(f"  API错误: {result.get('error')}")
            return result
        # status_code 非0表示服务端拒绝
        status = result.get('status_code')
        if status and status != 0:
            if status == 5:
                self.log.emit("  status_code=5: 未登录或cookie已过期，请在Chrome中重新登录抖音")
            elif status == 2:
                self.log.emit("  status_code=2: 参数错误，请检查链接是否正确")
            else:
                self.log.emit(f"  status_code={status}: 服务端拒绝，请确认已登录抖音")
        return result

    def _download_user(self, engine):
        if not engine.check_login():
            self.log.emit("未检测到登录状态，请先在Chrome中登录抖音")
            self.need_login.emit()
            return

        self.log.emit("已登录，开始采集...")
        self.log.emit("导航到用户主页...")
        engine.navigate(self.url, wait=6)

        m = re.search(r'/user/([^?]+)', self.url)
        sec_user_id = m.group(1) if m else ''
        self.log.emit(f"sec_user_id: {sec_user_id}")

        collected = {}
        max_cursor = "0"
        page_num = 0

        self.log.emit("采集作品列表...")

        while not self._cancel:
            page_num += 1
            api_url = (
                f'https://www.douyin.com/aweme/v1/web/aweme/post/'
                f'?device_platform=webapp&aid=6383&channel=channel_pc_web'
                f'&sec_user_id={sec_user_id}&max_cursor={max_cursor}'
                f'&locate_query=false&publish_video_strategy_type=2'
                f'&pc_client_type=1&version_code=170400&version_name=17.4.0'
                f'&cookie_enabled=true&screen_width=1920&screen_height=1080'
                f'&browser_language=zh-CN&browser_platform=Win32'
                f'&browser_name=Chrome&browser_version=126.0.0.0'
                f'&browser_online=true&platform=PC'
            )

            result = self._api(engine, api_url)

            if not result or result.get('error'):
                break

            aweme_list = result.get('aweme_list')
            if not aweme_list:
                self.log.emit(f"第{page_num}页: 无数据")
                break

            for item in aweme_list:
                aid = str(item.get('aweme_id', ''))
                if aid and aid not in collected:
                    v = extract_video(item)
                    collected[aid] = v
                    self.video_ready.emit(v)

            has_more = result.get('has_more', False)
            max_cursor = str(result.get('max_cursor', ''))
            self.log.emit(f"  第{page_num}页: +{len(aweme_list)}个, 总计: {len(collected)}")

            if not has_more or not max_cursor:
                break
            time.sleep(0.5)

        self.log.emit(f"采集完成，共 {len(collected)} 个作品")
        self._do_download(engine, list(collected.values()))

    def _download_liked(self, engine):
        if not engine.check_login():
            self.log.emit("未检测到登录状态，请先在Chrome中登录抖音")
            self.need_login.emit()
            return

        self.log.emit("已登录，开始采集喜欢列表...")

        # 获取 sec_user_id
        sec_user_id = 'self'
        if self.url:
            m = re.search(r'/user/([^\s?]+)', self.url)
            if m and m.group(1) != 'self':
                sec_user_id = m.group(1)

        engine.navigate('https://www.douyin.com/user/self', wait=6)
        self.log.emit(f"sec_user_id: {sec_user_id}")

        collected = {}
        max_cursor = "0"
        page_num = 0

        self.log.emit("采集喜欢列表...")

        while not self._cancel:
            page_num += 1
            api_url = (
                f'https://www.douyin.com/aweme/v1/web/aweme/favorite/'
                f'?device_platform=webapp&aid=6383&channel=channel_pc_web'
                f'&sec_user_id={sec_user_id}&max_cursor={max_cursor}'
                f'&locate_query=false&count=20'
                f'&pc_client_type=1&version_code=170400&version_name=17.4.0'
                f'&cookie_enabled=true&screen_width=1920&screen_height=1080'
                f'&browser_language=zh-CN&browser_platform=Win32'
                f'&browser_name=Chrome&browser_version=126.0.0.0'
                f'&browser_online=true&platform=PC'
            )

            result = self._api(engine, api_url)

            if not result or result.get('error'):
                break

            aweme_list = result.get('aweme_list')
            if not aweme_list:
                self.log.emit(f"第{page_num}页: 无数据")
                break

            for item in aweme_list:
                aid = str(item.get('aweme_id', ''))
                if aid and aid not in collected:
                    v = extract_video(item)
                    collected[aid] = v
                    self.video_ready.emit(v)

            has_more = result.get('has_more', False)
            max_cursor = str(result.get('max_cursor', ''))
            self.log.emit(f"  第{page_num}页: +{len(aweme_list)}个, 总计: {len(collected)}")

            if not has_more or not max_cursor:
                break
            time.sleep(0.5)

        self.log.emit(f"采集完成，共 {len(collected)} 个喜欢")
        self._do_download(engine, list(collected.values()))

    def _download_collection(self, engine):
        if not engine.check_login():
            self.log.emit("未检测到登录状态，请先在Chrome中登录抖音")
            self.need_login.emit()
            return

        self.log.emit("已登录，开始采集收藏列表...")

        # 导航到用户主页
        engine.navigate('https://www.douyin.com/user/self', wait=6)

        # 先记录主页作品的视频ID（后面要排除）
        self.log.emit("记录主页作品...")
        post_ids = set(engine.page.evaluate("""
            (() => {
                const links = document.querySelectorAll('a[href*="/video/"]');
                return [...new Set(Array.from(links).map(a => {
                    const m = a.href.match(/video\\/(\\d+)/);
                    return m ? m[1] : null;
                }).filter(Boolean))];
            })()
        """) or [])
        self.log.emit(f"主页作品: {len(post_ids)} 个")

        # 点击"收藏"标签
        self.log.emit("点击收藏标签...")
        engine.page.evaluate("""
            (() => {
                const tabs = document.querySelectorAll('*');
                for (const t of tabs) {
                    if (t.textContent.trim() === '收藏' && t.children.length === 0) {
                        t.click();
                        return 'clicked';
                    }
                }
                return 'not found';
            })()
        """)
        time.sleep(3)

        # 点击"视频"子标签
        self.log.emit("点击视频子标签...")
        engine.page.evaluate("""
            (() => {
                const tabs = document.querySelectorAll('*');
                for (const t of tabs) {
                    if (t.textContent.trim() === '视频' && t.children.length === 0) {
                        t.click();
                        return 'clicked';
                    }
                }
                return 'not found';
            })()
        """)
        time.sleep(5)

        # 获取当前页面所有视频ID，排除主页作品
        self.log.emit("抓取收藏视频...")
        all_ids = engine.page.evaluate("""
            (() => {
                const links = document.querySelectorAll('a[href*="/video/"]');
                return [...new Set(Array.from(links).map(a => {
                    const m = a.href.match(/video\\/(\\d+)/);
                    return m ? m[1] : null;
                }).filter(Boolean))];
            })()
        """) or []
        video_ids = [vid for vid in all_ids if vid not in post_ids]

        self.log.emit(f"收藏列表: {len(video_ids)} 个")

        # 获取每个视频的详情
        videos = []
        for idx, vid in enumerate(video_ids):
            if self._cancel: break
            self.progress.emit(idx + 1, len(video_ids), f"获取 {vid}")
            result = self._api(engine,
                f'https://www.douyin.com/aweme/v1/web/aweme/detail/'
                f'?aweme_id={vid}&aid=6383&cookie_enabled=true')
            if result and result.get('aweme_detail'):
                v = extract_video(result['aweme_detail'])
                v['aweme_id'] = vid
                videos.append(v)
                self.video_ready.emit(v)
            time.sleep(0.3)

        self.log.emit(f"采集完成，共 {len(videos)} 个收藏")
        self._do_download(engine, videos)

    def _resolve_short_link(self, engine):
        """解析短链接，自动在Chrome中打开并等待跳转"""
        self.log.emit(f"正在解析短链接: {self.url}")

        # 在 Chrome 中打开短链接
        engine.navigate(self.url, wait=5)

        # 等待页面跳转，获取最终 URL
        final_url = engine.page.url
        self.log.emit(f"跳转后URL: {final_url}")

        # 从最终 URL 提取视频或用户信息
        vid_match = re.search(r'/video/(\d+)', final_url)
        user_match = re.search(r'/user/([^\s?]+)', final_url)

        if vid_match:
            vid = vid_match.group(1)
            self.log.emit(f"识别为视频: {vid}")
            result = self._api(engine,
                f'https://www.douyin.com/aweme/v1/web/aweme/detail/'
                f'?aweme_id={vid}&aid=6383&cookie_enabled=true')
            if result and result.get('aweme_detail'):
                v = extract_video(result['aweme_detail'])
                v['aweme_id'] = vid
                self._do_download(engine, [v])
            else:
                self.log.emit(f"获取视频失败: {result}")
        elif user_match:
            self.url = f"https://www.douyin.com/user/{user_match.group(1)}"
            self._download_user(engine)
        else:
            self.log.emit(f"无法从URL中识别视频或用户: {final_url}")

        self.finished_all.emit(0, 0, 0)

    def _download_single(self, engine):
        vid_match = re.search(r'video/(\d+)', self.url)
        vid = vid_match.group(1) if vid_match else self.url
        self.log.emit(f"获取视频: {vid}")

        # 先导航到视频页面
        engine.navigate(f"https://www.douyin.com/video/{vid}", wait=5)

        result = self._api(engine,
            f'https://www.douyin.com/aweme/v1/web/aweme/detail/'
            f'?aweme_id={vid}&aid=6383&cookie_enabled=true')

        if result and result.get('aweme_detail'):
            v = extract_video(result['aweme_detail'])
            v['aweme_id'] = vid
            self._do_download(engine, [v])
        else:
            self.log.emit(f"获取失败: {result}")

    def _do_download(self, engine, videos):
        if not videos:
            self.log.emit("无内容可下载")
            self.finished_all.emit(0, 0, 0)
            return

        dl_file = os.path.join(self.save_dir, '_downloaded.json')
        downloaded = {}
        if os.path.exists(dl_file):
            try:
                with open(dl_file, 'r', encoding='utf-8') as f:
                    downloaded = json.load(f)
            except:
                pass

        ok = skip = fail = 0
        total = len(videos)

        for idx, video in enumerate(videos):
            if self._cancel:
                self.log.emit("已取消")
                break

            vid = video['aweme_id']
            self.progress.emit(idx + 1, total, f"下载 {vid}")

            if vid in downloaded:
                self.download_done.emit(vid, 'skip')
                skip += 1
                continue

            try:
                result = self._download_one(engine, video)
                if result:
                    ok += 1
                    downloaded[vid] = os.path.basename(result)
                    with open(dl_file, 'w', encoding='utf-8') as f:
                        json.dump(downloaded, f, ensure_ascii=False, indent=2)
                    self.download_done.emit(vid, 'ok')
                    # 传递视频元数据给数据库
                    video['video_path'] = result
                    self.video_saved.emit(video)
                else:
                    fail += 1
                    self.download_done.emit(vid, 'fail')
            except Exception as e:
                fail += 1
                self.log.emit(f"  失败: {e}")
                self.download_done.emit(vid, 'fail')

            time.sleep(0.3)

        self.finished_all.emit(ok, skip, fail)

    def _download_one(self, engine, video):
        title = clean_filename(video.get('desc', '') or video.get('aweme_id', ''))
        author = video.get('author', 'unknown')
        video_urls = video.get('video_urls', [])
        if not video_urls:
            video_url = video.get('video_url', '')
            if video_url:
                video_urls = [video_url]
        if not video_urls:
            self.log.emit(f"  无视频地址: {title[:30]}")
            return None

        author_dir = os.path.join(self.save_dir, clean_filename(author))
        os.makedirs(author_dir, exist_ok=True)
        save_path = os.path.join(author_dir, f"{title}.mp4")

        if os.path.exists(save_path):
            self.log.emit(f"  已存在: {title[:30]}")
            return save_path

        self.log.emit(f"  下载: {title[:30]} (尝试{len(video_urls)}个CDN)")

        body = engine.fetch_bytes_multi(video_urls)
        if not body or len(body) < 10000:
            self.log.emit(f"  所有CDN均失败: {title[:30]}")
            return None

        with open(save_path, 'wb') as f:
            f.write(body)
        self.log.emit(f"  已下载: {len(body)/1024/1024:.1f}MB - {title[:30]}")

        # 下载封面
        if self.config.get('downloadCover'):
            cover_url = video.get('cover_url', '')
            if cover_url:
                try:
                    cover_data = engine.fetch_bytes(cover_url)
                    if cover_data and len(cover_data) > 1000:
                        with open(os.path.join(author_dir, f"{title}_cover.jpg"), 'wb') as f:
                            f.write(cover_data)
                except:
                    pass

        # 保存信息
        if self.config.get('downloadInfo'):
            ts = video.get('create_time', 0)
            time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts)) if ts else '-'
            with open(os.path.join(author_dir, f"{title}_info.txt"), 'w', encoding='utf-8') as f:
                f.write('\n'.join([
                    f"标题: {title}", f"作者: {author}", f"ID: {video.get('aweme_id', '')}",
                    f"链接: https://www.douyin.com/video/{video.get('aweme_id', '')}",
                    f"点赞: {video.get('digg_count', 0)}", f"评论: {video.get('comment_count', 0)}",
                    f"分享: {video.get('share_count', 0)}", f"时间: {time_str}"
                ]))

        return save_path


# ============================================================
# 侧边栏按钮
# ============================================================
class SideButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                border: none; text-align: left; padding: 10px 20px;
                font-size: 14px; color: #ccc; background: transparent;
                border-radius: 8px; margin: 2px 8px;
            }
            QPushButton:hover { background: rgba(255,255,255,0.08); color: #fff; }
            QPushButton:checked { background: rgba(255,255,255,0.12); color: #fff; font-weight: bold; }
        """)
        self.setCheckable(True)


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.worker = None
        self.current_page_idx = 0
        self.current_analysis_mode = 'single_video'
        self.transcript_worker = None
        self.analysis_worker = None
        self.knowledge_worker = None
        self._current_videos = {}  # aweme_id -> video dict
        self.setup_ui()

    def setup_ui(self):
        self.setWindowTitle("DyD 抖音视频下载器 v4")
        self.setMinimumSize(1100, 700)
        self.resize(1200, 800)
        self.setStyleSheet("""
            QMainWindow { background: #1a1a2e; }
            QWidget { color: #e0e0e0; }
            QTableWidget {
                background: #16213e; gridline-color: #1a1a3e;
                border: none; border-radius: 8px; font-size: 13px;
            }
            QTableWidget::item { padding: 8px; border-bottom: 1px solid #1a1a3e; }
            QTableWidget::item:selected { background: #0f3460; }
            QHeaderView::section {
                background: #0f3460; color: #e0e0e0; padding: 10px 8px;
                border: none; font-weight: bold; font-size: 13px;
            }
            QProgressBar {
                border: none; border-radius: 4px; background: #16213e;
                text-align: center; color: white; height: 8px;
            }
            QProgressBar::chunk { background: #e94560; border-radius: 4px; }
            QLineEdit {
                background: #16213e; border: 1px solid #0f3460; border-radius: 6px;
                padding: 8px 12px; font-size: 13px; color: #fff;
            }
            QLineEdit:focus { border-color: #e94560; }
            QPushButton {
                background: #e94560; color: white; border: none; border-radius: 6px;
                padding: 8px 20px; font-size: 13px; font-weight: bold;
            }
            QPushButton:hover { background: #ff6b81; }
            QPushButton:disabled { background: #555; color: #999; }
            QTextEdit {
                background: #16213e; border: 1px solid #0f3460; border-radius: 6px;
                color: #aaa; font-family: Consolas; font-size: 12px; padding: 8px;
            }
            QComboBox {
                background: #16213e; border: 1px solid #0f3460; border-radius: 6px;
                padding: 8px 12px; font-size: 13px; color: #fff;
            }
            QComboBox QAbstractItemView {
                background: #16213e; color: #fff;
                selection-background-color: #0f3460;
                border: 1px solid #0f3460; outline: none;
                max-height: 200px;
            }
            QCheckBox { font-size: 13px; spacing: 8px; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QSpinBox {
                background: #16213e; border: 1px solid #0f3460; border-radius: 6px;
                padding: 6px; font-size: 13px; color: #fff;
            }
            QLabel { font-size: 13px; }
        """)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === 左侧导航栏 ===
        sidebar = QFrame()
        sidebar.setFixedWidth(200)
        sidebar.setStyleSheet("QFrame { background: #12122a; border-right: 1px solid #1a1a3e; }")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 20, 0, 20)
        side_layout.setSpacing(4)

        logo = QLabel("🎵 DyD 下载器")
        logo.setStyleSheet("font-size: 18px; font-weight: bold; color: #e94560; padding: 10px 20px;")
        side_layout.addWidget(logo)
        side_layout.addSpacing(20)

        self.btn_page_home = SideButton("🏠 首页")
        self.btn_page_home.setChecked(True)
        self.btn_page_home.clicked.connect(lambda: self.switch_page(0))
        side_layout.addWidget(self.btn_page_home)

        self.btn_page_download = SideButton("📥 下载管理")
        self.btn_page_download.clicked.connect(lambda: self.switch_page(1))
        side_layout.addWidget(self.btn_page_download)

        self.btn_page_settings = SideButton("⚙️ 设置")
        self.btn_page_settings.clicked.connect(lambda: self.switch_page(7))
        side_layout.addWidget(self.btn_page_settings)

        self.btn_page_transcript = SideButton("📝 逐字稿")
        self.btn_page_transcript.clicked.connect(lambda: self.switch_page(3))
        side_layout.addWidget(self.btn_page_transcript)

        self.btn_page_analysis = SideButton("🤖 AI 分析")
        self.btn_page_analysis.clicked.connect(lambda: self.switch_page(4))
        side_layout.addWidget(self.btn_page_analysis)

        self.btn_page_knowledge = SideButton("📚 知识点")
        self.btn_page_knowledge.clicked.connect(lambda: self.switch_page(5))
        side_layout.addWidget(self.btn_page_knowledge)

        self.btn_page_history = SideButton("📋 下载历史")
        self.btn_page_history.clicked.connect(lambda: self.switch_page(6))
        side_layout.addWidget(self.btn_page_history)

        side_layout.addStretch()

        ver = QLabel("v5.0")
        ver.setStyleSheet("color: #555; font-size: 11px; padding: 10px 20px;")
        side_layout.addWidget(ver)

        main_layout.addWidget(sidebar)

        # === 右侧内容区 ===
        self.pages = QStackedWidget()
        self.pages.setStyleSheet("QStackedWidget { background: #1a1a2e; }")
        main_layout.addWidget(self.pages, 1)

        # --- 页面0: 首页（引导页）---
        home_page = QWidget()
        hp_layout = QVBoxLayout(home_page)
        hp_layout.setContentsMargins(40, 30, 40, 30)
        hp_layout.setSpacing(20)

        title = QLabel("🎵 DyD 抖音视频下载器")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #e94560;")
        hp_layout.addWidget(title)

        tip = QLabel(
            "使用说明:\n\n"
            "1. 点击下方按钮打开 Chrome 浏览器\n"
            "2. 在 Chrome 中登录抖音账号\n"
            "3. 登录完成后，切换到「下载管理」页面\n"
            "4. 粘贴链接，选择下载类型，点击开始下载\n\n"
            "支持: 主页作品、喜欢、收藏、单个视频"
        )
        tip.setStyleSheet("font-size: 14px; color: #aaa; line-height: 1.6; padding: 10px;")
        tip.setWordWrap(True)
        hp_layout.addWidget(tip)

        # Chrome 状态
        self.chrome_status = QLabel("🔴 Chrome 未运行")
        self.chrome_status.setStyleSheet("font-size: 14px; color: #e94560; padding: 10px;")
        hp_layout.addWidget(self.chrome_status)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(15)

        self.btn_open_chrome = QPushButton("🌐 打开 Chrome 浏览器")
        self.btn_open_chrome.setFixedHeight(50)
        self.btn_open_chrome.setStyleSheet("""
            QPushButton {
                background: #e94560; color: white; border: none; border-radius: 8px;
                padding: 10px 30px; font-size: 15px; font-weight: bold;
            }
            QPushButton:hover { background: #ff6b81; }
        """)
        self.btn_open_chrome.clicked.connect(self.on_open_chrome)
        btn_row.addWidget(self.btn_open_chrome)

        self.btn_open_douyin = QPushButton("🔗 在Chrome中打开抖音")
        self.btn_open_douyin.setFixedHeight(50)
        self.btn_open_douyin.setStyleSheet("""
            QPushButton {
                background: #0f3460; color: white; border: none; border-radius: 8px;
                padding: 10px 30px; font-size: 15px; font-weight: bold;
            }
            QPushButton:hover { background: #1a4a8a; }
        """)
        self.btn_open_douyin.clicked.connect(self.on_open_douyin)
        btn_row.addWidget(self.btn_open_douyin)

        hp_layout.addLayout(btn_row)
        hp_layout.addStretch()

        self.pages.addWidget(home_page)

        # --- 页面1: 下载管理 ---
        download_page = QWidget()
        dp_layout = QVBoxLayout(download_page)
        dp_layout.setContentsMargins(20, 20, 20, 20)
        dp_layout.setSpacing(12)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)

        self.combo_type = QComboBox()
        self.combo_type.addItems(["主页作品", "我的喜欢", "我的收藏", "单个作品", "导入链接"])
        self.combo_type.setFixedWidth(130)
        self.combo_type.setFixedHeight(40)
        top_bar.addWidget(self.combo_type)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("粘贴抖音主页或视频链接...")
        self.url_input.setFixedHeight(40)
        top_bar.addWidget(self.url_input, 1)

        self.btn_start = QPushButton("📥 开始下载")
        self.btn_start.setFixedHeight(40)
        self.btn_start.setFixedWidth(130)
        self.btn_start.clicked.connect(self.start_download)
        top_bar.addWidget(self.btn_start)

        self.btn_cancel = QPushButton("❌ 取消")
        self.btn_cancel.setFixedHeight(40)
        self.btn_cancel.setFixedWidth(80)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_download)
        self.btn_cancel.setStyleSheet("QPushButton { background: #555; } QPushButton:hover { background: #e94560; }")
        top_bar.addWidget(self.btn_cancel)

        dp_layout.addLayout(top_bar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setVisible(False)
        dp_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("就绪 - 请先确保Chrome已打开并登录抖音")
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        dp_layout.addWidget(self.status_label)

        self.task_table = QTableWidget()
        self.task_table.setColumnCount(7)
        self.task_table.setHorizontalHeaderLabels(["", "作品标题", "作者", "点赞", "评论", "状态", "发布时间"])
        self.task_table.setColumnWidth(0, 40)
        self.task_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.task_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.task_table.setShowGrid(False)
        self.task_table.setAlternatingRowColors(True)
        self.task_table.setStyleSheet("QTableWidget { alternate-background-color: #16213e; } QTableWidget::item { padding: 10px 8px; }")
        dp_layout.addWidget(self.task_table, 1)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(120)
        self.log_text.setPlaceholderText("操作日志...")
        dp_layout.addWidget(self.log_text)

        self.pages.addWidget(download_page)

        # --- 页面2: 设置 ---
        settings_page = QWidget()
        sp_layout = QVBoxLayout(settings_page)
        sp_layout.setContentsMargins(40, 30, 40, 30)
        sp_layout.setSpacing(20)

        stitle = QLabel("⚙️ 下载设置")
        stitle.setStyleSheet("font-size: 20px; font-weight: bold; color: #e94560;")
        sp_layout.addWidget(stitle)

        dir_card = QFrame()
        dir_card.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
        dir_layout = QHBoxLayout(dir_card)
        dir_layout.addWidget(QLabel("保存目录:"))
        self.dir_input = QLineEdit(self.config.get('saveDir', ''))
        dir_layout.addWidget(self.dir_input, 1)
        btn_browse = QPushButton("选择")
        btn_browse.setFixedWidth(80)
        btn_browse.clicked.connect(self.on_browse_dir)
        dir_layout.addWidget(btn_browse)
        sp_layout.addWidget(dir_card)

        opt_card = QFrame()
        opt_card.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
        opt_layout = QHBoxLayout(opt_card)
        self.chk_cover = QCheckBox("下载封面图片")
        self.chk_cover.setChecked(self.config.get('downloadCover', True))
        opt_layout.addWidget(self.chk_cover)
        self.chk_info = QCheckBox("下载作品信息")
        self.chk_info.setChecked(self.config.get('downloadInfo', True))
        opt_layout.addWidget(self.chk_info)
        opt_layout.addStretch()
        retry_label = QLabel("重试次数:")
        opt_layout.addWidget(retry_label)
        self.spin_retry = QSpinBox()
        self.spin_retry.setRange(0, 10)
        self.spin_retry.setValue(self.config.get('retryCount', 3))
        self.spin_retry.setFixedWidth(60)
        opt_layout.addWidget(self.spin_retry)
        sp_layout.addWidget(opt_card)

        btn_save = QPushButton("💾 保存下载设置")
        btn_save.setFixedWidth(150)
        btn_save.setFixedHeight(40)
        btn_save.clicked.connect(self.on_save_settings)
        sp_layout.addWidget(btn_save)

        # --- LLM 配置 ---
        llm_card = QFrame()
        llm_card.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
        llm_layout = QVBoxLayout(llm_card)
        llm_layout.addWidget(QLabel("🤖 LLM 配置"))

        llm_grid = QHBoxLayout()
        llm_grid.addWidget(QLabel("Provider:"))
        self.llm_provider_combo = QComboBox()
        self.llm_provider_combo.addItems(["ollama", "openai"])
        self.llm_provider_combo.setFixedWidth(120)
        llm_grid.addWidget(self.llm_provider_combo)
        llm_grid.addWidget(QLabel("API 地址:"))
        self.llm_api_base_input = QLineEdit()
        llm_grid.addWidget(self.llm_api_base_input, 1)
        llm_grid.addWidget(QLabel("模型:"))
        self.llm_model_input = QLineEdit()
        self.llm_model_input.setFixedWidth(150)
        llm_grid.addWidget(self.llm_model_input)
        llm_layout.addLayout(llm_grid)

        llm_grid2 = QHBoxLayout()
        llm_grid2.addWidget(QLabel("API Key:"))
        self.llm_api_key_input = QLineEdit()
        self.llm_api_key_input.setEchoMode(QLineEdit.Password)
        llm_grid2.addWidget(self.llm_api_key_input, 1)
        llm_grid2.addWidget(QLabel("温度(x0.1):"))
        self.llm_temp_input = QSpinBox()
        self.llm_temp_input.setRange(0, 20)
        self.llm_temp_input.setValue(7)
        self.llm_temp_input.setFixedWidth(60)
        llm_grid2.addWidget(self.llm_temp_input)
        llm_grid2.addWidget(QLabel("最大Token:"))
        self.llm_max_tokens_input = QSpinBox()
        self.llm_max_tokens_input.setRange(256, 32768)
        self.llm_max_tokens_input.setValue(4096)
        self.llm_max_tokens_input.setSingleStep(256)
        self.llm_max_tokens_input.setFixedWidth(80)
        llm_grid2.addWidget(self.llm_max_tokens_input)
        llm_layout.addLayout(llm_grid2)
        sp_layout.addWidget(llm_card)

        # --- Whisper 配置 ---
        whisper_card = QFrame()
        whisper_card.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
        whisper_layout = QVBoxLayout(whisper_card)
        whisper_layout.addWidget(QLabel("🎤 Whisper 配置"))
        whisper_grid = QHBoxLayout()
        whisper_grid.addWidget(QLabel("模型:"))
        self.whisper_model_combo = QComboBox()
        self.whisper_model_combo.addItems(["tiny", "base", "small", "medium", "large"])
        self.whisper_model_combo.setFixedWidth(100)
        whisper_grid.addWidget(self.whisper_model_combo)
        whisper_grid.addWidget(QLabel("设备:"))
        self.whisper_device_combo = QComboBox()
        self.whisper_device_combo.addItems(["cpu", "cuda"])
        self.whisper_device_combo.setFixedWidth(80)
        whisper_grid.addWidget(self.whisper_device_combo)
        whisper_grid.addWidget(QLabel("计算类型:"))
        self.whisper_compute_combo = QComboBox()
        self.whisper_compute_combo.addItems(["int8", "float16", "float32"])
        self.whisper_compute_combo.setFixedWidth(100)
        whisper_grid.addWidget(self.whisper_compute_combo)
        whisper_grid.addWidget(QLabel("语言:"))
        self.whisper_lang_combo = QComboBox()
        self.whisper_lang_combo.addItems(["zh", "en", "auto"])
        self.whisper_lang_combo.setFixedWidth(80)
        whisper_grid.addWidget(self.whisper_lang_combo)
        whisper_grid.addStretch()
        whisper_layout.addLayout(whisper_grid)
        sp_layout.addWidget(whisper_card)

        # --- 提示词管理（精简版）---
        prompt_card = QFrame()
        prompt_card.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
        prompt_layout = QVBoxLayout(prompt_card)
        prompt_top = QHBoxLayout()
        prompt_top.addWidget(QLabel("📝 提示词管理:"))
        self.prompt_select_combo = QComboBox()
        self.prompt_select_combo.addItems(["单视频分析", "主播蒸馏", "知识点", "画面分析"])
        self.prompt_select_combo.setFixedWidth(120)
        self.prompt_select_combo.currentIndexChanged.connect(self._on_prompt_select)
        prompt_top.addWidget(self.prompt_select_combo)
        btn_save_prompts = QPushButton("💾 保存")
        btn_save_prompts.setFixedWidth(80)
        btn_save_prompts.clicked.connect(self._save_current_prompt)
        prompt_top.addWidget(btn_save_prompts)
        btn_reset_prompts = QPushButton("🔄 重置")
        btn_reset_prompts.setFixedWidth(80)
        btn_reset_prompts.clicked.connect(self._reset_current_prompt)
        prompt_top.addWidget(btn_reset_prompts)
        prompt_top.addStretch()
        prompt_layout.addLayout(prompt_top)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMaximumHeight(120)
        self.prompt_edit.setPlainText(load_prompt('analyze_video'))
        prompt_layout.addWidget(self.prompt_edit)
        sp_layout.addWidget(prompt_card)

        sp_layout.addStretch()

        self.pages.addWidget(settings_page)

        # --- 页面3: 逐字稿提取 ---
        transcript_page = QWidget()
        tp_layout = QVBoxLayout(transcript_page)
        tp_layout.setContentsMargins(20, 20, 20, 20)
        tp_layout.setSpacing(12)

        tp_top = QHBoxLayout()
        tp_top.setSpacing(10)

        self.transcript_combo = QComboBox()
        self.transcript_combo.setFixedWidth(300)
        self.transcript_combo.setFixedHeight(40)
        self.transcript_combo.setPlaceholderText("选择已下载的视频...")
        tp_top.addWidget(self.transcript_combo)

        self.btn_transcript_load = QPushButton("🔄 刷新列表")
        self.btn_transcript_load.setFixedHeight(40)
        self.btn_transcript_load.setFixedWidth(100)
        self.btn_transcript_load.clicked.connect(self.load_transcript_list)
        tp_top.addWidget(self.btn_transcript_load)

        self.btn_transcript_start = QPushButton("📝 开始提取")
        self.btn_transcript_start.setFixedHeight(40)
        self.btn_transcript_start.setFixedWidth(120)
        self.btn_transcript_start.clicked.connect(self.start_transcript)
        tp_top.addWidget(self.btn_transcript_start)

        tp_top.addStretch()
        tp_layout.addLayout(tp_top)

        self.transcript_progress = QProgressBar()
        self.transcript_progress.setFixedHeight(6)
        self.transcript_progress.setVisible(False)
        tp_layout.addWidget(self.transcript_progress)

        self.transcript_status = QLabel("就绪")
        self.transcript_status.setStyleSheet("color: #888; font-size: 12px;")
        tp_layout.addWidget(self.transcript_status)

        self.transcript_text = QTextEdit()
        self.transcript_text.setReadOnly(True)
        self.transcript_text.setPlaceholderText("逐字稿将显示在这里...")
        tp_layout.addWidget(self.transcript_text, 1)

        tp_btn_row = QHBoxLayout()
        tp_btn_row.setSpacing(10)
        self.btn_transcript_save = QPushButton("💾 保存逐字稿")
        self.btn_transcript_save.setFixedHeight(36)
        self.btn_transcript_save.setEnabled(False)
        self.btn_transcript_save.clicked.connect(self.save_transcript_file)
        tp_btn_row.addWidget(self.btn_transcript_save)
        self.btn_transcript_copy = QPushButton("📋 复制")
        self.btn_transcript_copy.setFixedHeight(36)
        self.btn_transcript_copy.setEnabled(False)
        self.btn_transcript_copy.clicked.connect(self.copy_transcript)
        tp_btn_row.addWidget(self.btn_transcript_copy)
        tp_btn_row.addStretch()
        tp_layout.addLayout(tp_btn_row)

        self.pages.addWidget(transcript_page)

        # --- 页面4: AI 分析 ---
        analysis_page = QWidget()
        ap_layout = QVBoxLayout(analysis_page)
        ap_layout.setContentsMargins(20, 20, 20, 20)
        ap_layout.setSpacing(12)

        # 模式选择
        mode_bar = QHBoxLayout()
        mode_bar.setSpacing(10)
        self.btn_mode_video = QPushButton("🎬 单视频分析")
        self.btn_mode_video.setFixedHeight(40)
        self.btn_mode_video.setCheckable(True)
        self.btn_mode_video.setChecked(True)
        self.btn_mode_video.clicked.connect(lambda: self.switch_analysis_mode('video'))
        mode_bar.addWidget(self.btn_mode_video)
        self.btn_mode_anchor = QPushButton("👤 主播蒸馏")
        self.btn_mode_anchor.setFixedHeight(40)
        self.btn_mode_anchor.setCheckable(True)
        self.btn_mode_anchor.clicked.connect(lambda: self.switch_analysis_mode('anchor'))
        mode_bar.addWidget(self.btn_mode_anchor)
        self.btn_mode_visual = QPushButton("🎨 画面分析")
        self.btn_mode_visual.setFixedHeight(40)
        self.btn_mode_visual.setCheckable(True)
        self.btn_mode_visual.clicked.connect(lambda: self.switch_analysis_mode('visual'))
        mode_bar.addWidget(self.btn_mode_visual)
        mode_bar.addStretch()
        ap_layout.addLayout(mode_bar)

        # 选择区
        sel_bar = QHBoxLayout()
        sel_bar.setSpacing(10)
        self.analysis_combo = QComboBox()
        self.analysis_combo.setFixedWidth(300)
        self.analysis_combo.setFixedHeight(40)
        sel_bar.addWidget(self.analysis_combo)
        self.btn_analysis_refresh = QPushButton("🔄 刷新")
        self.btn_analysis_refresh.setFixedHeight(40)
        self.btn_analysis_refresh.setFixedWidth(80)
        self.btn_analysis_refresh.clicked.connect(self.load_analysis_list)
        sel_bar.addWidget(self.btn_analysis_refresh)
        self.btn_analysis_start = QPushButton("🚀 开始分析")
        self.btn_analysis_start.setFixedHeight(40)
        self.btn_analysis_start.setFixedWidth(120)
        self.btn_analysis_start.clicked.connect(self.start_analysis)
        sel_bar.addWidget(self.btn_analysis_start)
        sel_bar.addStretch()
        ap_layout.addLayout(sel_bar)

        self.analysis_progress = QProgressBar()
        self.analysis_progress.setFixedHeight(6)
        self.analysis_progress.setVisible(False)
        ap_layout.addWidget(self.analysis_progress)

        self.analysis_status = QLabel("就绪")
        self.analysis_status.setStyleSheet("color: #888; font-size: 12px;")
        ap_layout.addWidget(self.analysis_status)

        # 提示词编辑区
        prompt_card = QFrame()
        prompt_card.setStyleSheet("QFrame { background: #16213e; border-radius: 8px; padding: 10px; }")
        pc_layout = QVBoxLayout(prompt_card)
        pc_layout.setContentsMargins(10, 10, 10, 10)
        pc_layout.setSpacing(6)
        pc_label = QLabel("提示词（可编辑）:")
        pc_label.setStyleSheet("color: #7ec8e3; font-size: 12px;")
        pc_layout.addWidget(pc_label)
        self.analysis_prompt = QTextEdit()
        self.analysis_prompt.setMaximumHeight(120)
        self.analysis_prompt.setPlaceholderText("提示词将自动加载...")
        pc_layout.addWidget(self.analysis_prompt)
        ap_layout.addWidget(prompt_card)

        # 结果显示区
        self.analysis_result = QTextEdit()
        self.analysis_result.setReadOnly(True)
        self.analysis_result.setPlaceholderText("分析结果将显示在这里...")
        ap_layout.addWidget(self.analysis_result, 1)

        ap_btn_row = QHBoxLayout()
        ap_btn_row.setSpacing(10)
        self.btn_analysis_save = QPushButton("💾 保存报告")
        self.btn_analysis_save.setFixedHeight(36)
        self.btn_analysis_save.setEnabled(False)
        self.btn_analysis_save.clicked.connect(self.save_analysis_report)
        ap_btn_row.addWidget(self.btn_analysis_save)
        ap_btn_row.addStretch()
        ap_layout.addLayout(ap_btn_row)

        self.pages.addWidget(analysis_page)

        # --- 页面5: 知识点提取 ---
        knowledge_page = QWidget()
        kp_layout = QVBoxLayout(knowledge_page)
        kp_layout.setContentsMargins(20, 20, 20, 20)
        kp_layout.setSpacing(12)

        kp_top = QHBoxLayout()
        kp_top.setSpacing(10)
        self.knowledge_combo = QComboBox()
        self.knowledge_combo.setFixedWidth(300)
        self.knowledge_combo.setFixedHeight(40)
        kp_top.addWidget(self.knowledge_combo)
        self.btn_knowledge_refresh = QPushButton("🔄 刷新")
        self.btn_knowledge_refresh.setFixedHeight(40)
        self.btn_knowledge_refresh.setFixedWidth(80)
        self.btn_knowledge_refresh.clicked.connect(self.load_knowledge_list)
        kp_top.addWidget(self.btn_knowledge_refresh)
        self.btn_knowledge_start = QPushButton("📚 提取知识点")
        self.btn_knowledge_start.setFixedHeight(40)
        self.btn_knowledge_start.setFixedWidth(140)
        self.btn_knowledge_start.clicked.connect(self.start_knowledge)
        kp_top.addWidget(self.btn_knowledge_start)
        kp_top.addStretch()
        kp_layout.addLayout(kp_top)

        self.knowledge_progress = QProgressBar()
        self.knowledge_progress.setFixedHeight(6)
        self.knowledge_progress.setVisible(False)
        kp_layout.addWidget(self.knowledge_progress)

        self.knowledge_status = QLabel("就绪")
        self.knowledge_status.setStyleSheet("color: #888; font-size: 12px;")
        kp_layout.addWidget(self.knowledge_status)

        # 提示词编辑
        kp_prompt_card = QFrame()
        kp_prompt_card.setStyleSheet("QFrame { background: #16213e; border-radius: 8px; padding: 10px; }")
        kpc_layout = QVBoxLayout(kp_prompt_card)
        kpc_layout.setContentsMargins(10, 10, 10, 10)
        kpc_layout.setSpacing(6)
        kpc_label = QLabel("知识点提取提示词（可编辑）:")
        kpc_label.setStyleSheet("color: #7ec8e3; font-size: 12px;")
        kpc_layout.addWidget(kpc_label)
        self.knowledge_prompt = QTextEdit()
        self.knowledge_prompt.setMaximumHeight(100)
        self.knowledge_prompt.setPlaceholderText("提示词将自动加载...")
        kpc_layout.addWidget(self.knowledge_prompt)
        kp_layout.addWidget(kp_prompt_card)

        self.knowledge_result = QTextEdit()
        self.knowledge_result.setReadOnly(True)
        self.knowledge_result.setPlaceholderText("知识点将显示在这里...")
        kp_layout.addWidget(self.knowledge_result, 1)

        kp_btn_row = QHBoxLayout()
        kp_btn_row.setSpacing(10)
        self.btn_knowledge_save = QPushButton("💾 保存")
        self.btn_knowledge_save.setFixedHeight(36)
        self.btn_knowledge_save.setEnabled(False)
        self.btn_knowledge_save.clicked.connect(self.save_knowledge)
        kp_btn_row.addWidget(self.btn_knowledge_save)
        self.btn_knowledge_copy = QPushButton("📋 复制")
        self.btn_knowledge_copy.setFixedHeight(36)
        self.btn_knowledge_copy.setEnabled(False)
        self.btn_knowledge_copy.clicked.connect(self.copy_knowledge)
        kp_btn_row.addWidget(self.btn_knowledge_copy)
        kp_btn_row.addStretch()
        kp_layout.addLayout(kp_btn_row)

        self.pages.addWidget(knowledge_page)

        # --- 页面6: 下载历史 ---
        history_page = QWidget()
        hp_layout = QVBoxLayout(history_page)
        hp_layout.setContentsMargins(20, 20, 20, 20)
        hp_layout.setSpacing(12)

        hp_search = QHBoxLayout()
        hp_search.setSpacing(10)
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("搜索标题或作者...")
        self.history_search.setFixedHeight(40)
        hp_search.addWidget(self.history_search, 1)
        self.btn_history_search = QPushButton("🔍 搜索")
        self.btn_history_search.setFixedHeight(40)
        self.btn_history_search.setFixedWidth(80)
        self.btn_history_search.clicked.connect(self.search_history)
        hp_search.addWidget(self.btn_history_search)
        self.btn_history_refresh = QPushButton("🔄 刷新")
        self.btn_history_refresh.setFixedHeight(40)
        self.btn_history_refresh.setFixedWidth(80)
        self.btn_history_refresh.clicked.connect(self.load_history)
        hp_search.addWidget(self.btn_history_refresh)
        hp_layout.addLayout(hp_search)

        self.history_table = QTableWidget()
        self.history_table.setColumnCount(9)
        self.history_table.setHorizontalHeaderLabels(
            ["", "标题", "作者", "点赞", "评论", "分享", "下载时间", "逐字稿", "操作"])
        self.history_table.setColumnWidth(0, 40)
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.setShowGrid(False)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setStyleSheet("""
            QTableWidget { alternate-background-color: #16213e; }
            QTableWidget::item { padding: 8px; }
        """)
        hp_layout.addWidget(self.history_table, 1)

        self.history_status = QLabel("共 0 条记录")
        self.history_status.setStyleSheet("color: #888; font-size: 12px;")
        hp_layout.addWidget(self.history_status)

        self.pages.addWidget(history_page)

        # --- 页面7: 设置（LLM + Whisper + 提示词）---
        settings2_page = QWidget()
        s2_scroll = QScrollArea()
        s2_scroll.setWidgetResizable(True)
        s2_content = QWidget()
        s2_layout = QVBoxLayout(s2_content)
        s2_layout.setContentsMargins(40, 30, 40, 30)
        s2_layout.setSpacing(16)

        s2_title = QLabel("⚙️ 高级设置")
        s2_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #e94560;")
        s2_layout.addWidget(s2_title)

        # LLM 配置卡片
        llm_card = QFrame()
        llm_card.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
        llm_layout = QVBoxLayout(llm_card)
        llm_layout.setSpacing(10)
        llm_label = QLabel("🤖 LLM 配置")
        llm_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #e94560;")
        llm_layout.addWidget(llm_label)

        llm_row1 = QHBoxLayout()
        llm_row1.addWidget(QLabel("Provider:"))
        self.llm_provider = QComboBox()
        self.llm_provider.addItems(["ollama", "openai"])
        self.llm_provider.setFixedWidth(150)
        llm_row1.addWidget(self.llm_provider)
        llm_row1.addWidget(QLabel("模型:"))
        self.llm_model = QLineEdit("qwen2.5:7b")
        self.llm_model.setFixedWidth(200)
        llm_row1.addWidget(self.llm_model)
        llm_row1.addStretch()
        llm_layout.addLayout(llm_row1)

        llm_row2 = QHBoxLayout()
        llm_row2.addWidget(QLabel("API 地址:"))
        self.llm_api_url = QLineEdit("http://localhost:11434")
        llm_row2.addWidget(self.llm_api_url, 1)
        llm_layout.addLayout(llm_row2)

        llm_row3 = QHBoxLayout()
        llm_row3.addWidget(QLabel("API Key:"))
        self.llm_api_key = QLineEdit()
        self.llm_api_key.setEchoMode(QLineEdit.Password)
        llm_row3.addWidget(self.llm_api_key, 1)
        self.btn_llm_key_toggle = QPushButton("👁")
        self.btn_llm_key_toggle.setFixedWidth(40)
        self.btn_llm_key_toggle.clicked.connect(self.toggle_api_key)
        llm_row3.addWidget(self.btn_llm_key_toggle)
        llm_layout.addLayout(llm_row3)

        llm_row4 = QHBoxLayout()
        llm_row4.addWidget(QLabel("温度:"))
        self.llm_temperature = QSpinBox()
        self.llm_temperature.setRange(0, 20)
        self.llm_temperature.setValue(7)
        self.llm_temperature.setFixedWidth(60)
        llm_row4.addWidget(self.llm_temperature)
        llm_row4.addWidget(QLabel("(除以10)"))
        llm_row4.addWidget(QLabel("最大Token:"))
        self.llm_max_tokens = QSpinBox()
        self.llm_max_tokens.setRange(256, 32768)
        self.llm_max_tokens.setValue(4096)
        self.llm_max_tokens.setSingleStep(256)
        self.llm_max_tokens.setFixedWidth(100)
        llm_row4.addWidget(self.llm_max_tokens)
        llm_row4.addStretch()
        llm_layout.addLayout(llm_row4)

        s2_layout.addWidget(llm_card)

        # Whisper 配置卡片
        whisper_card = QFrame()
        whisper_card.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
        w_layout = QVBoxLayout(whisper_card)
        w_layout.setSpacing(10)
        w_label = QLabel("🎤 Whisper 配置")
        w_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #e94560;")
        w_layout.addWidget(w_label)

        w_row1 = QHBoxLayout()
        w_row1.addWidget(QLabel("模型大小:"))
        self.whisper_model = QComboBox()
        self.whisper_model.addItems(["tiny", "base", "small", "medium", "large"])
        self.whisper_model.setCurrentText("base")
        self.whisper_model.setFixedWidth(120)
        w_row1.addWidget(self.whisper_model)
        w_row1.addWidget(QLabel("设备:"))
        self.whisper_device = QComboBox()
        self.whisper_device.addItems(["cpu", "cuda"])
        self.whisper_device.setFixedWidth(100)
        w_row1.addWidget(self.whisper_device)
        w_row1.addWidget(QLabel("计算类型:"))
        self.whisper_compute = QComboBox()
        self.whisper_compute.addItems(["int8", "float16", "float32"])
        self.whisper_compute.setFixedWidth(100)
        w_row1.addWidget(self.whisper_compute)
        w_row1.addWidget(QLabel("语言:"))
        self.whisper_lang = QComboBox()
        self.whisper_lang.addItems(["zh", "en", "auto"])
        self.whisper_lang.setFixedWidth(80)
        w_row1.addWidget(self.whisper_lang)
        w_row1.addStretch()
        w_layout.addLayout(w_row1)

        s2_layout.addWidget(whisper_card)

        # 提示词管理卡片
        prompt_card2 = QFrame()
        prompt_card2.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
        p_layout = QVBoxLayout(prompt_card2)
        p_layout.setSpacing(10)
        p_label = QLabel("📝 提示词管理")
        p_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #e94560;")
        p_layout.addWidget(p_label)

        self.prompt_tabs_widget = QComboBox()
        self.prompt_tabs_widget.addItems([
            "单视频分析", "主播蒸馏分析", "知识点提取", "画面分析"
        ])
        self.prompt_tabs_widget.setFixedWidth(200)
        self.prompt_tabs_widget.currentIndexChanged.connect(self.on_prompt_tab_changed)
        p_layout.addWidget(self.prompt_tabs_widget)

        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMaximumHeight(200)
        p_layout.addWidget(self.prompt_edit)

        p_btn_row = QHBoxLayout()
        p_btn_row.setSpacing(10)
        btn_prompt_save = QPushButton("💾 保存提示词")
        btn_prompt_save.setFixedHeight(36)
        btn_prompt_save.clicked.connect(self.save_current_prompt)
        p_btn_row.addWidget(btn_prompt_save)
        btn_prompt_reset = QPushButton("🔄 恢复默认")
        btn_prompt_reset.setFixedHeight(36)
        btn_prompt_reset.clicked.connect(self.reset_current_prompt)
        p_btn_row.addWidget(btn_prompt_reset)
        p_btn_row.addStretch()
        p_layout.addLayout(p_btn_row)

        s2_layout.addWidget(prompt_card2)

        # 保存所有高级设置
        btn_save_all = QPushButton("💾 保存所有设置")
        btn_save_all.setFixedWidth(200)
        btn_save_all.setFixedHeight(40)
        btn_save_all.clicked.connect(self.save_advanced_settings)
        s2_layout.addWidget(btn_save_all)

        s2_layout.addStretch()

        s2_scroll.setWidget(s2_content)
        s2_main = QVBoxLayout(settings2_page)
        s2_main.setContentsMargins(0, 0, 0, 0)
        s2_main.addWidget(s2_scroll)

        self.pages.addWidget(settings2_page)

        # 初始化提示词
        self._load_prompt_to_edit()

        # 定时检查 Chrome 状态
        self.chrome_timer = QTimer()
        self.chrome_timer.timeout.connect(self.check_chrome_status)
        self.chrome_timer.start(3000)
        self.check_chrome_status()

    def check_chrome_status(self):
        if is_chrome_running():
            self.chrome_status.setText("🟢 Chrome 已运行")
            self.chrome_status.setStyleSheet("font-size: 14px; color: #00c864; padding: 10px;")
        else:
            self.chrome_status.setText("🔴 Chrome 未运行")
            self.chrome_status.setStyleSheet("font-size: 14px; color: #e94560; padding: 10px;")

    def on_open_chrome(self):
        self.log_text.append("正在启动Chrome...")
        ok = start_chrome()
        if ok:
            self.log_text.append("Chrome已启动，调试端口已开启")
        else:
            self.log_text.append("Chrome启动失败，请手动启动Chrome")

    def on_open_douyin(self):
        if is_chrome_running():
            # 通过 CDP 打开抖音
            try:
                import urllib.request
                # 打开新标签页
                url = "https://www.douyin.com"
                urllib.request.urlopen(
                    f'http://localhost:{CHROME_PORT}/json/new?{url}'
                )
            except:
                # 备用方案：用系统默认浏览器
                webbrowser.open("https://www.douyin.com")
        else:
            self.log_text.append("Chrome未运行，请先点击「打开Chrome浏览器」")

    def switch_page(self, idx):
        self.current_page_idx = idx
        self.pages.setCurrentIndex(idx)
        self.btn_page_home.setChecked(idx == 0)
        self.btn_page_download.setChecked(idx == 1)
        self.btn_page_settings.setChecked(idx == 2)
        self.btn_page_transcript.setChecked(idx == 3)
        self.btn_page_analysis.setChecked(idx == 4)
        self.btn_page_knowledge.setChecked(idx == 5)
        self.btn_page_history.setChecked(idx == 6)
        # 切换页面时自动加载数据
        if idx == 3:
            self.load_transcript_list()
        elif idx == 4:
            self.load_analysis_list()
        elif idx == 5:
            self.load_knowledge_list()
        elif idx == 6:
            self.load_history()

    def on_browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存目录", self.dir_input.text())
        if d:
            self.dir_input.setText(d)

    def on_save_settings(self):
        self.config['saveDir'] = self.dir_input.text()
        self.config['downloadCover'] = self.chk_cover.isChecked()
        self.config['downloadInfo'] = self.chk_info.isChecked()
        self.config['retryCount'] = self.spin_retry.value()
        save_config(self.config)
        os.makedirs(self.config['saveDir'], exist_ok=True)
        # 保存 LLM 设置
        self.llm_config.update({
            'llm_provider': self.llm_provider_combo.currentText(),
            'llm_api_base': self.llm_api_base_input.text(),
            'llm_api_key': self.llm_api_key_input.text(),
            'llm_model': self.llm_model_input.text(),
            'llm_temperature': self.llm_temp_input.value() / 10.0,
            'llm_max_tokens': self.llm_max_tokens_input.value(),
            'whisper_model': self.whisper_model_combo.currentText(),
            'whisper_device': self.whisper_device_combo.currentText(),
            'whisper_compute': self.whisper_compute_combo.currentText(),
            'whisper_language': self.whisper_lang_combo.currentText(),
        })
        _save_llm_config_file(self.llm_config)
        QMessageBox.information(self, "提示", "设置已保存 ✓")

    def _on_prompt_select(self, idx):
        names = ['analyze_video', 'analyze_anchor', 'extract_knowledge', 'analyze_visual']
        if 0 <= idx < len(names):
            self.prompt_edit.setPlainText(load_prompt(names[idx]))

    def _save_current_prompt(self):
        idx = self.prompt_select_combo.currentIndex()
        names = ['analyze_video', 'analyze_anchor', 'extract_knowledge', 'analyze_visual']
        if 0 <= idx < len(names):
            save_prompt(names[idx], self.prompt_edit.toPlainText())
            QMessageBox.information(self, "提示", "提示词已保存 ✓")

    def _reset_current_prompt(self):
        idx = self.prompt_select_combo.currentIndex()
        names = ['analyze_video', 'analyze_anchor', 'extract_knowledge', 'analyze_visual']
        if 0 <= idx < len(names):
            reset_prompt(names[idx])
            self.prompt_edit.setPlainText(load_prompt(names[idx]))
            QMessageBox.information(self, "提示", "提示词已重置为默认 ✓")

    def start_download(self):
        if not is_chrome_running():
            QMessageBox.warning(self, "提示", "Chrome未运行，请先打开Chrome浏览器并登录抖音")
            return

        type_text = self.combo_type.currentText()
        url = self.url_input.text().strip()
        save_dir = self.dir_input.text() or self.config['saveDir']
        os.makedirs(save_dir, exist_ok=True)

        mode_map = {
            "主页作品": "user",
            "我的喜欢": "liked",
            "我的收藏": "collection",
            "单个作品": "video",
        }
        mode = mode_map.get(type_text, 'user')

        # "我的喜欢"和"我的收藏"不需要链接，直接从当前登录账号获取
        if mode in ('liked', 'collection'):
            self.worker = DownloadWorker(mode, '', save_dir, self.config)
            self.worker.progress.connect(self.on_progress)
            self.worker.video_ready.connect(self.on_video_ready)
            self.worker.download_done.connect(self.on_download_done)
            self.worker.log.connect(self.on_log)
            self.worker.finished_all.connect(self.on_finished)
            self.worker.need_login.connect(self.on_need_login)
            self.worker.start()
            self.btn_start.setEnabled(False)
            self.btn_cancel.setEnabled(True)
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            self.status_label.setText("正在采集...")
            self.log_text.clear()
            return

        # 从文本中提取链接
        link_match = re.search(r'https?://(?:www\.)?douyin\.com/(?:video|note)/(\d+)', url)
        user_match = re.search(r'https?://(?:www\.)?douyin\.com/user/([^\s?]+)', url)
        short_match = None if (link_match or user_match) else re.search(r'https?://v\.douyin\.com/[^\s]+', url)

        if link_match:
            url = f"https://www.douyin.com/video/{link_match.group(1)}"
            mode = 'video'
        elif user_match:
            url = f"https://www.douyin.com/user/{user_match.group(1)}"
            mode = 'user'
        elif short_match:
            self.log_text.append(f"检测到短链接，正在自动解析: {short_match.group()}")
            QMessageBox.information(self, "短链接解析",
                "程序将自动在Chrome中打开短链接并等待跳转。\n"
                "点击确定后请不要操作Chrome，等待约5秒...")
            self.worker = DownloadWorker('short', short_match.group(), save_dir, self.config)
            self.worker.progress.connect(self.on_progress)
            self.worker.video_ready.connect(self.on_video_ready)
            self.worker.download_done.connect(self.on_download_done)
            self.worker.log.connect(self.on_log)
            self.worker.finished_all.connect(self.on_finished)
            self.worker.need_login.connect(self.on_need_login)
            self.worker.start()
            self.btn_start.setEnabled(False)
            self.btn_cancel.setEnabled(True)
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)
            self.status_label.setText("正在解析短链接...")
            self.log_text.clear()
            return
        elif not url:
            QMessageBox.warning(self, "提示", "请输入链接")
            return

        # 主页作品需要用户主页链接
        if mode == 'user' and '/user/' not in url:
            QMessageBox.warning(self, "提示", "主页作品需要用户主页链接")
            return

        self.worker = DownloadWorker(mode, url, save_dir, self.config)
        self.worker.progress.connect(self.on_progress)
        self.worker.video_ready.connect(self.on_video_ready)
        self.worker.download_done.connect(self.on_download_done)
        self.worker.video_saved.connect(self.on_video_saved)
        self.worker.log.connect(self.on_log)
        self.worker.finished_all.connect(self.on_finished)
        self.worker.need_login.connect(self.on_need_login)
        self.worker.start()

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("正在采集...")
        self.log_text.clear()

    def cancel_download(self):
        if self.worker:
            self.worker.cancel()

    def on_progress(self, current, total, msg):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.status_label.setText(f"进度: {current}/{total}")

    def on_video_ready(self, video):
        row = self.task_table.rowCount()
        self.task_table.insertRow(row)
        self.task_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        title = video.get('desc', '')[:50].replace('\n', ' ')
        self.task_table.setItem(row, 1, QTableWidgetItem(title))
        self.task_table.setItem(row, 2, QTableWidgetItem(video.get('author', '')))
        self.task_table.setItem(row, 3, QTableWidgetItem(str(video.get('digg_count', 0))))
        self.task_table.setItem(row, 4, QTableWidgetItem(str(video.get('comment_count', 0))))
        self.task_table.setItem(row, 5, QTableWidgetItem("⏳ 待下载"))
        ts = video.get('create_time', 0)
        date_str = time.strftime('%Y-%m-%d', time.localtime(ts)) if ts else '-'
        self.task_table.setItem(row, 6, QTableWidgetItem(date_str))
        # 保存到临时字典
        vid = video.get('aweme_id', '')
        if vid:
            self._current_videos[vid] = video

    def on_download_done(self, vid, status):
        for row in range(self.task_table.rowCount()):
            item = self.task_table.item(row, 5)
            if item and '待下载' in item.text():
                if status == 'ok':
                    item.setText("✅ 完成")
                    item.setForeground(QColor(0, 200, 100))
                elif status == 'skip':
                    item.setText("⏭ 已存在")
                    item.setForeground(QColor(150, 150, 150))
                elif status == 'fail':
                    item.setText("❌ 失败")
                    item.setForeground(QColor(233, 69, 96))
                break

    def on_video_saved(self, video):
        """下载成功时保存到数据库"""
        save_download({
            'aweme_id': video.get('aweme_id', ''),
            'title': video.get('desc', ''),
            'author': video.get('author', ''),
            'digg_count': video.get('digg_count', 0),
            'comment_count': video.get('comment_count', 0),
            'share_count': video.get('share_count', 0),
            'create_time': video.get('create_time', 0),
            'video_path': video.get('video_path', ''),
        })

    def on_log(self, msg):
        self.log_text.append(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_finished(self, ok, skip, fail):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"✅ 完成  成功: {ok}  |  跳过: {skip}  |  失败: {fail}")
        self.log_text.append(f"\n{'='*50}\n完成! 成功: {ok} | 跳过: {skip} | 失败: {fail}\n{'='*50}")
        self.worker = None

    def on_need_login(self):
        QMessageBox.information(self, "需要登录",
            "请在Chrome中登录抖音，然后重新点击下载。")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        if hasattr(self, 'chrome_timer'):
            self.chrome_timer.stop()
        event.accept()
        event.accept()


# ============================================================
# 扩展 Tab：逐字稿 / AI分析 / 知识点 / 历史 / 设置
# ============================================================
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend.services.database import (
    get_downloads, get_download_by_aweme_id, delete_download,
    save_download, get_transcript, save_transcript, get_all_transcripts,
    get_analysis_history, save_analysis,
)
from backend.services.transcript import SpeechRecognizer
from dyd_analysis import AnalyzerEngine, load_prompt, save_prompt, reset_prompt


class TranscriptWorker(QThread):
    """逐字稿提取后台线程"""
    progress = pyqtSignal(int, int, str)
    log = pyqtSignal(str)
    finished = pyqtSignal(str)  # aweme_id
    error = pyqtSignal(str)

    def __init__(self, video_path, aweme_id, output_dir, whisper_config):
        super().__init__()
        self.video_path = video_path
        self.aweme_id = aweme_id
        self.output_dir = output_dir
        self.whisper_config = whisper_config

    def run(self):
        try:
            self.log.emit(f"开始提取逐字稿: {os.path.basename(self.video_path)}")
            recognizer = SpeechRecognizer(
                model_size=self.whisper_config.get('model_size', 'base'),
                device=self.whisper_config.get('device', 'cpu'),
                compute_type=self.whisper_config.get('compute_type', 'int8'),
                language=self.whisper_config.get('language', 'zh'),
            )
            result = recognizer.transcribe_video(
                self.video_path, self.output_dir,
                progress_callback=lambda msg: self.log.emit(msg)
            )
            if result:
                save_transcript({
                    'aweme_id': self.aweme_id,
                    'text_content': result['text'],
                    'srt_path': result.get('srt_path', ''),
                    'duration': result.get('duration', 0),
                    'word_count': result.get('word_count', 0),
                })
                self.log.emit(f"逐字稿提取完成，共 {result['word_count']} 字")
                self.finished.emit(self.aweme_id)
            else:
                self.error.emit("逐字稿提取失败")
        except Exception as e:
            self.error.emit(f"错误: {e}")


class AnalysisWorker(QThread):
    """AI 分析后台线程"""
    log = pyqtSignal(str)
    result_ready = pyqtSignal(str)  # 分析结果 Markdown
    error = pyqtSignal(str)

    def __init__(self, engine, analysis_type, **kwargs):
        super().__init__()
        self.engine = engine
        self.analysis_type = analysis_type
        self.kwargs = kwargs

    def run(self):
        try:
            if self.analysis_type == 'single_video':
                self.log.emit("开始单视频分析...")
                result = self.engine.analyze_single_video(
                    self.kwargs['video_info'],
                    self.kwargs['transcript'],
                    self.kwargs.get('prompt'),
                )
            elif self.analysis_type == 'anchor':
                self.log.emit("开始主播蒸馏分析...")
                result = self.engine.analyze_anchor(
                    self.kwargs['videos_data'],
                    self.kwargs['full_transcripts'],
                    self.kwargs.get('prompt'),
                    self.kwargs.get('dimensions'),
                )
                # 多维度结果合并
                result = '\n\n---\n\n'.join(f"## {k}\n\n{v}" for k, v in result.items())
            elif self.analysis_type == 'knowledge':
                self.log.emit("开始知识点提取...")
                result = self.engine.extract_knowledge(
                    self.kwargs['transcript'],
                    self.kwargs.get('prompt'),
                )
            elif self.analysis_type == 'visual':
                self.log.emit("开始画面分析...")
                result = self.engine.analyze_visual(
                    self.kwargs['video_info'],
                    self.kwargs.get('cover_desc', ''),
                    self.kwargs.get('frame_desc', ''),
                    self.kwargs.get('prompt'),
                )
            elif self.analysis_type == 'chat':
                self.log.emit("AI 思考中...")
                result = self.engine.chat(
                    self.kwargs['question'],
                    self.kwargs.get('context', ''),
                )
            else:
                result = "未知分析类型"

            self.result_ready.emit(result)
        except Exception as e:
            self.error.emit(f"分析错误: {e}\n{traceback.format_exc()}")


def extend_main_window(window):
    """扩展 MainWindow：加载数据 + 连接信号（按钮和页面已在 setup_ui 中创建）"""
    # --- 扩展 switch_page ---
    _all_btns = {
        0: window.btn_page_home,
        1: window.btn_page_download,
        3: window.btn_page_transcript,
        4: window.btn_page_analysis,
        5: window.btn_page_knowledge,
        6: window.btn_page_history,
        7: window.btn_page_settings,
    }
    _orig_switch = window.switch_page

    def new_switch_page(idx):
        _orig_switch(idx)
        for k, btn in _all_btns.items():
            btn.setChecked(k == idx)
        if idx == 3:
            _refresh_transcript_combo(window)
        elif idx == 4:
            _refresh_analysis_combo(window)
        elif idx == 5:
            _refresh_knowledge_combo(window)
        elif idx == 6:
            _load_history(window)
        elif idx == 7:
            _load_llm_settings(window)

    window.switch_page = new_switch_page

    # --- 初始化 ---
    window.llm_config = _load_llm_config_file()
    window.transcript_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'transcripts')
    window.reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'reports')
    os.makedirs(window.transcript_dir, exist_ok=True)
    os.makedirs(window.reports_dir, exist_ok=True)


# ---- 页面构建函数 ----

def _build_transcript_page(window):
    """Tab 3: 逐字稿提取"""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    title = QLabel("📝 逐字稿提取")
    title.setStyleSheet("font-size: 20px; font-weight: bold; color: #e94560;")
    layout.addWidget(title)

    # 选择视频区
    select_frame = QFrame()
    select_frame.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
    select_layout = QHBoxLayout(select_frame)

    select_layout.addWidget(QLabel("选择视频:"))
    window.transcript_combo = QComboBox()
    window.transcript_combo.setMinimumWidth(300)
    select_layout.addWidget(window.transcript_combo, 1)

    btn_refresh_transcripts = QPushButton("刷新列表")
    btn_refresh_transcripts.setFixedWidth(100)
    btn_refresh_transcripts.clicked.connect(lambda: _refresh_transcript_combo(window))
    select_layout.addWidget(btn_refresh_transcripts)

    btn_start_transcript = QPushButton("▶ 开始提取")
    btn_start_transcript.setFixedWidth(120)
    btn_start_transcript.clicked.connect(lambda: _start_transcript(window))
    select_layout.addWidget(btn_start_transcript)

    layout.addWidget(select_frame)

    # 进度
    window.transcript_progress = QProgressBar()
    window.transcript_progress.setFixedHeight(6)
    window.transcript_progress.setVisible(False)
    layout.addWidget(window.transcript_progress)

    # 结果显示
    result_frame = QFrame()
    result_frame.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
    result_layout = QVBoxLayout(result_frame)
    result_layout.addWidget(QLabel("逐字稿结果:"))
    window.transcript_result = QTextEdit()
    window.transcript_result.setReadOnly(True)
    window.transcript_result.setPlaceholderText("选择视频并点击「开始提取」...")
    window.transcript_result.setStyleSheet("""
        QTextEdit { background: #0d1b2a; border: 1px solid #0f3460; border-radius: 6px;
        color: #ccc; font-size: 13px; padding: 10px; line-height: 1.8; }
    """)
    result_layout.addWidget(window.transcript_result)
    layout.addWidget(result_frame, 1)

    # 底部按钮
    btn_row = QHBoxLayout()
    btn_save_transcript = QPushButton("💾 保存逐字稿")
    btn_save_transcript.clicked.connect(lambda: _save_transcript_file(window))
    btn_row.addWidget(btn_save_transcript)
    btn_row.addStretch()
    layout.addLayout(btn_row)

    window.transcript_worker = None
    return page


def _build_analysis_page(window):
    """Tab 4: AI 分析"""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    title = QLabel("🤖 AI 分析")
    title.setStyleSheet("font-size: 20px; font-weight: bold; color: #e94560;")
    layout.addWidget(title)

    # 模式切换按钮
    mode_frame = QFrame()
    mode_frame.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 10px; }")
    mode_layout = QHBoxLayout(mode_frame)
    mode_layout.setSpacing(8)

    window.btn_mode_video = QPushButton("📹 单视频分析")
    window.btn_mode_video.setCheckable(True)
    window.btn_mode_video.setChecked(True)
    window.btn_mode_video.clicked.connect(lambda: _switch_analysis_mode(window, 'single_video'))
    mode_layout.addWidget(window.btn_mode_video)

    window.btn_mode_anchor = QPushButton("👤 主播蒸馏")
    window.btn_mode_anchor.setCheckable(True)
    window.btn_mode_anchor.clicked.connect(lambda: _switch_analysis_mode(window, 'anchor'))
    mode_layout.addWidget(window.btn_mode_anchor)

    window.btn_mode_visual = QPushButton("🎬 画面分析")
    window.btn_mode_visual.setCheckable(True)
    window.btn_mode_visual.clicked.connect(lambda: _switch_analysis_mode(window, 'visual'))
    mode_layout.addWidget(window.btn_mode_visual)

    mode_layout.addStretch()
    layout.addWidget(mode_frame)

    # 主内容区
    content = QHBoxLayout()
    content.setSpacing(12)

    # 左侧：输入 + 提示词
    left = QVBoxLayout()
    left.setSpacing(8)

    # 选择视频
    select_frame = QFrame()
    select_frame.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 12px; }")
    select_layout = QVBoxLayout(select_frame)
    select_layout.addWidget(QLabel("选择分析对象:"))
    window.analysis_combo = QComboBox()
    select_layout.addWidget(window.analysis_combo)
    btn_refresh_analysis = QPushButton("刷新")
    btn_refresh_analysis.setFixedWidth(80)
    btn_refresh_analysis.clicked.connect(lambda: _refresh_analysis_combo(window))
    select_layout.addWidget(btn_refresh_analysis)
    left.addWidget(select_frame)

    # 主播蒸馏维度选择（默认隐藏）
    window.dim_frame = QFrame()
    window.dim_frame.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 12px; }")
    window.dim_frame.setVisible(False)
    dim_layout = QVBoxLayout(window.dim_frame)
    dim_layout.addWidget(QLabel("分析维度:"))
    window.dim_checks = []
    dim_names = [
        '01_整体表现概览', '02_最佳最差视频分析', '03_内容类型分布',
        '04_标题与封面策略', '05_发布节奏分析', '06_账号定位分析',
        '07_运营方法论', '08_爆款拆解', '09_可复制清单', '10_知识库'
    ]
    for name in dim_names:
        chk = QCheckBox(name.split('_', 1)[1])
        chk.setChecked(True)
        window.dim_checks.append((name, chk))
        dim_layout.addWidget(chk)
    left.addWidget(window.dim_frame)

    # 提示词编辑
    prompt_frame = QFrame()
    prompt_frame.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 12px; }")
    prompt_layout = QVBoxLayout(prompt_frame)
    prompt_layout.addWidget(QLabel("提示词 (可编辑):"))
    window.analysis_prompt = QTextEdit()
    window.analysis_prompt.setMaximumHeight(150)
    window.analysis_prompt.setStyleSheet("""
        QTextEdit { background: #0d1b2a; border: 1px solid #0f3460; border-radius: 6px;
        color: #ccc; font-size: 12px; font-family: Consolas; padding: 8px; }
    """)
    prompt_layout.addWidget(window.analysis_prompt)

    prompt_btn_row = QHBoxLayout()
    btn_save_prompt = QPushButton("💾 保存提示词")
    btn_save_prompt.setFixedWidth(120)
    btn_save_prompt.clicked.connect(lambda: _save_analysis_prompt(window))
    prompt_btn_row.addWidget(btn_save_prompt)
    btn_reset_prompt = QPushButton("🔄 重置默认")
    btn_reset_prompt.setFixedWidth(100)
    btn_reset_prompt.clicked.connect(lambda: _reset_analysis_prompt(window))
    prompt_btn_row.addWidget(btn_reset_prompt)
    prompt_btn_row.addStretch()
    prompt_layout.addLayout(prompt_btn_row)
    left.addWidget(prompt_frame)

    # 开始分析按钮
    btn_start_analysis = QPushButton("▶ 开始分析")
    btn_start_analysis.setFixedHeight(40)
    btn_start_analysis.clicked.connect(lambda: _start_analysis(window))
    left.addWidget(btn_start_analysis)

    content.addLayout(left, 1)

    # 右侧：结果
    right = QVBoxLayout()
    right_frame = QFrame()
    right_frame.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
    right_layout = QVBoxLayout(right_frame)
    right_layout.addWidget(QLabel("分析结果:"))
    window.analysis_result = QTextEdit()
    window.analysis_result.setReadOnly(True)
    window.analysis_result.setPlaceholderText("选择分析对象和模式，点击「开始分析」...")
    window.analysis_result.setStyleSheet("""
        QTextEdit { background: #0d1b2a; border: 1px solid #0f3460; border-radius: 6px;
        color: #ccc; font-size: 13px; padding: 10px; line-height: 1.6; }
    """)
    right_layout.addWidget(window.analysis_result)
    right.addWidget(right_frame, 1)

    # 保存按钮
    btn_save_analysis = QPushButton("💾 保存分析报告")
    btn_save_analysis.clicked.connect(lambda: _save_analysis_report(window))
    right.addWidget(btn_save_analysis)

    content.addLayout(right, 1)
    layout.addLayout(content, 1)

    window.analysis_worker = None
    window.current_analysis_mode = 'single_video'
    return page


def _build_knowledge_page(window):
    """Tab 5: 知识点提取"""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    title = QLabel("📚 知识点提取")
    title.setStyleSheet("font-size: 20px; font-weight: bold; color: #e94560;")
    layout.addWidget(title)

    content = QHBoxLayout()
    content.setSpacing(12)

    # 左侧
    left = QVBoxLayout()
    left.setSpacing(8)

    select_frame = QFrame()
    select_frame.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 12px; }")
    select_layout = QVBoxLayout(select_frame)
    select_layout.addWidget(QLabel("选择已提取逐字稿的视频:"))
    window.knowledge_combo = QComboBox()
    select_layout.addWidget(window.knowledge_combo)
    btn_refresh_knowledge = QPushButton("刷新")
    btn_refresh_knowledge.setFixedWidth(80)
    btn_refresh_knowledge.clicked.connect(lambda: _refresh_knowledge_combo(window))
    select_layout.addWidget(btn_refresh_knowledge)
    left.addWidget(select_frame)

    # 提示词
    prompt_frame = QFrame()
    prompt_frame.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 12px; }")
    prompt_layout = QVBoxLayout(prompt_frame)
    prompt_layout.addWidget(QLabel("提示词 (可编辑):"))
    window.knowledge_prompt = QTextEdit()
    window.knowledge_prompt.setMaximumHeight(150)
    window.knowledge_prompt.setStyleSheet("""
        QTextEdit { background: #0d1b2a; border: 1px solid #0f3460; border-radius: 6px;
        color: #ccc; font-size: 12px; font-family: Consolas; padding: 8px; }
    """)
    prompt_layout.addWidget(window.knowledge_prompt)
    left.addWidget(prompt_frame)

    btn_start_knowledge = QPushButton("▶ 开始提取知识点")
    btn_start_knowledge.setFixedHeight(40)
    btn_start_knowledge.clicked.connect(lambda: _start_knowledge(window))
    left.addWidget(btn_start_knowledge)

    content.addLayout(left, 1)

    # 右侧
    right = QVBoxLayout()
    right_frame = QFrame()
    right_frame.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
    right_layout = QVBoxLayout(right_frame)
    right_layout.addWidget(QLabel("知识点:"))
    window.knowledge_result = QTextEdit()
    window.knowledge_result.setReadOnly(True)
    window.knowledge_result.setPlaceholderText("选择视频并点击「开始提取知识点」...")
    window.knowledge_result.setStyleSheet("""
        QTextEdit { background: #0d1b2a; border: 1px solid #0f3460; border-radius: 6px;
        color: #ccc; font-size: 13px; padding: 10px; line-height: 1.6; }
    """)
    right_layout.addWidget(window.knowledge_result)
    right.addWidget(right_frame, 1)

    btn_save_knowledge = QPushButton("💾 保存知识点")
    btn_save_knowledge.clicked.connect(lambda: _save_knowledge_file(window))
    right.addWidget(btn_save_knowledge)

    content.addLayout(right, 1)
    layout.addLayout(content, 1)

    window.knowledge_worker = None
    return page


def _build_history_page(window):
    """Tab 6: 下载历史"""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(12)

    title = QLabel("📋 下载历史")
    title.setStyleSheet("font-size: 20px; font-weight: bold; color: #e94560;")
    layout.addWidget(title)

    # 搜索栏
    search_frame = QFrame()
    search_frame.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 10px; }")
    search_layout = QHBoxLayout(search_frame)
    window.history_search = QLineEdit()
    window.history_search.setPlaceholderText("搜索标题或作者...")
    window.history_search.setFixedHeight(35)
    search_layout.addWidget(window.history_search, 1)
    btn_search = QPushButton("🔍 搜索")
    btn_search.setFixedWidth(100)
    btn_search.clicked.connect(lambda: _load_history(window, window.history_search.text()))
    search_layout.addWidget(btn_search)
    btn_clear_search = QPushButton("清除")
    btn_clear_search.setFixedWidth(80)
    btn_clear_search.clicked.connect(lambda: (window.history_search.clear(), _load_history(window)))
    search_layout.addWidget(btn_clear_search)
    layout.addWidget(search_frame)

    # 历史表格
    window.history_table = QTableWidget()
    window.history_table.setColumnCount(10)
    window.history_table.setHorizontalHeaderLabels(
        ["", "标题", "作者", "点赞", "评论", "分享", "下载时间", "逐字稿", "分析", "操作"]
    )
    window.history_table.setColumnWidth(0, 40)
    window.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
    window.history_table.verticalHeader().setVisible(False)
    window.history_table.setSelectionBehavior(QTableWidget.SelectRows)
    window.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
    window.history_table.setShowGrid(False)
    window.history_table.setAlternatingRowColors(True)
    window.history_table.setStyleSheet("""
        QTableWidget { alternate-background-color: #16213e; }
        QTableWidget::item { padding: 6px; }
        QPushButton { font-size: 11px; padding: 4px 8px; }
    """)
    layout.addWidget(window.history_table, 1)

    return page


def _build_llm_settings_page(window):
    """Tab 7: 设置（LLM + Whisper + 提示词）"""
    page = QWidget()
    scroll = QStackedWidget()
    scroll_layout = QVBoxLayout(page)
    scroll_layout.setContentsMargins(20, 20, 20, 20)
    scroll_layout.setSpacing(12)

    title = QLabel("⚙️ 设置")
    title.setStyleSheet("font-size: 20px; font-weight: bold; color: #e94560;")
    scroll_layout.addWidget(title)

    # LLM 配置
    llm_card = QFrame()
    llm_card.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
    llm_layout = QVBoxLayout(llm_card)
    llm_layout.addWidget(QLabel("🤖 LLM 配置"))

    llm_grid = QHBoxLayout()
    llm_grid.addWidget(QLabel("Provider:"))
    window.llm_provider_combo = QComboBox()
    window.llm_provider_combo.addItems(["ollama", "openai"])
    window.llm_provider_combo.setFixedWidth(120)
    llm_grid.addWidget(window.llm_provider_combo)

    llm_grid.addWidget(QLabel("API 地址:"))
    window.llm_api_base_input = QLineEdit()
    llm_grid.addWidget(window.llm_api_base_input, 1)

    llm_grid.addWidget(QLabel("模型:"))
    window.llm_model_input = QLineEdit()
    window.llm_model_input.setFixedWidth(150)
    llm_grid.addWidget(window.llm_model_input)
    llm_layout.addLayout(llm_grid)

    llm_grid2 = QHBoxLayout()
    llm_grid2.addWidget(QLabel("API Key:"))
    window.llm_api_key_input = QLineEdit()
    window.llm_api_key_input.setEchoMode(QLineEdit.Password)
    llm_grid2.addWidget(window.llm_api_key_input, 1)

    llm_grid2.addWidget(QLabel("温度:"))
    window.llm_temp_input = QSpinBox()
    window.llm_temp_input.setRange(0, 20)
    window.llm_temp_input.setValue(7)
    window.llm_temp_input.setFixedWidth(60)
    llm_grid2.addWidget(QLabel("(x0.1)"))
    llm_grid2.addWidget(window.llm_temp_input)

    llm_grid2.addWidget(QLabel("最大Token:"))
    window.llm_max_tokens_input = QSpinBox()
    window.llm_max_tokens_input.setRange(256, 32768)
    window.llm_max_tokens_input.setValue(4096)
    window.llm_max_tokens_input.setSingleStep(256)
    window.llm_max_tokens_input.setFixedWidth(80)
    llm_grid2.addWidget(window.llm_max_tokens_input)
    llm_layout.addLayout(llm_grid2)

    scroll_layout.addWidget(llm_card)

    # Whisper 配置
    whisper_card = QFrame()
    whisper_card.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
    whisper_layout = QVBoxLayout(whisper_card)
    whisper_layout.addWidget(QLabel("🎤 Whisper 配置"))

    whisper_grid = QHBoxLayout()
    whisper_grid.addWidget(QLabel("模型大小:"))
    window.whisper_model_combo = QComboBox()
    window.whisper_model_combo.addItems(["tiny", "base", "small", "medium", "large"])
    window.whisper_model_combo.setFixedWidth(100)
    whisper_grid.addWidget(window.whisper_model_combo)

    whisper_grid.addWidget(QLabel("设备:"))
    window.whisper_device_combo = QComboBox()
    window.whisper_device_combo.addItems(["cpu", "cuda"])
    window.whisper_device_combo.setFixedWidth(80)
    whisper_grid.addWidget(window.whisper_device_combo)

    whisper_grid.addWidget(QLabel("计算类型:"))
    window.whisper_compute_combo = QComboBox()
    window.whisper_compute_combo.addItems(["int8", "float16", "float32"])
    window.whisper_compute_combo.setFixedWidth(100)
    whisper_grid.addWidget(window.whisper_compute_combo)

    whisper_grid.addWidget(QLabel("语言:"))
    window.whisper_lang_combo = QComboBox()
    window.whisper_lang_combo.addItems(["zh", "en", "auto"])
    window.whisper_lang_combo.setFixedWidth(80)
    whisper_grid.addWidget(window.whisper_lang_combo)

    whisper_grid.addStretch()
    whisper_layout.addLayout(whisper_grid)
    scroll_layout.addWidget(whisper_card)

    # 提示词管理
    prompt_card = QFrame()
    prompt_card.setStyleSheet("QFrame { background: #16213e; border-radius: 10px; padding: 15px; }")
    prompt_layout = QVBoxLayout(prompt_card)
    prompt_layout.addWidget(QLabel("📝 提示词管理"))

    prompt_names = ['analyze_video', 'analyze_anchor', 'extract_knowledge', 'analyze_visual']
    prompt_labels = ['单视频分析', '主播蒸馏', '知识点提取', '画面分析']
    window.prompt_edits = {}
    for name, label in zip(prompt_names, prompt_labels):
        prompt_layout.addWidget(QLabel(f"{label}:"))
        edit = QTextEdit()
        edit.setMaximumHeight(80)
        edit.setStyleSheet("""
            QTextEdit { background: #0d1b2a; border: 1px solid #0f3460; border-radius: 6px;
            color: #ccc; font-size: 11px; font-family: Consolas; padding: 6px; }
        """)
        edit.setPlainText(load_prompt(name))
        window.prompt_edits[name] = edit
        prompt_layout.addWidget(edit)

    prompt_btn_row = QHBoxLayout()
    btn_save_prompts = QPushButton("💾 保存所有提示词")
    btn_save_prompts.clicked.connect(lambda: _save_all_prompts(window))
    prompt_btn_row.addWidget(btn_save_prompts)
    btn_reset_prompts = QPushButton("🔄 重置全部默认")
    btn_reset_prompts.clicked.connect(lambda: _reset_all_prompts(window))
    prompt_btn_row.addWidget(btn_reset_prompts)
    prompt_btn_row.addStretch()
    prompt_layout.addLayout(prompt_btn_row)
    scroll_layout.addWidget(prompt_card)

    # 保存设置按钮
    btn_save_settings = QPushButton("💾 保存所有设置")
    btn_save_settings.setFixedHeight(40)
    btn_save_settings.clicked.connect(lambda: _save_all_settings(window))
    scroll_layout.addWidget(btn_save_settings)

    scroll_layout.addStretch()
    return page


# ---- 逐字稿功能 ----

def _refresh_transcript_combo(window):
    downloads = get_downloads()
    window.transcript_combo.clear()
    for d in downloads:
        has_transcript = get_transcript(d['aweme_id']) is not None
        status = "✅" if has_transcript else "⏳"
        window.transcript_combo.addItem(f"{status} {d['title'][:40]} - {d['author']}", d['aweme_id'])


def _start_transcript(window):
    idx = window.transcript_combo.currentIndex()
    if idx < 0:
        QMessageBox.warning(window, "提示", "请先选择视频")
        return

    aweme_id = window.transcript_combo.currentData()
    download = get_download_by_aweme_id(aweme_id)
    if not download or not download.get('video_path'):
        QMessageBox.warning(window, "提示", "找不到视频文件")
        return

    video_path = download['video_path']
    if not os.path.exists(video_path):
        QMessageBox.warning(window, "提示", f"视频文件不存在: {video_path}")
        return

    whisper_config = {
        'model_size': window.whisper_model_combo.currentText() if hasattr(window, 'whisper_model_combo') else 'base',
        'device': window.whisper_device_combo.currentText() if hasattr(window, 'whisper_device_combo') else 'cpu',
        'compute_type': window.whisper_compute_combo.currentText() if hasattr(window, 'whisper_compute_combo') else 'int8',
        'language': window.whisper_lang_combo.currentText() if hasattr(window, 'whisper_lang_combo') else 'zh',
    }

    window.transcript_progress.setVisible(True)
    window.transcript_progress.setRange(0, 0)

    window.transcript_worker = TranscriptWorker(video_path, aweme_id, window.transcript_dir, whisper_config)
    window.transcript_worker.log.connect(lambda msg: window.transcript_result.append(msg))
    window.transcript_worker.finished.connect(lambda aid: _on_transcript_done(window, aid))
    window.transcript_worker.error.connect(lambda msg: (
        QMessageBox.warning(window, "错误", msg),
        window.transcript_progress.setVisible(False)
    ))
    window.transcript_worker.start()


def _on_transcript_done(window, aweme_id):
    window.transcript_progress.setVisible(False)
    transcript = get_transcript(aweme_id)
    if transcript:
        window.transcript_result.setPlainText(transcript['text_content'])
    QMessageBox.information(window, "完成", "逐字稿提取完成!")
    _refresh_transcript_combo(window)


def _save_transcript_file(window):
    text = window.transcript_result.toPlainText()
    if not text:
        QMessageBox.warning(window, "提示", "没有可保存的内容")
        return
    path, _ = QFileDialog.getSaveFileName(window, "保存逐字稿", "", "Text Files (*.txt)")
    if path:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        QMessageBox.information(window, "完成", f"已保存到: {path}")


# ---- AI 分析功能 ----

def _switch_analysis_mode(window, mode):
    window.current_analysis_mode = mode
    window.btn_mode_video.setChecked(mode == 'single_video')
    window.btn_mode_anchor.setChecked(mode == 'anchor')
    window.btn_mode_visual.setChecked(mode == 'visual')
    window.dim_frame.setVisible(mode == 'anchor')

    # 加载对应提示词
    prompt_map = {
        'single_video': 'analyze_video',
        'anchor': 'analyze_anchor',
        'visual': 'analyze_visual',
    }
    name = prompt_map.get(mode, 'analyze_video')
    window.analysis_prompt.setPlainText(load_prompt(name))

    # 刷新下拉框
    _refresh_analysis_combo(window)


def _refresh_analysis_combo(window):
    window.analysis_combo.clear()
    downloads = get_downloads()
    if window.current_analysis_mode == 'anchor':
        # 主播模式：按作者分组
        authors = {}
        for d in downloads:
            a = d['author']
            if a not in authors:
                authors[a] = 0
            authors[a] += 1
        for author, count in authors.items():
            window.analysis_combo.addItem(f"👤 {author} ({count}个视频)", author)
    else:
        # 单视频/画面模式：列出所有视频
        for d in downloads:
            has_t = "📝" if get_transcript(d['aweme_id']) else ""
            window.analysis_combo.addItem(
                f"{has_t} {d['title'][:40]} - {d['author']}", d['aweme_id']
            )


def _start_analysis(window):
    idx = window.analysis_combo.currentIndex()
    if idx < 0:
        QMessageBox.warning(window, "提示", "请先选择分析对象")
        return

    config = window.llm_config
    engine = AnalyzerEngine(config)
    prompt_text = window.analysis_prompt.toPlainText()

    if window.current_analysis_mode == 'single_video':
        aweme_id = window.analysis_combo.currentData()
        download = get_download_by_aweme_id(aweme_id)
        transcript = get_transcript(aweme_id)
        if not transcript:
            QMessageBox.warning(window, "提示", "请先提取逐字稿")
            return
        video_info = {
            'title': download['title'] if download else '',
            'author': download['author'] if download else '',
            'digg_count': download['digg_count'] if download else 0,
            'comment_count': download['comment_count'] if download else 0,
            'share_count': download['share_count'] if download else 0,
        }
        window.analysis_worker = AnalysisWorker(
            engine, 'single_video',
            video_info=video_info,
            transcript=transcript['text_content'],
            prompt=prompt_text,
        )
    elif window.current_analysis_mode == 'anchor':
        author = window.analysis_combo.currentData()
        downloads = get_downloads()
        author_videos = [d for d in downloads if d['author'] == author]
        # 合并逐字稿
        transcripts_parts = []
        for d in author_videos:
            t = get_transcript(d['aweme_id'])
            if t:
                transcripts_parts.append(f"【{d['title']}】\n{t['text_content']}")
        full_transcripts = '\n\n'.join(transcripts_parts)
        if not full_transcripts:
            QMessageBox.warning(window, "提示", "该主播没有已提取的逐字稿，请先提取")
            return
        selected_dims = [name for name, chk in window.dim_checks if chk.isChecked()]
        window.analysis_worker = AnalysisWorker(
            engine, 'anchor',
            videos_data=author_videos,
            full_transcripts=full_transcripts,
            prompt=prompt_text,
            dimensions=selected_dims,
        )
    elif window.current_analysis_mode == 'visual':
        aweme_id = window.analysis_combo.currentData()
        download = get_download_by_aweme_id(aweme_id)
        video_info = {
            'title': download['title'] if download else '',
            'author': download['author'] if download else '',
        }
        window.analysis_worker = AnalysisWorker(
            engine, 'visual',
            video_info=video_info,
            prompt=prompt_text,
        )
    else:
        return

    window.analysis_result.setPlainText("分析中，请稍候...")
    window.analysis_worker.log.connect(lambda msg: window.analysis_result.append(f"\n{msg}"))
    window.analysis_worker.result_ready.connect(lambda r: _on_analysis_done(window, r))
    window.analysis_worker.error.connect(lambda msg: window.analysis_result.setPlainText(f"错误: {msg}"))
    window.analysis_worker.start()


def _on_analysis_done(window, result):
    window.analysis_result.setPlainText(result)
    # 保存到分析历史
    save_analysis({
        'aweme_id': '',
        'analysis_type': window.current_analysis_mode,
        'prompt_used': window.analysis_prompt.toPlainText()[:200],
        'result_path': '',
        'result_preview': result[:500],
    })


def _save_analysis_report(window):
    text = window.analysis_result.toPlainText()
    if not text:
        QMessageBox.warning(window, "提示", "没有可保存的内容")
        return
    path, _ = QFileDialog.getSaveFileName(window, "保存分析报告", "", "Markdown (*.md)")
    if path:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        QMessageBox.information(window, "完成", f"已保存到: {path}")


def _save_analysis_prompt(window):
    mode_map = {
        'single_video': 'analyze_video',
        'anchor': 'analyze_anchor',
        'visual': 'analyze_visual',
    }
    name = mode_map.get(window.current_analysis_mode, 'analyze_video')
    save_prompt(name, window.analysis_prompt.toPlainText())
    QMessageBox.information(window, "完成", "提示词已保存")


def _reset_analysis_prompt(window):
    mode_map = {
        'single_video': 'analyze_video',
        'anchor': 'analyze_anchor',
        'visual': 'analyze_visual',
    }
    name = mode_map.get(window.current_analysis_mode, 'analyze_video')
    reset_prompt(name)
    window.analysis_prompt.setPlainText(load_prompt(name))
    QMessageBox.information(window, "完成", "提示词已重置为默认")


# ---- 知识点功能 ----

def _refresh_knowledge_combo(window):
    window.knowledge_combo.clear()
    transcripts = get_all_transcripts()
    for t in transcripts:
        title = t.get('title', t.get('aweme_id', ''))
        author = t.get('author', '')
        wc = t.get('word_count', 0)
        window.knowledge_combo.addItem(f"📝 {title[:40]} - {author} ({wc}字)", t['aweme_id'])


def _start_knowledge(window):
    idx = window.knowledge_combo.currentIndex()
    if idx < 0:
        QMessageBox.warning(window, "提示", "请先选择视频")
        return

    aweme_id = window.knowledge_combo.currentData()
    transcript = get_transcript(aweme_id)
    if not transcript:
        QMessageBox.warning(window, "提示", "没有逐字稿")
        return

    config = window.llm_config
    engine = AnalyzerEngine(config)
    prompt_text = window.knowledge_prompt.toPlainText()

    window.knowledge_worker = AnalysisWorker(
        engine, 'knowledge',
        transcript=transcript['text_content'],
        prompt=prompt_text,
    )
    window.knowledge_result.setPlainText("提取中，请稍候...")
    window.knowledge_worker.log.connect(lambda msg: window.knowledge_result.append(f"\n{msg}"))
    window.knowledge_worker.result_ready.connect(lambda r: window.knowledge_result.setPlainText(r))
    window.knowledge_worker.error.connect(lambda msg: window.knowledge_result.setPlainText(f"错误: {msg}"))
    window.knowledge_worker.start()


def _save_knowledge_file(window):
    text = window.knowledge_result.toPlainText()
    if not text:
        QMessageBox.warning(window, "提示", "没有可保存的内容")
        return
    path, _ = QFileDialog.getSaveFileName(window, "保存知识点", "", "Markdown (*.md)")
    if path:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        QMessageBox.information(window, "完成", f"已保存到: {path}")


# ---- 历史功能 ----

def _load_history(window, keyword=None):
    downloads = get_downloads(keyword)
    table = window.history_table
    table.setRowCount(0)

    for d in downloads:
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        table.setItem(row, 1, QTableWidgetItem(d.get('title', '')[:50]))
        table.setItem(row, 2, QTableWidgetItem(d.get('author', '')))
        table.setItem(row, 3, QTableWidgetItem(str(d.get('digg_count', 0))))
        table.setItem(row, 4, QTableWidgetItem(str(d.get('comment_count', 0))))
        table.setItem(row, 5, QTableWidgetItem(str(d.get('share_count', 0))))

        ts = d.get('created_at', '')
        table.setItem(row, 6, QTableWidgetItem(str(ts)[:16]))

        has_t = "✅" if get_transcript(d['aweme_id']) else "❌"
        table.setItem(row, 7, QTableWidgetItem(has_t))
        table.setItem(row, 8, QTableWidgetItem("-"))

        # 操作按钮
        btn_widget = QWidget()
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(4, 2, 4, 2)
        btn_layout.setSpacing(4)

        btn_transcript = QPushButton("📝")
        btn_transcript.setFixedSize(28, 28)
        btn_transcript.setToolTip("提取逐字稿")
        aid = d['aweme_id']
        btn_transcript.clicked.connect(lambda _, a=aid: _history_extract_transcript(window, a))
        btn_layout.addWidget(btn_transcript)

        btn_analysis = QPushButton("🤖")
        btn_analysis.setFixedSize(28, 28)
        btn_analysis.setToolTip("AI 分析")
        btn_analysis.clicked.connect(lambda _, a=aid: _history_analyze(window, a))
        btn_layout.addWidget(btn_analysis)

        btn_delete = QPushButton("🗑")
        btn_delete.setFixedSize(28, 28)
        btn_delete.setToolTip("删除记录")
        btn_delete.clicked.connect(lambda _, a=aid: _history_delete(window, a))
        btn_layout.addWidget(btn_delete)

        table.setCellWidget(row, 9, btn_widget)


def _history_extract_transcript(window, aweme_id):
    window.switch_page(3)
    # 自动选中对应视频
    for i in range(window.transcript_combo.count()):
        if window.transcript_combo.itemData(i) == aweme_id:
            window.transcript_combo.setCurrentIndex(i)
            break


def _history_analyze(window, aweme_id):
    window.switch_page(4)
    _refresh_analysis_combo(window)
    for i in range(window.analysis_combo.count()):
        if window.analysis_combo.itemData(i) == aweme_id:
            window.analysis_combo.setCurrentIndex(i)
            break


def _history_delete(window, aweme_id):
    reply = QMessageBox.question(window, "确认", "确定要删除这条记录吗？")
    if reply == QMessageBox.Yes:
        delete_download(aweme_id)
        _load_history(window)


# ---- 设置功能 ----

def _load_llm_config_file():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'llm_config.json')
    default = {
        'llm_provider': 'ollama',
        'llm_api_base': 'http://localhost:11434',
        'llm_api_key': '',
        'llm_model': 'qwen2.5:7b',
        'llm_temperature': 0.7,
        'llm_max_tokens': 4096,
        'whisper_model': 'base',
        'whisper_device': 'cpu',
        'whisper_compute': 'int8',
        'whisper_language': 'zh',
    }
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            default.update(saved)
        except:
            pass
    return default


def _save_llm_config_file(config):
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'llm_config.json')
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _load_llm_settings(window):
    cfg = window.llm_config
    window.llm_provider_combo.setCurrentText(cfg.get('llm_provider', 'ollama'))
    window.llm_api_base_input.setText(cfg.get('llm_api_base', ''))
    window.llm_api_key_input.setText(cfg.get('llm_api_key', ''))
    window.llm_model_input.setText(cfg.get('llm_model', ''))
    window.llm_temp_input.setValue(int(cfg.get('llm_temperature', 7)))
    window.llm_max_tokens_input.setValue(cfg.get('llm_max_tokens', 4096))
    window.whisper_model_combo.setCurrentText(cfg.get('whisper_model', 'base'))
    window.whisper_device_combo.setCurrentText(cfg.get('whisper_device', 'cpu'))
    window.whisper_compute_combo.setCurrentText(cfg.get('whisper_compute', 'int8'))
    window.whisper_lang_combo.setCurrentText(cfg.get('whisper_language', 'zh'))


def _save_all_settings(window):
    window.llm_config.update({
        'llm_provider': window.llm_provider_combo.currentText(),
        'llm_api_base': window.llm_api_base_input.text(),
        'llm_api_key': window.llm_api_key_input.text(),
        'llm_model': window.llm_model_input.text(),
        'llm_temperature': window.llm_temp_input.value() / 10.0,
        'llm_max_tokens': window.llm_max_tokens_input.value(),
        'whisper_model': window.whisper_model_combo.currentText(),
        'whisper_device': window.whisper_device_combo.currentText(),
        'whisper_compute': window.whisper_compute_combo.currentText(),
        'whisper_language': window.whisper_lang_combo.currentText(),
    })
    _save_llm_config_file(window.llm_config)
    QMessageBox.information(window, "提示", "设置已保存 ✓")


def _save_all_prompts(window):
    for name, edit in window.prompt_edits.items():
        save_prompt(name, edit.toPlainText())
    QMessageBox.information(window, "提示", "所有提示词已保存 ✓")


def _reset_all_prompts(window):
    for name in window.prompt_edits:
        reset_prompt(name)
        window.prompt_edits[name].setPlainText(load_prompt(name))
    QMessageBox.information(window, "提示", "所有提示词已重置为默认 ✓")


# ---- 下载完成后自动保存到数据库 ----

def _hook_download_done(window):
    """钩子：下载完成时自动保存到数据库"""
    orig_on_finished = window.on_finished

    def new_on_finished(ok, skip, fail):
        orig_on_finished(ok, skip, fail)
        # 从下载目录扫描新文件，保存到数据库
        save_dir = window.dir_input.text() if hasattr(window, 'dir_input') else window.config.get('saveDir', '')
        if not save_dir or not os.path.exists(save_dir):
            return
        # 扫描 _downloaded.json
        dl_file = os.path.join(save_dir, '_downloaded.json')
        if os.path.exists(dl_file):
            try:
                with open(dl_file, 'r', encoding='utf-8') as f:
                    downloaded = json.load(f)
                for vid, filename in downloaded.items():
                    existing = get_download_by_aweme_id(vid)
                    if not existing:
                        # 找到视频文件路径
                        for root, dirs, files in os.walk(save_dir):
                            if filename in files:
                                save_download({
                                    'aweme_id': vid,
                                    'title': os.path.splitext(filename)[0],
                                    'author': os.path.basename(root),
                                    'video_path': os.path.join(root, filename),
                                })
                                break
            except:
                pass

    window.on_finished = new_on_finished

# ============================================================
# MainWindow 扩展方法（逐字稿/AI分析/知识点/历史/设置）
# ============================================================

# --- 逐字稿相关 ---
def _load_downloads_for_combo(self, combo):
    """加载已下载视频到下拉框"""
    combo.clear()
    downloads = get_downloads()
    for d in downloads:
        combo.addItem(f"{d['title'][:40]} - {d['author']}", d['aweme_id'])

MainWindow._load_downloads_for_combo = _load_downloads_for_combo

def load_transcript_list(self):
    """加载已提取的逐字稿列表"""
    self._load_downloads_for_combo(self.transcript_combo)
MainWindow.load_transcript_list = load_transcript_list

def start_transcript(self):
    """开始提取逐字稿"""
    idx = self.transcript_combo.currentIndex()
    if idx < 0:
        QMessageBox.warning(self, "提示", "请先选择一个视频")
        return
    aweme_id = self.transcript_combo.currentData()
    download = get_download_by_aweme_id(aweme_id)
    if not download or not download.get('video_path'):
        QMessageBox.warning(self, "提示", "找不到视频文件")
        return

    video_path = download['video_path']
    if not os.path.exists(video_path):
        QMessageBox.warning(self, "提示", f"视频文件不存在: {video_path}")
        return

    transcript_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'transcripts')
    os.makedirs(transcript_dir, exist_ok=True)

    self.transcript_worker = TranscriptWorker(
        video_path, aweme_id, transcript_dir, self.config
    )
    self.transcript_worker.log.connect(self.on_transcript_log)
    self.transcript_worker.finished.connect(self.on_transcript_done)
    self.transcript_worker.error.connect(self.on_transcript_error)
    self.transcript_worker.start()

    self.transcript_progress.setVisible(True)
    self.transcript_progress.setRange(0, 0)
    self.transcript_status.setText("正在提取逐字稿...")
    self.btn_transcript_start.setEnabled(False)
MainWindow.start_transcript = start_transcript

def on_transcript_log(self, msg):
    self.transcript_text.append(msg)
    self.transcript_text.moveCursor(QTextCursor.End)
MainWindow.on_transcript_log = on_transcript_log

def on_transcript_done(self, aweme_id, text):
    self.transcript_progress.setVisible(False)
    self.transcript_status.setText("提取完成 ✓")
    self.transcript_text.clear()
    self.transcript_text.setText(text)
    self.btn_transcript_start.setEnabled(True)
    self.btn_transcript_save.setEnabled(True)
    self.btn_transcript_copy.setEnabled(True)
    # 保存到数据库
    save_transcript({
        'aweme_id': aweme_id,
        'text_content': text,
        'word_count': len(text),
    })
MainWindow.on_transcript_done = on_transcript_done

def on_transcript_error(self, msg):
    self.transcript_progress.setVisible(False)
    self.transcript_status.setText(f"提取失败: {msg}")
    self.btn_transcript_start.setEnabled(True)
    QMessageBox.warning(self, "提取失败", msg)
MainWindow.on_transcript_error = on_transcript_error

def save_transcript_file(self):
    text = self.transcript_text.toPlainText()
    if not text:
        return
    path, _ = QFileDialog.getSaveFileName(self, "保存逐字稿", "", "文本文件 (*.txt)")
    if path:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        QMessageBox.information(self, "提示", "已保存 ✓")
MainWindow.save_transcript_file = save_transcript_file

def copy_transcript(self):
    text = self.transcript_text.toPlainText()
    if text:
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "提示", "已复制到剪贴板 ✓")
MainWindow.copy_transcript = copy_transcript

# --- AI 分析相关 ---
def switch_analysis_mode(self, mode):
    self._analysis_mode = mode
    self.btn_mode_video.setChecked(mode == 'video')
    self.btn_mode_anchor.setChecked(mode == 'anchor')
    self.btn_mode_visual.setChecked(mode == 'visual')
    self.load_analysis_list()
    # 加载对应提示词
    prompt_map = {'video': 'analyze_video', 'anchor': 'analyze_anchor', 'visual': 'analyze_visual'}
    self.analysis_prompt.setText(load_prompt(prompt_map.get(mode, 'analyze_video')))
MainWindow.switch_analysis_mode = switch_analysis_mode

def load_analysis_list(self):
    self.analysis_combo.clear()
    if getattr(self, '_analysis_mode', 'video') == 'anchor':
        # 主播蒸馏模式：加载所有作者
        downloads = get_downloads()
        authors = {}
        for d in downloads:
            a = d.get('author', '未知')
            if a not in authors:
                authors[a] = 0
            authors[a] += 1
        for author, count in authors.items():
            self.analysis_combo.addItem(f"{author} ({count}个视频)", author)
    else:
        self._load_downloads_for_combo(self.analysis_combo)
    # 加载提示词
    prompt_map = {'video': 'analyze_video', 'anchor': 'analyze_anchor', 'visual': 'analyze_visual'}
    mode = getattr(self, '_analysis_mode', 'video')
    self.analysis_prompt.setText(load_prompt(prompt_map.get(mode, 'analyze_video')))
MainWindow.load_analysis_list = load_analysis_list

def start_analysis(self):
    idx = self.analysis_combo.currentIndex()
    if idx < 0:
        QMessageBox.warning(self, "提示", "请先选择分析对象")
        return

    # 加载 LLM 配置
    engine = AnalyzerEngine({
        'llm_provider': self.config.get('llm_provider', 'ollama'),
        'api_url': self.config.get('api_url', 'http://localhost:11434'),
        'api_key': self.config.get('api_key', ''),
        'model': self.config.get('model', 'qwen2.5:7b'),
        'temperature': self.config.get('llm_temperature', 0.7),
        'max_tokens': self.config.get('llm_max_tokens', 4096),
    })

    prompt_text = self.analysis_prompt.toPlainText()
    mode = getattr(self, '_analysis_mode', 'video')

    if mode == 'video':
        aweme_id = self.analysis_combo.currentData()
        transcript = get_transcript(aweme_id)
        download = get_download_by_aweme_id(aweme_id)
        if not transcript:
            QMessageBox.warning(self, "提示", "请先提取逐字稿")
            return
        data = {
            'video_info': download or {},
            'transcript': transcript.get('text_content', ''),
            'prompt': prompt_text,
        }
    elif mode == 'anchor':
        author = self.analysis_combo.currentData()
        downloads = [d for d in get_downloads() if d.get('author') == author]
        all_transcripts = []
        for d in downloads:
            t = get_transcript(d['aweme_id'])
            if t:
                all_transcripts.append(f"## {d['title']}\n{t.get('text_content', '')}")
        if not all_transcripts:
            QMessageBox.warning(self, "提示", "请先为该主播的视频提取逐字稿")
            return
        data = {
            'videos_data': downloads,
            'transcripts_text': '\n\n'.join(all_transcripts),
            'prompt': prompt_text,
            'dimensions': list(range(1, 11)),
        }
    elif mode == 'visual':
        aweme_id = self.analysis_combo.currentData()
        download = get_download_by_aweme_id(aweme_id)
        if not download:
            QMessageBox.warning(self, "提示", "找不到视频信息")
            return
        data = {
            'video_info': download,
            'cover_description': f"封面路径: {download.get('cover_path', '无')}",
            'prompt': prompt_text,
        }
    else:
        return

    self.analysis_worker = AnalysisWorker(engine, mode, data, self.config)
    self.analysis_worker.progress.connect(self.on_analysis_progress)
    self.analysis_worker.finished.connect(self.on_analysis_done)
    self.analysis_worker.error.connect(self.on_analysis_error)
    self.analysis_worker.start()

    self.analysis_progress.setVisible(True)
    self.analysis_progress.setRange(0, 0)
    self.analysis_status.setText("正在分析...")
    self.btn_analysis_start.setEnabled(False)
MainWindow.start_analysis = start_analysis

def on_analysis_progress(self, msg):
    self.analysis_status.setText(msg)
MainWindow.on_analysis_progress = on_analysis_progress

def on_analysis_done(self, result):
    self.analysis_progress.setVisible(False)
    self.analysis_status.setText("分析完成 ✓")
    self.analysis_result.setText(result)
    self.btn_analysis_start.setEnabled(True)
    self.btn_analysis_save.setEnabled(True)
MainWindow.on_analysis_done = on_analysis_done

def on_analysis_error(self, msg):
    self.analysis_progress.setVisible(False)
    self.analysis_status.setText(f"分析失败: {msg}")
    self.btn_analysis_start.setEnabled(True)
    QMessageBox.warning(self, "分析失败", msg)
MainWindow.on_analysis_error = on_analysis_error

def save_analysis_report(self):
    text = self.analysis_result.toPlainText()
    if not text:
        return
    mode = getattr(self, '_analysis_mode', 'video')
    mode_names = {'video': '单视频分析', 'anchor': '主播蒸馏', 'visual': '画面分析'}
    path, _ = QFileDialog.getSaveFileName(
        self, "保存分析报告",
        f"{mode_names.get(mode, 'analysis')}_{time.strftime('%Y%m%d_%H%M%S')}.md",
        "Markdown (*.md)")
    if path:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        QMessageBox.information(self, "提示", "报告已保存 ✓")
MainWindow.save_analysis_report = save_analysis_report

# --- 知识点相关 ---
def load_knowledge_list(self):
    self._load_downloads_for_combo(self.knowledge_combo)
    self.knowledge_prompt.setText(load_prompt('extract_knowledge'))
MainWindow.load_knowledge_list = load_knowledge_list

def start_knowledge(self):
    idx = self.knowledge_combo.currentIndex()
    if idx < 0:
        QMessageBox.warning(self, "提示", "请先选择视频")
        return
    aweme_id = self.knowledge_combo.currentData()
    transcript = get_transcript(aweme_id)
    if not transcript:
        QMessageBox.warning(self, "提示", "请先提取逐字稿")
        return

    engine = AnalyzerEngine({
        'llm_provider': self.config.get('llm_provider', 'ollama'),
        'api_url': self.config.get('api_url', 'http://localhost:11434'),
        'api_key': self.config.get('api_key', ''),
        'model': self.config.get('model', 'qwen2.5:7b'),
        'temperature': self.config.get('llm_temperature', 0.7),
        'max_tokens': self.config.get('llm_max_tokens', 4096),
    })

    data = {
        'transcript': transcript.get('text_content', ''),
        'prompt': self.knowledge_prompt.toPlainText(),
    }

    self.knowledge_worker = AnalysisWorker(engine, 'knowledge', data, self.config)
    self.knowledge_worker.progress.connect(self.on_knowledge_progress)
    self.knowledge_worker.finished.connect(self.on_knowledge_done)
    self.knowledge_worker.error.connect(self.on_knowledge_error)
    self.knowledge_worker.start()

    self.knowledge_progress.setVisible(True)
    self.knowledge_progress.setRange(0, 0)
    self.knowledge_status.setText("正在提取知识点...")
    self.btn_knowledge_start.setEnabled(False)
MainWindow.start_knowledge = start_knowledge

def on_knowledge_progress(self, msg):
    self.knowledge_status.setText(msg)
MainWindow.on_knowledge_progress = on_knowledge_progress

def on_knowledge_done(self, result):
    self.knowledge_progress.setVisible(False)
    self.knowledge_status.setText("提取完成 ✓")
    self.knowledge_result.setText(result)
    self.btn_knowledge_start.setEnabled(True)
    self.btn_knowledge_save.setEnabled(True)
    self.btn_knowledge_copy.setEnabled(True)
MainWindow.on_knowledge_done = on_knowledge_done

def on_knowledge_error(self, msg):
    self.knowledge_progress.setVisible(False)
    self.knowledge_status.setText(f"提取失败: {msg}")
    self.btn_knowledge_start.setEnabled(True)
    QMessageBox.warning(self, "提取失败", msg)
MainWindow.on_knowledge_error = on_knowledge_error

def save_knowledge(self):
    text = self.knowledge_result.toPlainText()
    if not text:
        return
    path, _ = QFileDialog.getSaveFileName(
        self, "保存知识点",
        f"知识点_{time.strftime('%Y%m%d_%H%M%S')}.md",
        "Markdown (*.md)")
    if path:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        QMessageBox.information(self, "提示", "已保存 ✓")
MainWindow.save_knowledge = save_knowledge

def copy_knowledge(self):
    text = self.knowledge_result.toPlainText()
    if text:
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "提示", "已复制到剪贴板 ✓")
MainWindow.copy_knowledge = copy_knowledge

# --- 下载历史相关 ---
def load_history(self):
    keyword = self.history_search.text().strip() or None
    downloads = get_downloads(keyword)
    self.history_table.setRowCount(0)
    for d in downloads:
        row = self.history_table.rowCount()
        self.history_table.insertRow(row)
        self.history_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self.history_table.setItem(row, 1, QTableWidgetItem(d.get('title', '')[:40]))
        self.history_table.setItem(row, 2, QTableWidgetItem(d.get('author', '')))
        self.history_table.setItem(row, 3, QTableWidgetItem(str(d.get('digg_count', 0))))
        self.history_table.setItem(row, 4, QTableWidgetItem(str(d.get('comment_count', 0))))
        self.history_table.setItem(row, 5, QTableWidgetItem(str(d.get('share_count', 0))))
        self.history_table.setItem(row, 6, QTableWidgetItem(d.get('created_at', '')[:16]))

        # 逐字稿状态
        t = get_transcript(d['aweme_id'])
        status_item = QTableWidgetItem("✓" if t else "✗")
        status_item.setForeground(QColor(0, 200, 100) if t else QColor(200, 100, 100))
        self.history_table.setItem(row, 7, status_item)

        # 操作按钮
        btn_frame = QWidget()
        btn_layout = QHBoxLayout(btn_frame)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4)

        btn_folder = QPushButton("📂")
        btn_folder.setFixedSize(28, 28)
        btn_folder.setToolTip("打开文件夹")
        aweme_id = d['aweme_id']
        video_path = d.get('video_path', '')
        btn_folder.clicked.connect(lambda _, p=video_path: self.open_video_folder(p))
        btn_layout.addWidget(btn_folder)

        btn_del = QPushButton("🗑")
        btn_del.setFixedSize(28, 28)
        btn_del.setToolTip("删除")
        btn_del.clicked.connect(lambda _, a=aweme_id: self.delete_history_item(a))
        btn_layout.addWidget(btn_del)

        self.history_table.setCellWidget(row, 8, btn_frame)

    self.history_status.setText(f"共 {len(downloads)} 条记录")
MainWindow.load_history = load_history

def search_history(self):
    self.load_history()
MainWindow.search_history = search_history

def open_video_folder(self, video_path):
    if video_path and os.path.exists(video_path):
        folder = os.path.dirname(video_path)
        os.startfile(folder)
    else:
        QMessageBox.warning(self, "提示", "文件不存在")
MainWindow.open_video_folder = open_video_folder

def delete_history_item(self, aweme_id):
    reply = QMessageBox.question(self, "确认", "确定要删除这条记录吗？")
    if reply == QMessageBox.Yes:
        delete_download(aweme_id)
        self.load_history()
MainWindow.delete_history_item = delete_history_item

# --- 设置相关 ---
def toggle_api_key(self):
    if self.llm_api_key.echoMode() == QLineEdit.Password:
        self.llm_api_key.setEchoMode(QLineEdit.Normal)
        self.btn_llm_key_toggle.setText("🙈")
    else:
        self.llm_api_key.setEchoMode(QLineEdit.Password)
        self.btn_llm_key_toggle.setText("👁")
MainWindow.toggle_api_key = toggle_api_key

PROMPT_NAMES = ['analyze_video', 'analyze_anchor', 'extract_knowledge', 'analyze_visual']

def _load_prompt_to_edit(self):
    idx = self.prompt_tabs_widget.currentIndex()
    if 0 <= idx < len(PROMPT_NAMES):
        self.prompt_edit.setText(load_prompt(PROMPT_NAMES[idx]))
MainWindow._load_prompt_to_edit = _load_prompt_to_edit

def on_prompt_tab_changed(self, idx):
    self._load_prompt_to_edit()
MainWindow.on_prompt_tab_changed = on_prompt_tab_changed

def save_current_prompt(self):
    idx = self.prompt_tabs_widget.currentIndex()
    if 0 <= idx < len(PROMPT_NAMES):
        save_prompt(PROMPT_NAMES[idx], self.prompt_edit.toPlainText())
        QMessageBox.information(self, "提示", "提示词已保存 ✓")
MainWindow.save_current_prompt = save_current_prompt

def reset_current_prompt(self):
    idx = self.prompt_tabs_widget.currentIndex()
    if 0 <= idx < len(PROMPT_NAMES):
        # 删除文件，让 load_prompt 返回空
        path = os.path.join(PROMPTS_DIR, f'{PROMPT_NAMES[idx]}.txt')
        if os.path.exists(path):
            os.remove(path)
        self.prompt_edit.setText("")
        QMessageBox.information(self, "提示", "已恢复默认")
MainWindow.reset_current_prompt = reset_current_prompt

def save_advanced_settings(self):
    self.config['llm_provider'] = self.llm_provider.currentText()
    self.config['api_url'] = self.llm_api_url.text()
    self.config['api_key'] = self.llm_api_key.text()
    self.config['model'] = self.llm_model.text()
    self.config['llm_temperature'] = self.llm_temperature.value() / 10.0
    self.config['llm_max_tokens'] = self.llm_max_tokens.value()
    self.config['whisper_model'] = self.whisper_model.currentText()
    self.config['whisper_device'] = self.whisper_device.currentText()
    self.config['whisper_compute'] = self.whisper_compute.currentText()
    self.config['whisper_lang'] = self.whisper_lang.currentText()
    save_config(self.config)
    QMessageBox.information(self, "提示", "高级设置已保存 ✓")
MainWindow.save_advanced_settings = save_advanced_settings


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)
    window = MainWindow()
    extend_main_window(window)
    _load_llm_settings(window)
    window.show()
    app.exec_()


if __name__ == '__main__':
    main()
