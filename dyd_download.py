"""
DyD 独立下载器 v4 - 支持用户作品 + 收藏视频
"""
import sys
import os
import re
import json
import time

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

SAVE_DIR = os.path.join(os.environ.get('USERPROFILE', ''), 'Downloads', 'DyD')
if len(sys.argv) > 2 and os.path.isdir(sys.argv[-1]):
    SAVE_DIR = sys.argv[-1]
os.makedirs(SAVE_DIR, exist_ok=True)


def clean_filename(name):
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', '_', name).strip()[:100]
    return name or 'untitled'


def fetch_api(page, url):
    """通过页面内fetch调用抖音API"""
    try:
        return page.evaluate(f"""
            async () => {{
                try {{
                    const res = await fetch('{url}', {{ credentials: 'include' }});
                    const text = await res.text();
                    try {{
                        return JSON.parse(text);
                    }} catch (e) {{
                        return {{ error: 'Invalid JSON', text: text.substring(0, 200) }};
                    }}
                }} catch (e) {{
                    return {{ error: e.message }};
                }}
            }}
        """)
    except Exception as e:
        return {'error': str(e)}


def get_user_videos(user_url, page):
    """获取用户全部作品（cursor分页）"""
    if user_url not in page.url:
        print(f"导航到: {user_url}")
        try:
            page.goto(user_url, timeout=30000, wait_until='domcontentloaded')
        except:
            pass
        time.sleep(5)

    if '验证' in page.content():
        print("请完成验证码...")
        for i in range(120):
            time.sleep(3)
            if '验证' not in page.content():
                break
        time.sleep(2)

    m = re.search(r'/user/([^?]+)', user_url)
    sec_user_id = m.group(1) if m else ''
    print(f"sec_user_id: {sec_user_id}")

    collected = {}
    max_cursor = "0"
    page_num = 0

    print("分页采集全部作品...")

    while True:
        page_num += 1
        url = f'https://www.douyin.com/aweme/v1/web/aweme/post/?device_platform=webapp&aid=6383&channel=channel_pc_web&sec_user_id={sec_user_id}&max_cursor={max_cursor}&locate_query=false&publish_video_strategy_type=2&pc_client_type=1&version_code=170400&version_name=17.4.0&cookie_enabled=true&screen_width=1920&screen_height=1080&browser_language=zh-CN&browser_platform=Win32&browser_name=Chrome&browser_version=126.0.0.0&browser_online=true&platform=PC'
        result = fetch_api(page, url)

        if not result or not result.get('aweme_list'):
            break

        for item in result['aweme_list']:
            aid = str(item.get('aweme_id', ''))
            if aid and aid not in collected:
                collected[aid] = extract_video(item)

        has_more = result.get('has_more', False)
        max_cursor = str(result.get('max_cursor', ''))
        print(f"  第{page_num}页: +{len(result['aweme_list'])}个, 总计: {len(collected)}, has_more={has_more}")

        if not has_more or not max_cursor:
            break
        time.sleep(0.5)

    videos = list(collected.values())
    print(f"\n共采集到 {len(videos)} 个作品")
    return videos


