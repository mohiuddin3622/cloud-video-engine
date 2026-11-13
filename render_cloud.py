import os
import re
import sys
import json
import asyncio
import random
import urllib.parse
import textwrap
import requests
import subprocess

# Auto-install kokoro-onnx and soundfile if missing in workflow YAML
try:
    import soundfile as sf
    import numpy as np
    from kokoro_onnx import Kokoro
except ImportError:
    subprocess.run([
        sys.executable, "-m", "pip", "install",
        "kokoro-onnx", "soundfile", "numpy", "edge-tts", "gTTS"
    ], check=True)
    import soundfile as sf
    import numpy as np
    from kokoro_onnx import Kokoro

# 1. Auto-sanitize CLOUDINARY_URL BEFORE importing cloudinary!
raw_cloud_url = os.environ.get("CLOUDINARY_URL", "").strip()
if raw_cloud_url.startswith("CLOUDINARY_URL="):
    raw_cloud_url = raw_cloud_url.split("CLOUDINARY_URL=", 1)[1].strip()
raw_cloud_url = raw_cloud_url.replace("<", "").replace(">", "").strip('"').strip("'").strip()
if raw_cloud_url:
    os.environ["CLOUDINARY_URL"] = raw_cloud_url

import cloudinary
import cloudinary.uploader

if raw_cloud_url.startswith("cloudinary://") and "@" in raw_cloud_url:
    try:
        creds_part, cloud_name = raw_cloud_url.replace("cloudinary://", "", 1).split("@", 1)
        api_key, api_secret = creds_part.split(":", 1)
        cloudinary.config(
            cloud_name=cloud_name.strip(),
            api_key=api_key.strip(),
            api_secret=api_secret.strip(),
            secure=True
        )
    except Exception as e:
        print(f"Cloudinary URL parse warning: {e}")

# 2. Load payload sent from Google Sheets
raw_payload = os.environ.get("PAYLOAD_JSON", "{}")
try:
    payload = json.loads(raw_payload) if raw_payload else {}
except Exception:
    payload = {}

row_id = str(payload.get("row_id", "1"))
title = str(payload.get("title", "Daily Calm"))
niche = str(payload.get("niche", "rain")).lower().strip()
overlay_text = str(payload.get("overlay_text", "Peace comes from within.\nDo not seek it without."))
search_keyword = str(payload.get("search_keyword", "rain drops window dark night"))
caption = str(payload.get("caption", "Take a deep breath and relax. #relaxing #asmr #pianomusic #reels"))

PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "").strip()
FREESOUND_API_KEY = os.environ.get("FREESOUND_API_KEY", "").strip()
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL", "").strip()

VIDEO_DURATION = 16.0
FADE_DURATION = 1.5
FADE_START = VIDEO_DURATION - FADE_DURATION  # 14.5 seconds

# Kokoro-82M ASMR & Deep Calm Voice Options:
# - "af_nicole"  -> Famous breathy, soft-spoken ASMR whisper voice (#1 for instant relaxation)
# - "af_heart"   -> Warm, emotional, comforting female voice
# - "bm_george"  -> Deep, velvety British narrator (Calm App feel)
# - "am_michael" -> Deep, warm, grounded American male voice
# - "am_adam"    -> Calm, gentle male storytelling voice
KOKORO_VOICE = "af_nicole"

ASMR_QUERY_MAP = {
    "rain": "gentle rain falling window ambient asmr",
    "ocean": "calm ocean waves beach water ambient",
    "water": "gentle stream river flowing water nature",
    "forest": "forest wind birds leaves nature ambient",
    "fire": "cozy fireplace crackling wood fire asmr",
    "night": "night crickets calm wind ambient",
    "snow": "soft winter wind snow ambient"
}

def ensure_kokoro_models():
    """Downloads Kokoro-82M ONNX model and voices pack if not already downloaded by render.yml."""
    model_path = "kokoro-v1.0.int8.onnx"
    voices_path = "voices-v1.0.bin"

    if not os.path.exists(model_path) or os.path.getsize(model_path) < 10_000_000:
        print("Downloading Kokoro-82M int8 model...")
        url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx"
        r = requests.get(url, timeout=120)
        with open(model_path, "wb") as f:
            f.write(r.content)

    if not os.path.exists(voices_path) or os.path.getsize(voices_path) < 1_000_000:
        print("Downloading Kokoro-82M voices pack...")
        url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
        r = requests.get(url, timeout=60)
        with open(voices_path, "wb") as f:
            f.write(r.content)

    return model_path, voices_path

