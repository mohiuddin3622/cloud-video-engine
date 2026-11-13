import os
import sys
import json
import asyncio
import random
import urllib.parse
import textwrap
import requests
import subprocess

# Auto-install edge-tts and gTTS if missing in workflow YAML
try:
    import edge_tts
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "edge-tts", "gTTS"], check=True)
    import edge_tts

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

# Microsoft's most realistic, natural human neural voices (Gen-2 Multilingual models)
NATURAL_CALM_VOICES = [
    "en-US-AndrewMultilingualNeural",  # #1 Most realistic warm, calm, natural male voice
    "en-US-BrianMultilingualNeural",   # Soft, intimate, natural conversational male voice
    "en-US-AvaMultilingualNeural",     # Ultra-natural, warm, soothing female voice
    "en-GB-RyanNeural"                 # Crisp, calm British narrator
]

ASMR_QUERY_MAP = {
    "rain": "gentle rain falling window ambient asmr",
    "ocean": "calm ocean waves beach water ambient",
    "water": "gentle stream river flowing water nature",
    "forest": "forest wind birds leaves nature ambient",
    "fire": "cozy fireplace crackling wood fire asmr",
    "night": "night crickets calm wind ambient",
    "snow": "soft winter wind snow ambient"
}

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

async def _edge_tts_save(spoken_text, voice_name):
    # Subtle -10% rate and -2Hz pitch keeps 100% natural human vocal formants without sounding robotic!
    communicate = edge_tts.Communicate(
        text=spoken_text,
        voice=voice_name,
        rate="-10%",
        pitch="-2Hz"
    )
    await communicate.save("voice_raw.mp3")

def generate_natural_calm_voiceover(text):
    """Generates a 100% natural, studio-clean human voiceover without artificial pitch warping."""
    # Clean punctuation so the neural model uses its own natural human prosody and breath pauses
    spoken_text = " ".join(text.replace("\r", " ").replace("\n", " ").split()).strip()

    raw_generated = False

    for voice_name in NATURAL_CALM_VOICES:
        try:
            asyncio.run(_edge_tts_save(spoken_text, voice_name))
            if os.path.exists("voice_raw.mp3") and os.path.getsize("voice_raw.mp3") > 1000:
                print(f"Generated natural neural voiceover using {voice_name} ({os.path.getsize('voice_raw.mp3')} bytes).")
                raw_generated = True
                break
        except Exception as e:
            print(f"edge_tts ({voice_name}) failed: {e}")

    if not raw_generated:
        try:
            print("Switching to gTTS backup...")
            from gtts import gTTS
            tts = gTTS(text=spoken_text, lang="en", tld="co.uk", slow=False)
            tts.save("voice_raw.mp3")
            if os.path.exists("voice_raw.mp3") and os.path.getsize("voice_raw.mp3") > 1000:
                raw_generated = True
        except Exception as e:
            print(f"gTTS backup failed: {e}")

    # Pure Studio Mastering: NO asetrate warping, NO hollow midrange scoop, NO metallic echo.
    # Just clean 44.1kHz stereo conversion, gentle warmth at 120Hz, and smooth dynamic leveling.
    if raw_generated:
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", "voice_raw.mp3",
                "-af", (
                    "aformat=sample_rates=44100:channel_layouts=stereo,"
                    "highpass=f=75,"
                    "equalizer=f=125:width_type=o:w=1.5:g=2.0,"
                    "dynaudnorm=f=150:g=15,"
                    "volume=1.6"
                ),
                "-ar", "44100", "-ac", "2",
                "voice.mp3"
            ], check=True)
            return True
        except Exception:
            subprocess.run([
                "ffmpeg", "-y", "-i", "voice_raw.mp3",
                "-af", "volume=1.8", "-ar", "44100", "-ac", "2",
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
    vo_start = 2.5
    vo_end = min(VIDEO_DURATION - 1.0, vo_start + vo_duration + 0.4)

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
        f"lowpass=f=3200,volume='if(between(t,{vo_start:.2f},{vo_end:.2f}),0.16,0.34)':eval=frame[piano];"
        f"[2:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"volume='if(between(t,{vo_start:.2f},{vo_end:.2f}),0.20,0.42)':eval=frame[asmr];"
        f"[3:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"adelay=2500|2500,apad=whole_dur={VIDEO_DURATION}[voice];"
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
    generate_natural_calm_voiceover(overlay_text)
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