def get_liked_videos(user_url, page):
    """获取用户收藏/喜欢的视频（cursor分页）"""
    if user_url not in page.url:
        print(f"导航到: {user_url}")
        try:
            page.goto(user_url, timeout=30000, wait_until='domcontentloaded')
        except:
            pass
        time.sleep(5)

    if '验证' in page.content():
        print("请完成验证码...")
        for i in range(120):
            time.sleep(3)
            if '验证' not in page.content():
                break
        time.sleep(2)

    m = re.search(r'/user/([^?]+)', user_url)
    sec_user_id = m.group(1) if m else ''
    print(f"sec_user_id: {sec_user_id}")

    # 点击"喜欢"标签
    print("切换到'喜欢'标签...")
    page.evaluate("""
        () => {
            const tabs = document.querySelectorAll('span, div, a, li');
            for (const t of tabs) {
                if (t.textContent.trim() === '喜欢') { t.click(); return 'clicked'; }
            }
            return 'not found';
        }
    """)
    time.sleep(3)

    collected = {}
    max_cursor = "0"
    page_num = 0

    print("分页采集喜欢列表...")

    while True:
        page_num += 1
        # 收藏列表用 aweme/favorite 或 mix/listcollection 接口
        # 先尝试 favorite 接口
        url = f'https://www-hj.douyin.com/aweme/v1/web/aweme/favorite/?device_platform=webapp&aid=6383&channel=channel_pc_web&sec_user_id={sec_user_id}&max_cursor={max_cursor}&locate_query=false&count=20&pc_client_type=1&version_code=170400&version_name=17.4.0&cookie_enabled=true&screen_width=1920&screen_height=1080&browser_language=zh-CN&browser_platform=Win32&browser_name=Chrome&browser_version=126.0.0.0&browser_online=true&platform=PC'
        result = fetch_api(page, url)

        if not result or not result.get('aweme_list'):
            # 如果favorite接口不行，尝试listcollection
            url2 = f'https://www.douyin.com/aweme/v1/web/mix/listcollection/?device_platform=webapp&aid=6383&channel=channel_pc_web&cursor={max_cursor}&count=20&pc_client_type=1&version_code=170400&version_name=17.4.0&cookie_enabled=true&screen_width=1920&screen_height=1080&browser_language=zh-CN&browser_platform=Win32&browser_name=Chrome&browser_version=126.0.0.0&browser_online=true&platform=PC'
            result = fetch_api(page, url2)

            if not result or not result.get('aweme_list') and not result.get('mix_list'):
                print(f"  第{page_num}页: 无数据，停止")
                break

            # listcollection 返回的是 mix_list
            mix_list = result.get('mix_list', [])
            if mix_list:
                for mix in mix_list:
                    aweme_list = mix.get('aweme_list', [])
                    for item in aweme_list:
                        aid = str(item.get('aweme_id', ''))
                        if aid and aid not in collected:
                            collected[aid] = extract_video(item)
                has_more = result.get('has_more', False)
                max_cursor = str(result.get('cursor', ''))
                print(f"  第{page_num}页: +{sum(len(m.get('aweme_list', [])) for m in mix_list)}个, 总计: {len(collected)}, has_more={has_more}")
            else:
                print(f"  第{page_num}页: 无数据")
                break
        else:
            for item in result['aweme_list']:
                aid = str(item.get('aweme_id', ''))
                if aid and aid not in collected:
                    collected[aid] = extract_video(item)

            has_more = result.get('has_more', False)
            max_cursor = str(result.get('max_cursor', ''))
            print(f"  第{page_num}页: +{len(result['aweme_list'])}个, 总计: {len(collected)}, has_more={has_more}")

        if not has_more or not max_cursor:
            break
        time.sleep(0.5)

    videos = list(collected.values())
    print(f"\n共采集到 {len(videos)} 个喜欢的视频")
    return videos


def extract_video(item):
    """从API响应中提取视频信息"""
    return {
        'aweme_id': str(item.get('aweme_id', '')),
        'desc': item.get('desc', ''),
        'author': item.get('author', {}).get('nickname', '') if item.get('author') else '',
        'author_sec_uid': item.get('author', {}).get('sec_uid', '') if item.get('author') else '',
        'digg_count': item.get('statistics', {}).get('digg_count', 0) if item.get('statistics') else 0,
        'comment_count': item.get('statistics', {}).get('comment_count', 0) if item.get('statistics') else 0,
        'share_count': item.get('statistics', {}).get('share_count', 0) if item.get('statistics') else 0,
        'create_time': item.get('create_time', 0),
        'video_url': (item.get('video', {}).get('play_addr', {}).get('url_list') or [''])[0] if item.get('video') else '',
        'cover_url': (item.get('video', {}).get('cover', {}).get('url_list') or [''])[0] if item.get('video') else '',
    }


def download_aweme(video, page, save_dir):
    """下载单个视频"""
    title = clean_filename(video.get('desc', '') or video.get('aweme_id', ''))
    aweme_id = video.get('aweme_id', '')
    author = video.get('author', 'unknown')

    print(f"\n  标题: {title}")
    print(f"  作者: {author}  赞:{video.get('digg_count', '-')} 评:{video.get('comment_count', '-')} 分享:{video.get('share_count', '-')}")

    video_url = video.get('video_url', '')
    if not video_url:
        print("  无视频地址，跳过")
        return None

    author_dir = os.path.join(save_dir, clean_filename(author))
    os.makedirs(author_dir, exist_ok=True)

    file_name = f"{title}.mp4"
    save_path = os.path.join(author_dir, file_name)

    if os.path.exists(save_path):
        print(f"  已存在，跳过")
        return save_path

    print(f"  下载视频...")
    resp = page.request.get(video_url)
    body = resp.body()

    if len(body) < 10000:
        print(f"  文件太小({len(body)}B)，跳过")
        return None

    with open(save_path, 'wb') as f:
        f.write(body)

    mb = len(body) / 1024 / 1024
    print(f"  已下载: {mb:.1f}MB")

    # 封面
    cover_url = video.get('cover_url', '')
    if cover_url:
        cover_path = os.path.join(author_dir, f"{title}_cover.jpg")
        if not os.path.exists(cover_path):
            try:
                cr = page.request.get(cover_url)
                with open(cover_path, 'wb') as f:
                    f.write(cr.body())
            except:
                pass

    # 作品信息
    create_time = video.get('create_time', 0)
    time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(create_time)) if create_time else '-'
    info_path = os.path.join(author_dir, f"{title}_info.txt")
    with open(info_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join([
            f"标题: {title}",
            f"作者: {author}",
            f"ID: {aweme_id}",
            f"链接: https://www.douyin.com/video/{aweme_id}",
            f"点赞: {video.get('digg_count', 0)}",
            f"评论: {video.get('comment_count', 0)}",
            f"分享: {video.get('share_count', 0)}",
            f"时间: {time_str}"
        ]))

    return save_path