def prepare_quote_lines(text):
    """Splits quote into clean single-line files + creates a spaced-out aesthetic header file."""
    cleaned = text.replace("\r", "").replace("\u2028", "\n").replace("\u2029", "\n")
    wrapped_lines = []
    for paragraph in cleaned.split("\n"):
        paragraph = paragraph.strip()
        if paragraph:
            wrapped_lines.extend(textwrap.wrap(paragraph, width=21))
    if not wrapped_lines:
        wrapped_lines = ["Peace comes from within."]

    line_files = []
    for idx, line in enumerate(wrapped_lines):
        fname = f"quote_line_{idx}.txt"
        with open(fname, "w", encoding="utf-8", newline="") as f:
            f.write(line.strip())
        line_files.append(fname)

    clean_title = "".join(c for c in title.upper() if c.isalnum() or c == " ").strip() or "DAILY STILLNESS"
    spaced_header = "   ".join(word for word in [" ".join(list(w)) for w in clean_title.split()])
    with open("header.txt", "w", encoding="utf-8", newline="") as f:
        f.write(f"•  {spaced_header}  •")

    return line_files

def generate_kokoro_asmr_voiceover(text):
    """
    Generates an ultra-realistic, breathy ASMR voiceover using Kokoro-82M (af_nicole),
    with a 1.1-second meditative silence pause between sentences and binaural mastering.
    """
    raw_chunks = re.split(r'(?<=[.!?])\s+|\n+', text.replace("\r", ""))
    phrases = [c.strip() for c in raw_chunks if c.strip()]
    if not phrases:
        phrases = ["Peace comes from within."]

    raw_generated = False

    try:
        model_path, voices_path = ensure_kokoro_models()
        kokoro = Kokoro(model_path, voices_path)

        audio_segments = []
        sample_rate = 24000

        for i, phrase in enumerate(phrases):
            # speed=0.84 gives a slow, unhurried, deeply soothing ASMR cadence
            samples, sr = kokoro.create(
                phrase,
                voice=KOKORO_VOICE,
                speed=0.84,
                lang="en-us"
            )
            sample_rate = sr
            audio_segments.append(samples)

            # Insert a 1.1-second pure meditative silence gap between sentences
            if i < len(phrases) - 1:
                pause_samples = np.zeros(int(sample_rate * 1.10), dtype=np.float32)
                audio_segments.append(pause_samples)

        full_audio = np.concatenate(audio_segments)
        sf.write("voice_raw.wav", full_audio, sample_rate)
        if os.path.exists("voice_raw.wav") and os.path.getsize("voice_raw.wav") > 1000:
            print(f"Generated Kokoro-82M ASMR voiceover ({KOKORO_VOICE}) successfully!")
            raw_generated = True
    except Exception as e:
        print(f"Kokoro-82M error ({e}), falling back to Edge-TTS...")

    # Fallback if Kokoro fails for any reason
    if not raw_generated:
        try:
            import edge_tts
            communicate = edge_tts.Communicate(
                text=" ... ".join(phrases),
                voice="en-GB-RyanNeural",
                rate="-12%",
                pitch="-2Hz"
            )
            asyncio.run(communicate.save("voice_raw.wav"))
            raw_generated = True
        except Exception as e2:
            print(f"Fallback TTS failed: {e2}")

    # Studio "Warm Ribbon Mic + 12ms Binaural Intimacy" Mastering Chain (44.1kHz Stereo)
    if raw_generated:
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", "voice_raw.wav",
                "-af", (
                    "aformat=sample_rates=44100:channel_layouts=stereo,"
                    "highpass=f=65,"
                    "equalizer=f=130:width_type=o:w=1.5:g=3.0,"
                    "equalizer=f=6200:width_type=o:w=1.5:g=-4.0,"
                    "lowpass=f=8000,"
                    "adelay=0|12,"
                    "afade=t=in:st=0:d=0.15,"
                    "volume=2.1"
                ),
                "-ar", "44100", "-ac", "2",
                "voice.mp3"
            ], check=True)
            return True
        except Exception as e:
            print(f"Mastering fallback: {e}")
            subprocess.run([
                "ffmpeg", "-y", "-i", "voice_raw.wav",
                "-af", "volume=2.0", "-ar", "44100", "-ac", "2",
                "voice.mp3"
            ], check=True)
            return True

    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={VIDEO_DURATION}",
        "voice.mp3"
    ], check=True)
    return False

