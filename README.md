# 视频处理API

这是一个基于FastAPI的视频处理服务，提供以下功能：

1. 将图片转换为视频
2. 将图片和音频合成为视频
3. 拼接多个视频

## 安装依赖

```bash
pip install -r requirements.txt
```

## 运行服务

```bash
python main.py
```

服务将在 http://localhost:8000 启动

## API 接口说明

### 1. 图片转视频
- 端点：`POST /image-to-video`
- 请求体：
```json
{
    "image_url": "图片路径",
    "duration": 视频时长（秒）
}
```

### 2. 图片音频合成视频
- 端点：`POST /image-audio-to-video`
- 请求体：
```json
{
    "image_url": "图片路径",
    "audio_url": "音频路径"
}
```

### 3. 视频拼接
- 端点：`POST /concatenate-videos`
- 请求体：
```json
{
    "video_urls": ["视频1路径", "视频2路径", ...]
}
```

## 注意事项

1. 所有输入文件路径需要是服务器可访问的路径
2. 输出视频将保存在 `output` 目录下
3. 建议在生产环境中添加文件清理机制 