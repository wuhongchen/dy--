"""
AI 分析引擎 - 支持 Ollama / OpenAI 兼容 API
"""
import os
import json
import httpx
import statistics
from collections import Counter
from datetime import datetime

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prompts')
REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'reports')


def load_prompt(name):
    """从 prompts/ 目录加载提示词模板"""
    path = os.path.join(PROMPTS_DIR, f'{name}.txt')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    return ''


def save_prompt(name, content):
    """保存提示词到 prompts/ 目录"""
    os.makedirs(PROMPTS_DIR, exist_ok=True)
    path = os.path.join(PROMPTS_DIR, f'{name}.txt')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)


DEFAULT_PROMPTS = {}


def _init_default_prompts():
    global DEFAULT_PROMPTS
    names = ['analyze_video', 'analyze_anchor', 'extract_knowledge', 'analyze_visual']
    for name in names:
        path = os.path.join(PROMPTS_DIR, f'{name}.txt')
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                DEFAULT_PROMPTS[name] = f.read()


_init_default_prompts()


def reset_prompt(name):
    """重置提示词为默认值"""
    if name in DEFAULT_PROMPTS:
        save_prompt(name, DEFAULT_PROMPTS[name])


class AnalyzerEngine:
    """AI 分析引擎"""

    def __init__(self, config=None):
        self.config = config or {}
        self.llm_provider = self.config.get('llm_provider', 'openai')
        self.api_url = self.config.get('llm_api_base', '')
        self.api_key = self.config.get('llm_api_key', '')
        self.model = self.config.get('llm_model', '')
        self.temperature = self.config.get('llm_temperature', 0.7)
        self.max_tokens = self.config.get('llm_max_tokens', 4096)

    def call_llm(self, prompt, system_prompt=''):
        """调用 LLM（OpenAI 兼容接口）"""
        return self._call_openai(prompt, system_prompt)

    def _call_openai(self, prompt, system_prompt=''):
        """调用 OpenAI 兼容 API"""
        url = f"{self.api_url}/chat/completions"
        messages = []
        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})
        messages.append({'role': 'user', 'content': prompt})

        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': self.temperature,
            'max_tokens': self.max_tokens,
        }

        headers = {}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        try:
            with httpx.Client(timeout=600) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                return data.get('choices', [{}])[0].get('message', {}).get('content', '')
        except Exception as e:
            return f"LLM 调用失败: {e}"

    def analyze_single_video(self, video_info, transcript, prompt_template=None, progress_callback=None):
        """单视频分析"""
        if progress_callback:
            progress_callback('正在分析视频...')

        if not prompt_template:
            prompt_template = load_prompt('analyze_video')

        prompt = prompt_template.format(
            title=video_info.get('desc', ''),
            author=video_info.get('author', ''),
            likes=video_info.get('digg_count', 0),
            comments=video_info.get('comment_count', 0),
            shares=video_info.get('share_count', 0),
            transcript=transcript[:15000],
        )

        result = self.call_llm(prompt, system_prompt='你是一个专业的抖音内容分析师，擅长分析视频内容和创作策略。')
        return result

    def analyze_anchor(self, videos_data, transcripts_text, prompt_template=None,
                       dimensions=None, progress_callback=None):
        """主播蒸馏分析"""
        if not dimensions:
            dimensions = list(range(1, 11))

        if not prompt_template:
            prompt_template = load_prompt('analyze_anchor')

        # 计算数据统计
        likes = [v.get('digg_count', 0) for v in videos_data]
        stats = {
            'total_videos': len(videos_data),
            'total_likes': sum(likes),
            'avg_likes': int(statistics.mean(likes)) if likes else 0,
            'max_likes': max(likes) if likes else 0,
            'min_likes': min(likes) if likes else 0,
        }

        # 内容类型分析
        content_types = self._classify_content_types(videos_data)
        content_types_text = '\n'.join([
            f"- {k}: {v['count']}个, 平均点赞 {v['avg_likes']}"
            for k, v in content_types.items()
        ])

        # 发布节奏分析
        rhythm = self._analyze_rhythm(videos_data)
        rhythm_text = '\n'.join([f"- {k}: {v}" for k, v in rhythm.items()])

        # 截断逐字稿
        truncated_transcripts = transcripts_text[:20000]

        prompt = prompt_template.format(
            total_videos=stats['total_videos'],
            total_likes=stats['total_likes'],
            avg_likes=stats['avg_likes'],
            max_likes=stats['max_likes'],
            min_likes=stats['min_likes'],
            content_types=content_types_text,
            publish_rhythm=rhythm_text,
            transcripts=truncated_transcripts,
        )

        system_prompt = '你是一个专业的抖音运营分析师，擅长数据分析和内容策略。请用Markdown格式输出分析报告。'

        # 逐维度分析
        results = {}
        dimension_names = {
            1: '01_整体表现概览', 2: '02_最佳最差视频分析',
            3: '03_内容类型分布', 4: '04_标题与封面策略',
            5: '05_发布节奏分析', 6: '06_账号定位分析',
            7: '07_运营方法论', 8: '08_爆款拆解',
            9: '09_可复制清单', 10: '10_知识库',
        }

        for dim in dimensions:
            if dim not in dimension_names:
                continue
            name = dimension_names[dim]
            if progress_callback:
                progress_callback(f"分析维度 {dim}/10: {name}")

            dim_prompt = f"{prompt}\n\n请重点分析第 {dim} 个维度：{name.split('_', 1)[1]}。"
            result = self.call_llm(dim_prompt, system_prompt=system_prompt)
            results[name] = result

        return results

    def extract_knowledge(self, transcript, prompt_template=None, progress_callback=None):
        """知识点提取"""
        if progress_callback:
            progress_callback('正在提取知识点...')

        if not prompt_template:
            prompt_template = load_prompt('extract_knowledge')

        prompt = prompt_template.format(transcript=transcript[:15000])
        result = self.call_llm(prompt, system_prompt='你是一个知识提炼专家，擅长从口语化内容中提取结构化知识。')
        return result

    def analyze_visual(self, video_info, cover_description='', frame_descriptions='',
                       prompt_template=None, progress_callback=None):
        """画面分析"""
        if progress_callback:
            progress_callback('正在分析画面...')

        if not prompt_template:
            prompt_template = load_prompt('analyze_visual')

        prompt = prompt_template.format(
            title=video_info.get('desc', ''),
            author=video_info.get('author', ''),
            cover_description=cover_description or '暂无封面描述',
            frame_descriptions=frame_descriptions or '暂无画面描述',
        )

        result = self.call_llm(prompt, system_prompt='你是一个视频画面分析师，擅长分析视频的视觉呈现和设计策略。')
        return result

    def _classify_content_types(self, videos):
        """内容类型分类"""
        type_keywords = {
            '教程类': ['教程', '教学', '怎么', '如何', '方法', '步骤', '技巧', '攻略'],
            '测评类': ['测评', '评测', '体验', '实测', '对比', '横评'],
            '推荐类': ['推荐', '安利', '种草', '分享', '好物'],
            '干货类': ['干货', '知识', '科普', '解析', '揭秘'],
            '故事类': ['故事', '经历', '记录', '日常', 'vlog'],
            '热点类': ['热点', '新闻', '事件', '热搜', '挑战'],
        }

        result = {}
        for vtype, keywords in type_keywords.items():
            count = 0
            total_likes = 0
            for v in videos:
                title = v.get('desc', '') or v.get('title', '')
                if any(kw in title for kw in keywords):
                    count += 1
                    total_likes += v.get('digg_count', 0)
            if count > 0:
                result[vtype] = {
                    'count': count,
                    'avg_likes': total_likes // count,
                }

        return result if result else {'其他': {'count': len(videos), 'avg_likes': 0}}

    def _analyze_rhythm(self, videos):
        """发布节奏分析"""
        times = []
        for v in videos:
            ct = v.get('create_time', 0)
            if ct:
                times.append(datetime.fromtimestamp(ct))

        if not times:
            return {'总跨度天数': 0}

        times.sort()
        hours = [t.hour for t in times]
        weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        weekday_counts = Counter(t.weekday() for t in times)
        hour_counts = Counter(hours)

        span_days = (times[-1] - times[0]).days if len(times) > 1 else 0
        avg_gap = span_days / max(len(times) - 1, 1)

        best_hour = hour_counts.most_common(1)[0][0] if hour_counts else 0
        best_weekday = weekdays[weekday_counts.most_common(1)[0][0]] if weekday_counts else '-'

        return {
            '总视频数': len(times),
            '总跨度天数': span_days,
            '平均发布间隔': f"{avg_gap:.1f}天",
            '最佳发布时段': f"{best_hour}:00",
            '最佳发布星期': best_weekday,
        }

    def save_report(self, content, filename, subdir=''):
        """保存报告为 MD 文件"""
        report_dir = os.path.join(REPORTS_DIR, subdir) if subdir else REPORTS_DIR
        os.makedirs(report_dir, exist_ok=True)
        path = os.path.join(report_dir, filename)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path