def detect_niche_keyword():
    combined = f"{niche} {search_keyword}".lower()
    for key in ASMR_QUERY_MAP:
        if key in combined:
            return key
    return "rain"

def download_visual_background():
    """1. Tries Pixabay Video -> 2. Tries Pollinations AI -> 3. Pure FFmpeg Dark Aesthetic Fallback."""
    if PIXABAY_API_KEY:
        try:
            q = urllib.parse.quote(search_keyword)
            url = f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}&q={q}&per_page=10&safesearch=true"
            r = requests.get(url, timeout=20)
            if r.status_code == 200:
                hits = r.json().get("hits", [])
                if not hits:
                    url = f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}&q=calm+nature+rain&per_page=10"
                    hits = requests.get(url, timeout=20).json().get("hits", [])
                if hits:
                    chosen = random.choice(hits)
                    videos_dict = chosen.get("videos", {})
                    video_url = (
                        videos_dict.get("large", {}).get("url")
                        or videos_dict.get("medium", {}).get("url")
                        or videos_dict.get("small", {}).get("url")
                    )
                    if video_url:
                        vid_data = requests.get(video_url, timeout=60).content
                        if len(vid_data) > 50000:
                            with open("raw_video.mp4", "wb") as f:
                                f.write(vid_data)
                            print("Downloaded video from Pixabay successfully.")
                            return
        except Exception as e:
            print(f"Pixabay skipped: {e}")

    try:
        prompt = urllib.parse.quote(
            f"cinematic 35mm kodak film dark moody relaxing aesthetic {search_keyword} nature photography, 8k, no text"
        )
        seed = random.randint(1, 999999)
        img_url = f"https://image.pollinations.ai/prompt/{prompt}?width=1080&height=1920&seed={seed}&nologo=true"
        r = requests.get(img_url, timeout=35)
        if r.status_code == 200 and len(r.content) > 10000:
            with open("bg_scene.jpg", "wb") as f:
                f.write(r.content)
            subprocess.run([
                "ffmpeg", "-y", "-loop", "1", "-i", "bg_scene.jpg",
                "-vf", "scale=1080:1920,fps=30",
                "-t", f"{VIDEO_DURATION}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "raw_video.mp4"
            ], check=True)
            return
    except Exception as e:
        print(f"Pollinations fallback skipped: {e}")

    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=#0d1b2a:s=1080x1920:r=30:d={VIDEO_DURATION}",
        "-vf", "noise=alls=8:allf=t+u",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "raw_video.mp4"
    ], check=True)

def fetch_asmr_audio(matched_key):
    if FREESOUND_API_KEY:
        try:
            query = urllib.parse.quote(ASMR_QUERY_MAP.get(matched_key, f"{search_keyword} ambient"))
            url = (
                f"https://freesound.org/apiv2/search/text/"
                f"?query={query}&filter=duration:[16 TO 180]&fields=id,name,previews"
                f"&page_size=8&token={FREESOUND_API_KEY}"
            )
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                results = r.json().get("results", [])
                if results:
                    chosen_sound = random.choice(results)
                    mp3_url = chosen_sound["previews"]["preview-hq-mp3"]
                    audio_bytes = requests.get(mp3_url, timeout=20).content
                    if len(audio_bytes) > 5000:
                        with open("asmr.mp3", "wb") as f:
                            f.write(audio_bytes)
                        return
        except Exception as e:
            print(f"Freesound ASMR fallback triggered: {e}")

    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anoisesrc=d={VIDEO_DURATION}:c=pink:r=44100:a=0.10",
        "-af", "bandpass=f=900:width_type=h:w=700,tremolo=f=0.4:d=0.35,extrastereo=m=2.0",
        "asmr.mp3"
    ], check=True)

