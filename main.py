from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import os
from moviepy.editor import ImageClip, AudioFileClip, VideoFileClip, concatenate_videoclips
import uuid
import shutil
import subprocess
import tempfile
import oss2
import time
import datetime
import requests
from PIL import Image
import io

app = FastAPI(title="视频处理API")

# 创建输出目录
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 创建静态文件目录
STATIC_DIR = "static"
os.makedirs(os.path.join(STATIC_DIR, "videos"), exist_ok=True)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def get_date_directory():
    """获取当前日期的目录路径"""
    today = datetime.datetime.now()
    year_month = today.strftime("%Y-%m")
    day = today.strftime("%d")
    return year_month, day

def upload_local_file(file_path):
    """上传文件到本地目录并返回可访问的URL"""
    # 获取日期目录
    year_month, day = get_date_directory()
    date_dir = os.path.join(STATIC_DIR, "videos", year_month, day)

    # 确保目录存在
    os.makedirs(date_dir, exist_ok=True)

    output_filename = os.path.basename(file_path)
    destination = os.path.join(date_dir, output_filename)

    # 复制文件
    shutil.copy(file_path, destination)
    print(f"文件已保存到本地目录: {destination}")

    # 返回可访问的URL（相对URL）
    relative_url = f"/static/videos/{year_month}/{day}/{output_filename}"
    # 添加host前缀
    url = f"http://video-api.fyshark.com{relative_url}"
    print(f"生成的文件URL: {url}")

    # 返回绝对文件路径
    abs_path = os.path.abspath(destination)
    print(f"文件的绝对路径: {abs_path}")

    return url

def upload_to_oss(file_path: str) -> str:
    """使用本地存储替代OSS上传"""
    print(f"使用本地存储: {file_path}")
    return upload_local_file(file_path)

def convert_audio_format(input_path, volume_db: float = 0):
    """将音频转换为WAV格式并调整音量，并保存到静态文件目录"""
    temp_dir = tempfile.gettempdir()
    temp_output_filename = f"{uuid.uuid4()}.wav"
    temp_output_path = os.path.join(temp_dir, temp_output_filename)

    try:
        # 使用ffmpeg转换音频格式并调整音量
        command = [
            'ffmpeg', '-i', input_path,
            '-acodec', 'pcm_s16le',  # 使用PCM编码
            '-ar', '44100',          # 设置采样率
            '-ac', '2',              # 设置声道数
            '-af', f'volume={10**(volume_db/20)}',  # 调整音量
            '-y',                    # 覆盖已存在的文件
            temp_output_path
        ]
        subprocess.run(command, check=True, capture_output=True)

        # 获取年月日目录
        year_month, day = get_date_directory()
        static_audio_dir = os.path.join(STATIC_DIR, "videos", year_month, day)
        os.makedirs(static_audio_dir, exist_ok=True)

        # 复制文件到静态目录
        final_filename = f"{uuid.uuid4()}.wav"
        final_path = os.path.join(static_audio_dir, final_filename)
        shutil.copy(temp_output_path, final_path)

        # 清理临时文件
        os.remove(temp_output_path)

        print(f"音频文件已保存到: {final_path}")
        return final_path
    except subprocess.CalledProcessError as e:
        # 清理临时文件
        if os.path.exists(temp_output_path):
            os.remove(temp_output_path)
        raise Exception(f"音频转换失败: {str(e)}")
    except Exception as e:
        # 清理临时文件
        if os.path.exists(temp_output_path):
            os.remove(temp_output_path)
        raise Exception(f"音频处理失败: {str(e)}")

class ImageToVideoRequest(BaseModel):
    image_url: str
    duration: float

class ImageAudioToVideoRequest(BaseModel):
    image_url: str
    audio_url: str
    volume_db: float = Field(
        default=0,
        description="音量调整值（分贝），正值增加音量，负值降低音量",
        ge=-20,
        le=20
    )

class ConcatenateVideosRequest(BaseModel):
    video_urls: List[str]
    volume_db: Optional[float] = Field(
        default=0,
        description="音量调整值（分贝），正值增加音量，负值降低音量",
        ge=-20,
        le=20
    )

class VideoResponse(BaseModel):
    video_url: str
    duration: float

