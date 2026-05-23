#!/usr/bin/env python3
"""
Bilibili Video Downloader

用法：
  python video_downloader.py <B站视频URL> [分辨率] [输出目录] [--audio-only]

示例：
  python video_downloader.py "https://www.bilibili.com/video/BV1xx" "720p" "./downloads"
  python video_downloader.py "https://www.bilibili.com/video/BV1xx" --audio-only
"""

import os
import sys
import json
import subprocess
import tempfile
import re
import math
import platform


def get_default_output_dir():
    if platform.system() == 'Darwin':
        return os.path.expanduser('~/Downloads')
    elif platform.system() == 'Windows':
        return r'E:\bilibili-downloader'
    else:
        return os.path.expanduser('~/Downloads')

def sanitize_filename(name, max_len=50):
    name = os.path.basename(name)
    name = re.sub(r'[^\w\u4e00-\u9fff\-\.]', '_', name)
    if len(name) > max_len:
        base, ext = os.path.splitext(name)
        base = base[:max_len - len(ext)]
        name = base + ext
    return name

def has_audio_stream(filepath):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', filepath],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() != ''
    except:
        return False

def has_video_stream(filepath):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', filepath],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() != ''
    except:
        return False

def merge_audio_video_if_needed(video_path, output_dir):
    if has_video_stream(video_path) and has_audio_stream(video_path):
        return video_path
    print('检测到音视频分离，尝试合并...')
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    dir_path = os.path.dirname(video_path)
    candidates = [os.path.join(dir_path, f) for f in os.listdir(dir_path) if f.startswith(base_name) and f != os.path.basename(video_path)]
    if len(candidates) == 1:
        other_file = candidates[0]
        v_has = has_video_stream(video_path)
        a_has = has_audio_stream(video_path)
        o_has_a = has_audio_stream(other_file)
        o_has_v = has_video_stream(other_file)
        if (v_has and o_has_a) or (a_has and o_has_v):
            merged_path = os.path.join(output_dir, f'{base_name}_merged.mp4')
            cmd = ['ffmpeg', '-i', video_path, '-i', other_file, '-c:v', 'copy', '-c:a', 'aac', '-map', '0:v:0', '-map', '1:a:0', merged_path]
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=120)
                if os.path.exists(merged_path):
                    print(f'合并完成: {merged_path}')
                    return merged_path
            except subprocess.CalledProcessError as e:
                print(f'合并失败: {e}')
    return video_path