def fetch_relaxing_piano():
    for filename in ["piano.mp3", "bgm.mp3", "audio/piano.mp3", "audio/default.mp3"]:
        if os.path.exists(filename):
            return filename

    if FREESOUND_API_KEY:
        try:
            query = urllib.parse.quote("soft relaxing piano calm ambient instrumental")
            url = (
                f"https://freesound.org/apiv2/search/text/"
                f"?query={query}&filter=duration:[16 TO 240]&fields=id,name,previews"
                f"&page_size=10&token={FREESOUND_API_KEY}"
            )
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                results = r.json().get("results", [])
                if results:
                    chosen_piano = random.choice(results)
                    mp3_url = chosen_piano["previews"]["preview-hq-mp3"]
                    audio_bytes = requests.get(mp3_url, timeout=20).content
                    if len(audio_bytes) > 5000:
                        with open("cloud_piano.mp3", "wb") as f:
                            f.write(audio_bytes)
                        return "cloud_piano.mp3"
        except Exception as e:
            print(f"Freesound Piano fallback triggered: {e}")

    subprocess.run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"sine=frequency=261.63:duration={VIDEO_DURATION}",
        "-f", "lavfi", "-i", f"sine=frequency=329.63:duration={VIDEO_DURATION}",
        "-f", "lavfi", "-i", f"sine=frequency=392.00:duration={VIDEO_DURATION}",
        "-f", "lavfi", "-i", f"sine=frequency=493.88:duration={VIDEO_DURATION}",
        "-filter_complex",
        "[0:a]tremolo=f=0.25:d=0.6[a0];"
        "[1:a]tremolo=f=0.33:d=0.6[a1];"
        "[2:a]tremolo=f=0.20:d=0.6[a2];"
        "[3:a]tremolo=f=0.15:d=0.7[a3];"
        "[a0][a1][a2][a3]amix=inputs=4:duration=first,"
        "lowpass=f=650,aecho=0.8:0.88:120|240:0.4|0.3,volume=0.18",
        "synth_piano.mp3"
    ], check=True)
    return "synth_piano.mp3"

def get_duration(filename):
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", filename
        ]
        return float(subprocess.check_output(cmd).decode().strip())
    except Exception:
        return 16.0

