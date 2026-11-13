import os
import json
import random
import urllib.parse
import textwrap
import requests
import subprocess
import cloudinary
import cloudinary.uploader

# 1. Load payload sent from Google Sheets
payload = json.loads(os.environ.get("PAYLOAD_JSON", "{}"))
row_id = payload.get("row_id", "1")
title = payload.get("title", "Daily Calm")
niche = payload.get("niche", "rain").lower().strip()
overlay_text = payload.get("overlay_text", "Peace comes from within.\nDo not seek it without.")
search_keyword = payload.get("search_keyword", "rain drops window dark night")
caption = payload.get("caption", "Take a deep breath and relax. #relaxing #asmr #reels")

PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "").strip()
FREESOUND_API_KEY = os.environ.get("FREESOUND_API_KEY", "").strip()
MAKE_WEBHOOK_URL = os.environ["MAKE_WEBHOOK_URL"]

VIDEO_DURATION = 16.0
FADE_DURATION = 1.5
FADE_START = VIDEO_DURATION - FADE_DURATION  # 14.5 seconds

ASMR_QUERY_MAP = {
    "rain": "gentle rain falling window ambient asmr",
    "ocean": "calm ocean waves beach water ambient",
    "water": "gentle stream river flowing water nature",
    "forest": "forest wind birds leaves nature ambient",
    "fire": "cozy fireplace crackling wood fire asmr",
    "night": "night crickets calm wind ambient",
    "snow": "soft winter wind snow ambient"
}

def prepare_quote_file(text):
    wrapped_lines = []
    for paragraph in text.splitlines():
        wrapped_lines.extend(textwrap.wrap(paragraph, width=24) or [""])
    clean_text = "\n".join(wrapped_lines)
    with open("quote.txt", "w", encoding="utf-8") as f:
        f.write(clean_text)

def detect_niche_keyword():
    combined = f"{niche} {search_keyword}".lower()
    for key in ASMR_QUERY_MAP:
        if key in combined:
            return key
    return "rain"

def download_visual_background():
    """Fetches HD video from Pixabay if key exists; otherwise uses Zero-Key Pollinations AI + FFmpeg Slow Zoom."""
    if PIXABAY_API_KEY:
        try:
            q = urllib.parse.quote(search_keyword)
            url = f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}&q={q}&per_page=10&safesearch=true"
            r = requests.get(url, timeout=20).json()
            hits = r.get("hits", [])
            
            if not hits:
                url = f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}&q=calm+nature+rain&per_page=10"
                hits = requests.get(url, timeout=20).json().get("hits", [])

            if hits:
                chosen = random.choice(hits)
                videos_dict = chosen["videos"]
                video_url = (
                    videos_dict.get("large", {}).get("url")
                    or videos_dict.get("medium", {}).get("url")
                    or videos_dict.get("small", {}).get("url")
                )
                if video_url:
                    with open("raw_video.mp4", "wb") as f:
                        f.write(requests.get(video_url, timeout=60).content)
                    return
        except Exception as e:
            print(f"Pixabay fetch skipped, using Zero-Key AI motion background: {e}")

    # ZERO-KEY CLOUD FALLBACK: Generate vertical 1080x1920 image via Pollinations.ai & animate 16s zoompan
    prompt = urllib.parse.quote(
        f"cinematic dark moody relaxing aesthetic {search_keyword} nature photography, 8k, photorealistic, no text"
    )
    seed = random.randint(1, 999999)
    img_url = f"https://image.pollinations.ai/prompt/{prompt}?width=1080&height=1920&seed={seed}&nologo=true"
    
    with open("bg_scene.jpg", "wb") as f:
        f.write(requests.get(img_url, timeout=45).content)

    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", "bg_scene.jpg",
        "-vf", "scale=2160:3840,zoompan=z='min(zoom+0.0004,1.20)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=480:s=1080x1920:fps=30",
        "-t", f"{VIDEO_DURATION}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "raw_video.mp4"
    ], check=True)