def get_video_info(file_path: str) -> Dict[str, Any]:
    """获取视频信息，包括时长等"""
    try:
        video = VideoFileClip(file_path)
        info = {
            "duration": round(video.duration, 2),  # 视频时长（秒）
            "size": os.path.getsize(file_path),    # 文件大小（字节）
            "fps": video.fps if hasattr(video, 'fps') else None,  # 帧率
            "width": video.w if hasattr(video, 'w') else None,    # 宽度
            "height": video.h if hasattr(video, 'h') else None    # 高度
        }
        video.close()  # 关闭视频文件
        return info
    except Exception as e:
        print(f"获取视频信息失败: {str(e)}")
        return {"duration": 0, "error": str(e)}

@app.post("/image-to-video", response_model=VideoResponse)
async def image_to_video(request: ImageToVideoRequest):
    # 生成唯一输出文件名
    output_filename = f"{uuid.uuid4()}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    temp_image_path = None
    try:
        # 处理URL图像：如果是远程URL，先下载到本地临时文件
        if request.image_url.startswith(('http://', 'https://')):
            try:
                print(f"下载图像: {request.image_url}")

                # 使用requests下载图片
                response = requests.get(request.image_url, timeout=30)
                if response.status_code == 200:
                    img = Image.open(io.BytesIO(response.content))
                    temp_image_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.jpg")
                    img.save(temp_image_path)
                    print(f"图像已下载到: {temp_image_path}")
                    image_path = temp_image_path
                else:
                    raise HTTPException(status_code=400, detail=f"无法下载图像，状态码: {response.status_code}")
            except Exception as e:
                if temp_image_path and os.path.exists(temp_image_path):
                    os.remove(temp_image_path)
                raise HTTPException(status_code=400, detail=f"处理图像URL时出错: {str(e)}")
        else:
            # 本地文件路径
            image_path = request.image_url

        # 创建视频
        clip = ImageClip(image_path).set_duration(request.duration)
        clip.write_videofile(output_path, fps=24, codec='libx264', audio_codec='aac')

        # 清理临时图像文件
        if temp_image_path and os.path.exists(temp_image_path):
            os.remove(temp_image_path)

        # 获取视频信息
        video_info = get_video_info(output_path)

        # 上传到本地存储
        video_url = upload_to_oss(output_path)

        # 清理本地文件
        os.remove(output_path)

        return {"video_url": video_url, "duration": video_info["duration"]}
    except Exception as e:
        # 确保清理所有临时文件
        if temp_image_path and os.path.exists(temp_image_path):
            os.remove(temp_image_path)
        if os.path.exists(output_path):
            os.remove(output_path)

        raise HTTPException(status_code=500, detail=f"创建视频失败: {str(e)}")