def adjust_volume(filepath, volume):
    """用 ffmpeg 调整音量，volume 为倍数（如 1.5 = 150%）"""
    print(f'调整音量: {volume}x ...')
    base, ext = os.path.splitext(filepath)
    tmp_path = f'{base}_vol{ext}'
    cmd = ['ffmpeg', '-y', '-i', filepath, '-af', f'volume={volume}', '-c:v', 'copy', tmp_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        os.replace(tmp_path, filepath)
        print(f'音量调整完成: {volume}x')
    except subprocess.CalledProcessError as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        print(f'音量调整失败: {e.stderr}')
    return filepath

def validate_bilibili_url(url):
    """检查是否为B站链接"""
    patterns = [
        r'bilibili\.com/video/',
        r'b23\.tv/',
        r'bilibili\.com/bangumi/',
    ]
    return any(re.search(p, url) for p in patterns)

def download_audio(url, output_dir=None):
    """仅下载音频，输出 AAC (.m4a) 文件"""
    if not validate_bilibili_url(url):
        raise Exception(f'仅支持B站链接，当前URL不是B站视频: {url}')
    if output_dir is None:
        output_dir = get_default_output_dir()

    print('尝试使用 Chrome cookie 下载音频...')
    print('仅提取音频，输出 AAC 格式')
    template = os.path.join(output_dir, '%(title)s.%(ext)s')
    download_cmd = [
        'yt-dlp',
        '-f', 'bestaudio',
        '--extract-audio',
        '--audio-format', 'aac',
        '--output', template,
        '--restrict-filenames',
        '--no-warnings',
        '--cookies-from-browser', 'chrome',
        url,
    ]
    try:
        subprocess.run(download_cmd, check=True, timeout=600, capture_output=True, text=True, encoding='utf-8')
    except subprocess.CalledProcessError as e:
        raise Exception(f'下载失败: {e.stderr}')
    files = [f for f in os.listdir(output_dir) if f.endswith(('.m4a', '.aac'))]
    if not files:
        raise Exception('未找到下载的音频文件')
    audio_path = os.path.join(output_dir, files[0])
    safe_name = sanitize_filename(files[0])
    if safe_name != files[0]:
        new_path = os.path.join(output_dir, safe_name)
        os.rename(audio_path, new_path)
        audio_path = new_path
    size_mb = os.path.getsize(audio_path) / (1024*1024)
    print(f'下载完成: {audio_path} ({size_mb:.1f} MB)')
    return audio_path

def download_video(url, output_dir=None, resolution=None):
    """下载B站视频，返回文件路径"""
    if not validate_bilibili_url(url):
        raise Exception(f'仅支持B站链接，当前URL不是B站视频: {url}')
    if output_dir is None:
        output_dir = get_default_output_dir()

    print('尝试使用 Chrome cookie 获取最高画质...')

    is_mac = platform.system() == 'Darwin'
    codec_pref = '[vcodec~="^(avc|h264)"]' if is_mac else ''
    merge_fmt = 'mov' if is_mac else 'mp4'

    if resolution:
        target_height = int(resolution.rstrip('p'))
        format_spec = f'bestvideo[height<={target_height}]{codec_pref}+bestaudio/bestvideo[height<={target_height}]+bestaudio/best[height<={target_height}]'
        print(f'目标分辨率: {resolution}')
    else:
        format_spec = f'bestvideo{codec_pref}+bestaudio/bestvideo+bestaudio/best'
        print('选择最高可用画质')
    print('开始下载...')
    template = os.path.join(output_dir, '%(title)s.%(ext)s')
    download_cmd = [
        'yt-dlp',
        '-f', format_spec,
        '--output', template,
        '--merge-output-format', merge_fmt,
        '--restrict-filenames',
        '--no-warnings',
        '--cookies-from-browser', 'chrome',
    ]
    download_cmd.append(url)
    try:
        subprocess.run(download_cmd, check=True, timeout=600, capture_output=True, text=True, encoding='utf-8')
    except subprocess.CalledProcessError as e:
        raise Exception(f'下载失败: {e.stderr}')
    files = [f for f in os.listdir(output_dir) if f.endswith(('.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4a'))]
    if not files:
        raise Exception('未找到下载的视频文件')
    video_path = os.path.join(output_dir, files[0])
    safe_name = sanitize_filename(files[0])
    if safe_name != files[0]:
        new_path = os.path.join(output_dir, safe_name)
        os.rename(video_path, new_path)
        video_path = new_path
    video_path = merge_audio_video_if_needed(video_path, output_dir)
    size_mb = os.path.getsize(video_path) / (1024*1024)
    print(f'下载完成: {video_path} ({size_mb:.1f} MB)')
    return video_path

def main():
    if len(sys.argv) < 2:
        print('用法: python video_downloader.py <B站视频URL> [分辨率] [输出目录] [--audio-only] [--volume N]')
        print('示例: python video_downloader.py "https://www.bilibili.com/video/BV1xx" "1080p" "./downloads"')
        print('      python video_downloader.py "https://www.bilibili.com/video/BV1xx" --audio-only --volume 2')
        sys.exit(1)
    audio_only = '--audio-only' in sys.argv
    volume = 1.5
    raw = sys.argv[1:]
    if '--volume' in raw:
        vi = raw.index('--volume')
        volume = float(raw[vi + 1])
        raw = raw[:vi] + raw[vi + 2:]
    args = [a for a in raw if a != '--audio-only']
    url = args[0]
    resolution = args[1] if len(args) > 1 else None
    output_dir = args[2] if len(args) > 2 else None
    if output_dir and not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    try:
        if audio_only:
            result = download_audio(url, output_dir)
        else:
            result = download_video(url, output_dir, resolution)
        if volume:
            result = adjust_volume(result, volume)
        print(f'\n最终文件: {result}')
    except Exception as e:
        print(f'❌ 出错: {e}')
        sys.exit(1)

if __name__ == '__main__':
    main()
