"""
SQLite 数据库管理
"""
import os
import sqlite3
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'dyd.db')


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aweme_id TEXT UNIQUE,
            title TEXT,
            author TEXT,
            digg_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            share_count INTEGER DEFAULT 0,
            create_time INTEGER DEFAULT 0,
            video_path TEXT,
            cover_path TEXT,
            info_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aweme_id TEXT UNIQUE,
            text_content TEXT,
            srt_path TEXT,
            duration REAL DEFAULT 0,
            word_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT UNIQUE,
            task_type TEXT,
            url TEXT,
            status TEXT DEFAULT 'pending',
            progress INTEGER DEFAULT 0,
            progress_step TEXT DEFAULT '',
            result_summary TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            aweme_id TEXT,
            analysis_type TEXT,
            prompt_used TEXT,
            result_path TEXT,
            result_preview TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# ---- Downloads CRUD ----

def save_download(data):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO downloads
            (aweme_id, title, author, digg_count, comment_count, share_count,
             create_time, video_path, cover_path, info_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get('aweme_id', ''),
            data.get('title', ''),
            data.get('author', ''),
            data.get('digg_count', 0),
            data.get('comment_count', 0),
            data.get('share_count', 0),
            data.get('create_time', 0),
            data.get('video_path', ''),
            data.get('cover_path', ''),
            data.get('info_path', ''),
        ))
        conn.commit()
    finally:
        conn.close()


def get_downloads(keyword=None):
    conn = get_conn()
    try:
        if keyword:
            rows = conn.execute(
                "SELECT * FROM downloads WHERE title LIKE ? OR author LIKE ? ORDER BY created_at DESC",
                (f'%{keyword}%', f'%{keyword}%')
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM downloads ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_download_by_aweme_id(aweme_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM downloads WHERE aweme_id = ?", (aweme_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_download(aweme_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM downloads WHERE aweme_id = ?", (aweme_id,))
        conn.commit()
    finally:
        conn.close()


# ---- Transcripts CRUD ----

def save_transcript(data):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO transcripts
            (aweme_id, text_content, srt_path, duration, word_count)
            VALUES (?, ?, ?, ?, ?)
        """, (
            data.get('aweme_id', ''),
            data.get('text_content', ''),
            data.get('srt_path', ''),
            data.get('duration', 0),
            data.get('word_count', 0),
        ))
        conn.commit()
    finally:
        conn.close()


def get_transcript(aweme_id):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM transcripts WHERE aweme_id = ?", (aweme_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_transcripts():
    conn = get_conn()
    try:
        rows = conn.execute("""
            SELECT t.*, d.title, d.author
            FROM transcripts t
            LEFT JOIN downloads d ON t.aweme_id = d.aweme_id
            ORDER BY t.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---- Tasks CRUD ----

def save_task(data):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO tasks
            (task_id, task_type, url, status, progress, progress_step, result_summary)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get('task_id', ''),
            data.get('task_type', ''),
            data.get('url', ''),
            data.get('status', 'pending'),
            data.get('progress', 0),
            data.get('progress_step', ''),
            data.get('result_summary', ''),
        ))
        conn.commit()
    finally:
        conn.close()


def update_task(task_id, **kwargs):
    conn = get_conn()
    try:
        sets = ', '.join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [task_id]
        conn.execute(f"UPDATE tasks SET {sets} WHERE task_id = ?", values)
        conn.commit()
    finally:
        conn.close()


# ---- Analysis History CRUD ----

def save_analysis(data):
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO analysis_history
            (aweme_id, analysis_type, prompt_used, result_path, result_preview)
            VALUES (?, ?, ?, ?, ?)
        """, (
            data.get('aweme_id', ''),
            data.get('analysis_type', ''),
            data.get('prompt_used', ''),
            data.get('result_path', ''),
            data.get('result_preview', ''),
        ))
        conn.commit()
    finally:
        conn.close()


def get_analysis_history(aweme_id=None):
    conn = get_conn()
    try:
        if aweme_id:
            rows = conn.execute(
                "SELECT * FROM analysis_history WHERE aweme_id = ? ORDER BY created_at DESC",
                (aweme_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM analysis_history ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# Initialize on import
init_db()