@app.post("/image-audio-to-video", response_model=VideoResponse)
async def image_audio_to_video(request: ImageAudioToVideoRequest):
    # 生成唯一输出文件名
    output_filename = f"{uuid.uuid4()}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    temp_image_path = None
    temp_audio_path = None

    try:
        # 处理URL图像：如果是远程URL，先下载到本地临时文件
        if request.image_url.startswith(('http://', 'https://')):
            try:
                print(f"下载图像: {request.image_url}")
                # 使用requests下载图片
                response = requests.get(request.image_url, timeout=30)
                if response.status_code == 200:
                    img = Image.open(io.BytesIO(response.content))
                    temp_image_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.jpg")
                    img.save(temp_image_path)
                    print(f"图像已下载到: {temp_image_path}")
                    image_path = temp_image_path
                else:
                    raise HTTPException(status_code=400, detail=f"无法下载图像，状态码: {response.status_code}")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"处理图像URL时出错: {str(e)}")
        else:
            # 本地文件路径
            image_path = request.image_url

        # 处理URL音频：如果是远程URL，先下载到本地临时文件
        if request.audio_url.startswith(('http://', 'https://')):
            try:
                print(f"下载音频: {request.audio_url}")
                temp_audio_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.mp3")
                # 使用requests下载音频
                response = requests.get(request.audio_url, timeout=30)
                if response.status_code == 200:
                    with open(temp_audio_path, 'wb') as f:
                        f.write(response.content)
                    print(f"音频已下载到: {temp_audio_path}")
                    audio_path = temp_audio_path
                else:
                    raise HTTPException(status_code=400, detail=f"无法下载音频，状态码: {response.status_code}")
            except Exception as e:
                # 清理临时文件
                if temp_image_path and os.path.exists(temp_image_path):
                    os.remove(temp_image_path)
                raise HTTPException(status_code=400, detail=f"处理音频URL时出错: {str(e)}")
        else:
            # 本地文件路径
            audio_path = request.audio_url

        # 转换音频格式并调整音量
        converted_audio_path = convert_audio_format(audio_path, request.volume_db)

        # 加载音频
        audio = AudioFileClip(converted_audio_path)

        # 创建视频
        clip = ImageClip(image_path).set_duration(audio.duration)
        clip = clip.set_audio(audio)

        # 使用更明确的编码器设置
        clip.write_videofile(
            output_path,
            fps=24,
            codec='libx264',
            audio_codec='aac',
            audio_bitrate='192k'
        )

        # 清理所有临时文件
        if temp_image_path and os.path.exists(temp_image_path):
            os.remove(temp_image_path)
        if temp_audio_path and os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)

        # 获取视频信息
        video_info = get_video_info(output_path)

        # 上传到本地存储
        video_url = upload_to_oss(output_path)

        # 清理本地文件
        os.remove(output_path)
        # 清理转换后的音频文件（如果存在且是临时文件）
        if os.path.exists(converted_audio_path) and converted_audio_path.startswith(tempfile.gettempdir()):
            os.remove(converted_audio_path)

        return {"video_url": video_url, "duration": video_info["duration"]}
    except Exception as e:
        # 清理所有临时文件
        if temp_image_path and os.path.exists(temp_image_path):
            os.remove(temp_image_path)
        if temp_audio_path and os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
        if os.path.exists(output_path):
            os.remove(output_path)

        raise HTTPException(status_code=500, detail=f"创建视频失败: {str(e)}")

@app.post("/concatenate-videos", response_model=VideoResponse)
async def concatenate_videos(request: ConcatenateVideosRequest):
    # 生成唯一输出文件名
    output_filename = f"{uuid.uuid4()}.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    # 临时文件列表，用于清理
    temp_files = []

    try:
        # 加载所有视频，如果是远程URL则先下载到本地
        clips = []
        for url in request.video_urls:
            local_video_path = None

            # 检查是否为远程URL
            if url.startswith(('http://', 'https://')):
                try:
                    # 创建临时文件下载路径
                    local_video_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}.mp4")

                    # 下载视频文件
                    print(f"下载视频: {url}")
                    response = requests.get(url, stream=True, timeout=30)
                    if response.status_code == 200:
                        with open(local_video_path, 'wb') as f:
                            for chunk in response.iter_content(chunk_size=1024*1024):
                                if chunk:  # 过滤掉保持连接的空数据块
                                    f.write(chunk)
                        print(f"视频已下载到: {local_video_path}")
                        temp_files.append(local_video_path)
                        video_path = local_video_path
                    else:
                        raise HTTPException(status_code=400, detail=f"无法下载视频，状态码: {response.status_code}")
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"处理视频URL时出错: {str(e)}")
            else:
                # 本地文件路径
                video_path = url

            # 加载视频
            clip = VideoFileClip(video_path)
            if request.volume_db != 0:
                # 调整音频音量
                clip = clip.volumex(10**(request.volume_db/20))
            clips.append(clip)

        if not clips:
            raise HTTPException(status_code=400, detail="没有有效的视频文件可合并")

        # 拼接视频
        final_clip = concatenate_videoclips(clips)
        final_clip.write_videofile(
            output_path,
            codec='libx264',
            audio_codec='aac',
            audio_bitrate='192k'
        )

        # 获取视频信息
        video_info = get_video_info(output_path)

        # 上传到本地存储
        video_url = upload_to_oss(output_path)

        # 清理所有临时文件
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)

        # 清理输出文件
        if os.path.exists(output_path):
            os.remove(output_path)

        return {"video_url": video_url, "duration": video_info["duration"]}
    except Exception as e:
        # 清理所有临时文件
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)

        # 清理输出文件
        if os.path.exists(output_path):
            os.remove(output_path)

        raise HTTPException(status_code=500, detail=f"合并视频失败: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=80)