def render_ffmpeg(piano_file):
    line_files = prepare_quote_lines(overlay_text)
    
    raw_vid_duration = get_duration("raw_video.mp4")
    if 0.5 < raw_vid_duration < VIDEO_DURATION:
        pts_multiplier = (VIDEO_DURATION + 0.5) / raw_vid_duration
    else:
        pts_multiplier = 1.0

    vo_duration = get_duration("voice.mp3")
    vo_start = 2.2
    vo_end = min(VIDEO_DURATION - 1.0, vo_start + vo_duration + 0.3)

    piano_duration = get_duration(piano_file)
    piano_start = random.uniform(0, piano_duration - VIDEO_DURATION - 1) if piano_duration > 20 else 0

    line_height = 84
    total_height = len(line_files) * line_height
    header_y = f"(h-{total_height})/2-95"
    divider_y = f"(h+{total_height})/2+45"

    header_alpha = "if(lt(t,1.0),0,if(lt(t,2.0),(t-1.0)/1.0,if(lt(t,14.5),0.80,if(lt(t,15.8),0.80*(15.8-t)/1.3,0))))"
    drawtext_chain = [
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
        f"textfile=header.txt:"
        f"fontcolor=#e2c08d:"
        f"fontsize=26:"
        f"x=(w-text_w)/2:"
        f"y={header_y}:"
        f"shadowcolor=black@0.85:shadowx=2:shadowy=2:"
        f"enable='gte(t,1.0)':"
        f"alpha='{header_alpha}'"
    ]

    for idx, fname in enumerate(line_files):
        start_t = 1.0 + (idx * 0.45)
        fade_in_end = start_t + 0.95
        line_alpha = (
            f"if(lt(t,{start_t:.2f}),0,"
            f"if(lt(t,{fade_in_end:.2f}),(t-{start_t:.2f})/0.95,"
            f"if(lt(t,14.5),1,"
            f"if(lt(t,15.8),(15.8-t)/1.3,0))))"
        )
        y_offset = f"(h-{total_height})/2+{idx * line_height}"
        drawtext_chain.append(
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf:"
            f"textfile={fname}:"
            f"fontcolor=#fdfbf7:"
            f"fontsize=54:"
            f"x=(w-text_w)/2:"
            f"y={y_offset}:"
            f"shadowcolor=black@0.92:shadowx=4:shadowy=5:"
            f"enable='gte(t,{start_t:.2f})':"
            f"alpha='{line_alpha}'"
        )

    drawtext_filters_str = ",".join(drawtext_chain)

    v_filter = (
        f"[0:v]setpts={pts_multiplier:.4f}*PTS,"
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,setsar=1,fps=30,"
        f"eq=brightness=-0.04:contrast=1.08:saturation=0.88,"
        f"colorbalance=rs=-0.03:bs=0.04:rh=0.05:gh=0.02:bh=-0.04,"
        f"noise=alls=4:allf=t+u,"
        f"vignette=PI/4,"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.48:t=fill,"
        f"drawbox=x=(iw-140)/2:y={divider_y}:w=140:h=2:color=#e2c08d@0.45:t=fill:enable='between(t,1.5,14.8)',"
        f"drawbox=x=0:y=ih-6:w='iw*(t/{VIDEO_DURATION})':h=6:color=#e2c08d@0.75:t=fill,"
        f"{drawtext_filters_str},"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={FADE_START}:d={FADE_DURATION}[v]"
    )

    a_filter = (
        f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"lowpass=f=3000,volume='if(between(t,{vo_start:.2f},{vo_end:.2f}),0.16,0.35)':eval=frame[piano];"
        f"[2:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"volume='if(between(t,{vo_start:.2f},{vo_end:.2f}),0.20,0.42)':eval=frame[asmr];"
        f"[3:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"adelay=2200|2200,apad=whole_dur={VIDEO_DURATION}[voice];"
        f"[piano][asmr][voice]amix=inputs=3:duration=first:dropout_transition=0:normalize=0,"
        f"afade=t=in:st=0:d=1.0,"
        f"afade=t=out:st={FADE_START}:d={FADE_DURATION}[a]"
    )

    filter_complex = f"{v_filter};{a_filter}"

    cmd = [
        "ffmpeg", "-y",
        "-i", "raw_video.mp4",
        "-ss", f"{piano_start:.2f}", "-stream_loop", "-1", "-i", piano_file,
        "-stream_loop", "-1", "-i", "asmr.mp3",
        "-i", "voice.mp3",
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-t", f"{VIDEO_DURATION}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "final_output.mp4"
    ]
    subprocess.run(cmd, check=True)

def upload_video_with_fallback():
    try:
        upload_result = cloudinary.uploader.upload(
            "final_output.mp4",
            resource_type="video",
            folder="auto_relaxing_videos"
        )
        video_url = upload_result["secure_url"]
        print(f"Uploaded to Cloudinary successfully: {video_url}")
        return video_url
    except Exception as e:
        print(f"Cloudinary upload skipped ({e}). Uploading to backup free MP4 host...")

    try:
        with open("final_output.mp4", "rb") as f:
            r = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "72h"},
                files={"fileToUpload": ("final_output.mp4", f, "video/mp4")},
                timeout=60
            )
            if r.status_code == 200 and r.text.strip().startswith("http"):
                return r.text.strip()
    except Exception as e2:
        print(f"Litterbox backup failed: {e2}")

    with open("final_output.mp4", "rb") as f:
        r = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": ("final_output.mp4", f, "video/mp4")},
            timeout=60
        )
        return r.text.strip()

def main():
    matched_key = detect_niche_keyword()
    download_visual_background()
    fetch_asmr_audio(matched_key)
    piano_file = fetch_relaxing_piano()
    generate_kokoro_asmr_voiceover(overlay_text)
    render_ffmpeg(piano_file)

    video_url = upload_video_with_fallback()

    if MAKE_WEBHOOK_URL and MAKE_WEBHOOK_URL.startswith("http"):
        resp = requests.post(MAKE_WEBHOOK_URL, json={
            "row_id": row_id,
            "title": title,
            "video_url": video_url,
            "caption": caption
        }, timeout=20)
        print(f"Sent webhook to Make.com successfully! Status: {resp.status_code}")
    else:
        print("Warning: MAKE_WEBHOOK_URL is not set or invalid; skipped Make webhook.")

if __name__ == "__main__":
    main()
