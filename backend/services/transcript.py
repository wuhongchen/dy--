"""
逐字稿提取引擎 - 基于 faster-whisper + ffmpeg
"""
import os
import subprocess
import json
from pathlib import Path


class SpeechRecognizer:
    """语音识别引擎"""

    def __init__(self, model_size='base', device='cpu', compute_type='int8', language='zh'):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.model = None

    def load_model(self):
        """懒加载 Whisper 模型"""
        if self.model is not None:
            return

        # 设置 HuggingFace 缓存路径
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'models', 'whisper')
        os.makedirs(data_dir, exist_ok=True)
        os.environ['HF_HOME'] = data_dir

        from faster_whisper import WhisperModel
        self.model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
            download_root=data_dir
        )

    def extract_audio(self, video_path, audio_path):
        """用 ffmpeg 从视频中提取音频（16kHz WAV）"""
        try:
            subprocess.run([
                'ffmpeg', '-i', video_path,
                '-ar', '16000', '-ac', '1', '-f', 'wav',
                '-y', audio_path
            ], capture_output=True, check=True, timeout=120)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def transcribe_video(self, video_path, output_dir, progress_callback=None):
        """转写单个视频，返回结果字典"""
        self.load_model()

        aweme_id = Path(video_path).stem
        os.makedirs(output_dir, exist_ok=True)

        txt_path = os.path.join(output_dir, f"{aweme_id}.txt")
        srt_path = os.path.join(output_dir, f"{aweme_id}.srt")

        # 如果已有逐字稿，跳过
        if os.path.exists(txt_path) and os.path.getsize(txt_path) > 10:
            with open(txt_path, 'r', encoding='utf-8') as f:
                text = f.read()
            return {
                'text': text,
                'segments': [],
                'duration': 0,
                'txt_path': txt_path,
                'srt_path': srt_path if os.path.exists(srt_path) else '',
                'word_count': len(text),
            }

        if progress_callback:
            progress_callback('提取音频...')

        # 提取音频
        audio_path = os.path.join(output_dir, f"{aweme_id}_audio.wav")
        if not self.extract_audio(video_path, audio_path):
            return None

        if progress_callback:
            progress_callback('语音识别中...')

        # Whisper 转写
        segments_iter, info = self.model.transcribe(
            audio_path,
            language=self.language if self.language != 'auto' else None,
            vad_filter=True,
        )

        segments = []
        full_text_parts = []
        for seg in segments_iter:
            segments.append({
                'start': seg.start,
                'end': seg.end,
                'text': seg.text.strip(),
            })
            full_text_parts.append(seg.text.strip())

        full_text = '\n'.join(full_text_parts)
        duration = info.duration if info else 0

        # 保存 TXT
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(full_text)

        # 保存 SRT
        with open(srt_path, 'w', encoding='utf-8') as f:
            for i, seg in enumerate(segments, 1):
                start = self._format_srt_time(seg['start'])
                end = self._format_srt_time(seg['end'])
                f.write(f"{i}\n{start} --> {end}\n{seg['text']}\n\n")

        # 清理临时音频
        try:
            os.remove(audio_path)
        except:
            pass

        return {
            'text': full_text,
            'segments': segments,
            'duration': duration,
            'txt_path': txt_path,
            'srt_path': srt_path,
            'word_count': len(full_text),
        }

    def batch_transcribe(self, video_dir, output_dir, progress_callback=None):
        """批量转写目录下所有视频"""
        results = []
        video_exts = ('.mp4', '.mov', '.avi', '.mkv')

        # 扫描所有视频文件
        videos = []
        for root, dirs, files in os.walk(video_dir):
            for f in files:
                if any(f.lower().endswith(ext) for ext in video_exts):
                    videos.append(os.path.join(root, f))

        total = len(videos)
        for idx, video_path in enumerate(videos):
            if progress_callback:
                progress_callback(f"转写 {idx+1}/{total}: {os.path.basename(video_path)}")
            result = self.transcribe_video(video_path, output_dir)
            if result:
                result['video_path'] = video_path
                results.append(result)

        return results

    @staticmethod
    def _format_srt_time(seconds):
        """秒数转 SRT 时间格式"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def merge_transcripts(transcript_dir, videos_data, output_path):
        """合并所有逐字稿为一个文件"""
        parts = []
        for video in videos_data:
            aweme_id = video.get('aweme_id', '')
            txt_path = os.path.join(transcript_dir, f"{aweme_id}.txt")
            if os.path.exists(txt_path):
                with open(txt_path, 'r', encoding='utf-8') as f:
                    text = f.read().strip()
                title = video.get('desc', video.get('title', ''))
                author = video.get('author', '')
                parts.append(f"## {title}\n作者: {author}\n\n{text}\n")

        full_text = '\n---\n\n'.join(parts)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(full_text)
        return full_text