def fetch_asmr_audio(matched_key):
    """Fetches niche ASMR from Freesound or synthesizes soothing brown-noise rain via FFmpeg."""
    if FREESOUND_API_KEY:
        try:
            query = ASMR_QUERY_MAP.get(matched_key, f"{search_keyword} ambient")
            url = (
                f"https://freesound.org/apiv2/search/text/"
                f"?query={query}&filter=duration:[16 TO 180]&fields=id,name,previews"
                f"&page_size=5&token={FREESOUND_API_KEY}"
            )
            data = requests.get(url, timeout=15).json()
            results = data.get("results", [])
            if results:
                chosen_sound = random.choice(results)
                mp3_url = chosen_sound["previews"]["preview-hq-mp3"]
                with open("asmr.mp3", "wb") as f:
                    f.write(requests.get(mp3_url, timeout=20).content)
                return
        except Exception as e:
            print(f"Freesound fallback triggered: {e}")

    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anoisesrc=d={VIDEO_DURATION}:c=brown:r=44100:a=0.08",
        "-af", "lowpass=f=1200,highpass=f=120",
        "asmr.mp3"
    ], check=True)

def select_music_track(matched_key):
    """Uses audio/{niche}.mp3 or bgm.mp3 if present; otherwise synthesizes a 432Hz ambient pad."""
    candidate = f"audio/{matched_key}.mp3"
    if os.path.exists(candidate):
        return candidate
    if os.path.exists("bgm.mp3"):
        return "bgm.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"sine=frequency=216:duration={VIDEO_DURATION}",
        "-f", "lavfi",
        "-i", f"sine=frequency=324:duration={VIDEO_DURATION}",
        "-filter_complex", "[0:a][1:a]amix=inputs=2,volume=0.08,lowpass=f=400",
        "synth_bgm.mp3"
    ], check=True)
    return "synth_bgm.mp3"

def get_duration(filename):
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", filename
    ]
    return float(subprocess.check_output(cmd).decode().strip())

def render_ffmpeg(music_file):
    prepare_quote_file(overlay_text)
    
    bgm_duration = get_duration(music_file)
    bgm_start = random.uniform(0, bgm_duration - VIDEO_DURATION - 1) if bgm_duration > 20 else 0

    text_alpha = (
        "if(lt(t,1.0),0,"
        "if(lt(t,2.2),(t-1.0)/1.2,"
        "if(lt(t,13.5),1,"
        "if(lt(t,15.0),(15.0-t)/1.5,0))))"
    )

    # Darkened background (48% black matte + vignette) + centered white quote with soft shadow
    v_filter = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,setsar=1,fps=30,"
        f"eq=brightness=-0.05:contrast=1.08:saturation=0.85,"
        f"vignette=PI/4,"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.48:t=fill,"
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf:"
        f"textfile=quote.txt:"
        f"fontcolor=white:"
        f"fontsize=56:"
        f"line_spacing=26:"
        f"text_align=M+C:"
        f"x=(w-text_w)/2:"
        f"y=(h-text_h)/2:"
        f"shadowcolor=black@0.90:shadowx=4:shadowy=5:"
        f"alpha='{text_alpha}',"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={FADE_START}:d={FADE_DURATION}[v]"
    )

    # Dual-layer audio: 40% relaxing music + 45% ASMR soundscape
    a_filter = (
        f"[1:a]volume=0.40[music];"
        f"[2:a]volume=0.45[asmr];"
        f"[music][asmr]amix=inputs=2:duration=first:dropout_transition=2,"
        f"afade=t=in:st=0:d=1.0,"
        f"afade=t=out:st={FADE_START}:d={FADE_DURATION}[a]"
    )

    filter_complex = f"{v_filter};{a_filter}"

    cmd = [
        "ffmpeg", "-y",
        "-stream_loop", "-1", "-i", "raw_video.mp4",
        "-ss", f"{bgm_start:.2f}", "-stream_loop", "-1", "-i", music_file,
        "-stream_loop", "-1", "-i", "asmr.mp3",
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-t", f"{VIDEO_DURATION}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "final_output.mp4"
    ]
    subprocess.run(cmd, check=True)

def main():
    matched_key = detect_niche_keyword()
    download_visual_background()
    fetch_asmr_audio(matched_key)
    music_file = select_music_track(matched_key)
    render_ffmpeg(music_file)

    upload_result = cloudinary.uploader.upload(
        "final_output.mp4",
        resource_type="video",
        folder="auto_relaxing_videos"
    )
    video_url = upload_result["secure_url"]

    requests.post(MAKE_WEBHOOK_URL, json={
        "row_id": row_id,
        "title": title,
        "video_url": video_url,
        "caption": caption
    }, timeout=20)

if __name__ == "__main__":
    main()
