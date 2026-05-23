B站视频/音频下载。使用 yt-dlp 从 Bilibili 下载视频或音频，自动从 Chrome 获取登录 cookie 以获取最高画质。

用户输入: $ARGUMENTS

## 执行步骤

1. 从用户输入中解析参数：视频URL（必填）、分辨率（可选，如 720p/1080p）、输出目录（可选）、是否仅下载音频
2. 验证 URL 是否为B站链接（bilibili.com/video/、b23.tv/、bilibili.com/bangumi/）
3. 判断是否为音频下载模式：当用户提到"音频"、"音乐"、"mp3"、"aac"、"只要声音"等关键词时，加上 `--audio-only` 参数
4. 判断是否需要调整音量：当用户提到"音量"、"声音大/小"、"加大音量"等关键词时，加上 `--volume N` 参数（N 为倍数，如 1.5 = 150%，2 = 200%）
5. 执行下载脚本：
   - 视频模式：`python3 .claude/commands/scripts/bilibili_downloader.py "<URL>" "<分辨率>" "<输出目录>"`
   - 音频模式：`python3 .claude/commands/scripts/bilibili_downloader.py "<URL>" --audio-only`
   - 调整音量：在上述命令后追加 `--volume N`（如 `--volume 2`）
5. 将下载结果路径告知用户

## 注意事项

- 仅支持B站链接，非B站链接直接拒绝
- 脚本始终携带 Chrome cookie，自动获取最高画质
- 默认下载最高可用画质，可通过参数限制分辨率
- 音频模式输出 AAC 格式（.m4a），仅提取音轨不含视频
- `--volume N` 可调整音量倍数（1.5 = 加大50%，2 = 翻倍），视频和音频模式均可用
- 默认下载位置：macOS → ~/Downloads，Windows → E:\bilibili-downloader
- B站视频通常音视频分离（DASH），脚本会自动合并
- 依赖：yt-dlp、ffmpeg（需提前安装：brew install yt-dlp ffmpeg）