def batch_download(videos, page, save_dir, label="视频"):
    """批量下载"""
    if not videos:
        print("无内容可下载")
        return

    print(f"\n开始下载{label}...\n")

    dl_file = os.path.join(save_dir, '_downloaded.json')
    downloaded = {}
    if os.path.exists(dl_file):
        with open(dl_file, 'r', encoding='utf-8') as f:
            downloaded = json.load(f)

    ok = 0
    fail = 0
    skip = 0

    for idx, video in enumerate(videos, 1):
        vid = video['aweme_id']
        print(f"[{idx}/{len(videos)}] {vid}", end="  ")

        if vid in downloaded:
            print(f"跳过")
            skip += 1
            continue

        try:
            result = download_aweme(video, page, save_dir)
            if result:
                ok += 1
                downloaded[vid] = os.path.basename(result)
                with open(dl_file, 'w', encoding='utf-8') as f:
                    json.dump(downloaded, f, ensure_ascii=False, indent=2)
            else:
                fail += 1
        except Exception as e:
            print(f"  失败: {e}")
            fail += 1

        time.sleep(0.3)

    print(f"\n{'='*60}")
    print(f"总计: {len(videos)} | 成功: {ok} | 跳过: {skip} | 失败: {fail}")
    print(f"保存: {save_dir}")
    print("=" * 60)


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else ['--help']
    command = args[0]

    print("=" * 60)
    print("       DyD 独立下载器 v4 - 作品+收藏")
    print("=" * 60)

    if command in ('--help', '-h'):
        print("""
用法:
  python dyd_download.py user <用户主页URL>       下载用户全部作品
  python dyd_download.py liked <用户主页URL>     下载用户喜欢的视频
  python dyd_download.py video <视频URL>         下载单个视频

示例:
  python dyd_download.py user "https://www.douyin.com/user/MS4wLjABAAAA..."
  python dyd_download.py liked "https://www.douyin.com/user/MS4wLjABAAAA..."
  python dyd_download.py video "https://www.douyin.com/video/7647801290189180169"
""")
        return

    from playwright.sync_api import sync_playwright

    print("连接Chrome...")
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page = context.pages[0]
    print(f"当前页面: {page.url}")

    try:
        user_url = args[1] if len(args) > 1 else ''

        if command == 'user':
            if not user_url:
                print("请提供用户主页URL")
                return
            videos = get_user_videos(user_url, page)

            # 保存列表
            save_path = os.path.join(SAVE_DIR, 'video_list_full.json')
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(videos, f, ensure_ascii=False, indent=2)
            print(f"列表已保存: {save_path}")

            batch_download(videos, page, SAVE_DIR, "作品")

        elif command == 'liked':
            if not user_url:
                print("请提供用户主页URL")
                return
            videos = get_liked_videos(user_url, page)

            # 保存列表
            save_path = os.path.join(SAVE_DIR, 'liked_list_full.json')
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(videos, f, ensure_ascii=False, indent=2)
            print(f"列表已保存: {save_path}")

            # 收藏视频保存到单独目录
            liked_dir = os.path.join(SAVE_DIR, '收藏')
            batch_download(videos, page, liked_dir, "喜欢")

        elif command == 'video':
            vid = args[1] if len(args) > 1 else ''
            m = re.search(r'video/(\d+)', vid)
            if m:
                vid = m.group(1)

            print(f"获取视频: {vid}")
            result = fetch_api(page,
                f'https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={vid}&aid=6383&cookie_enabled=true')

            if result and result.get('aweme_detail'):
                d = result['aweme_detail']
                video = extract_video(d)
                video['aweme_id'] = d.get('aweme_id', vid)
                download_aweme(video, page, SAVE_DIR)
                print("\n完成!")
            else:
                print("获取失败")

    finally:
        browser.close()
        pw.stop()


if __name__ == '__main__':
    main()
