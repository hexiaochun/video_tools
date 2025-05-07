from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import List, Optional
import os
from moviepy.editor import ImageClip, AudioFileClip, VideoFileClip, concatenate_videoclips
import uuid
import shutil
import subprocess
import tempfile
import oss2
import time
import datetime

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
    
    # 返回可访问的URL
    url = f"/static/videos/{year_month}/{day}/{output_filename}"
    print(f"生成的文件URL: {url}")
    return url

def upload_to_oss(file_path: str) -> str:
    """使用本地存储替代OSS上传"""
    print(f"使用本地存储: {file_path}")
    return upload_local_file(file_path)

def convert_audio_format(input_path, volume_db: float = 0):
    """将音频转换为WAV格式并调整音量，上传到OSS后返回URL"""
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, f"{uuid.uuid4()}.wav")
    
    try:
        # 使用ffmpeg转换音频格式并调整音量
        command = [
            'ffmpeg', '-i', input_path,
            '-acodec', 'pcm_s16le',  # 使用PCM编码
            '-ar', '44100',          # 设置采样率
            '-ac', '2',              # 设置声道数
            '-af', f'volume={10**(volume_db/20)}',  # 调整音量
            '-y',                    # 覆盖已存在的文件
            output_path
        ]
        subprocess.run(command, check=True, capture_output=True)
        
        # 上传到本地
        audio_url = upload_local_file(output_path)
        
        # 清理本地临时文件
        os.remove(output_path)
        
        return audio_url
    except subprocess.CalledProcessError as e:
        raise Exception(f"音频转换失败: {str(e)}")

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

@app.post("/image-to-video")
async def image_to_video(request: ImageToVideoRequest):
    
        # 生成唯一输出文件名
        output_filename = f"{uuid.uuid4()}.mp4"
        output_path = os.path.join(OUTPUT_DIR, output_filename)
        
        # 创建视频
        clip = ImageClip(request.image_url).set_duration(request.duration)
        clip.write_videofile(output_path, fps=24, codec='libx264', audio_codec='aac')
        
        # 上传到OSS
        video_url = upload_to_oss(output_path)
        
        # 清理本地文件
        os.remove(output_path)
        
        return {"video_url": video_url}
    

@app.post("/image-audio-to-video")
async def image_audio_to_video(request: ImageAudioToVideoRequest):
    try:
        # 生成唯一输出文件名
        output_filename = f"{uuid.uuid4()}.mp4"
        output_path = os.path.join(OUTPUT_DIR, output_filename)
        
        # 转换音频格式并调整音量
        converted_audio_url = convert_audio_format(request.audio_url, request.volume_db)
        
        try:
            # 加载音频
            audio = AudioFileClip(converted_audio_url)
            
            # 创建视频
            clip = ImageClip(request.image_url).set_duration(audio.duration)
            clip = clip.set_audio(audio)
            
            # 使用更明确的编码器设置
            clip.write_videofile(
                output_path,
                fps=24,
                codec='libx264',
                audio_codec='aac',
                audio_bitrate='192k'
            )
            
            # 上传到OSS
            video_url = upload_to_oss(output_path)
            
            # 清理本地文件
            os.remove(output_path)
            
            return {"video_url": video_url}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/concatenate-videos")
async def concatenate_videos(request: ConcatenateVideosRequest):
    try:
        # 生成唯一输出文件名
        output_filename = f"{uuid.uuid4()}.mp4"
        output_path = os.path.join(OUTPUT_DIR, output_filename)
        
        # 加载所有视频
        clips = []
        for url in request.video_urls:
            clip = VideoFileClip(url)
            if request.volume_db != 0:
                # 调整音频音量
                clip = clip.volumex(10**(request.volume_db/20))
            clips.append(clip)
        
        # 拼接视频
        final_clip = concatenate_videoclips(clips)
        final_clip.write_videofile(
            output_path,
            codec='libx264',
            audio_codec='aac',
            audio_bitrate='192k'
        )
        
        # 上传到OSS
        video_url = upload_to_oss(output_path)
        
        # 清理本地文件
        os.remove(output_path)
        
        return {"video_url": video_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000) 