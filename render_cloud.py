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

# Deepest, softest, most intimate neural voices
WHISPER_CALM_VOICES = [
    "en-US-ChristopherNeural",         # Deepest, softest late-night male narrator
    "en-US-DavisNeural",               # Intimate, breathy, ultra-calm male voice
    "en-US-AndrewMultilingualNeural",  # Warm, grounded chest-resonance voice
    "en-GB-RyanNeural"                 # Soft, soothing British male voice
]

ASMR_QUERY_MAP = {
    "rain": "soft gentle rain falling window ambient asmr",
    "ocean": "calm gentle ocean waves beach water ambient",
    "water": "soft stream river flowing water nature calm",
    "forest": "soft forest wind leaves nature ambient calm",
    "fire": "cozy fireplace soft crackling wood fire asmr",
    "night": "quiet night crickets soft wind ambient",
    "snow": "soft winter wind snow calm ambient"
}

def prepare_quote_lines(text):
    """Splits text into clean, single-line files with ZERO newline characters."""
    cleaned = text.replace("\r", "").replace("\u2028", "\n").replace("\u2029", "\n")
    wrapped_lines = []
    for paragraph in cleaned.split("\n"):
        paragraph = paragraph.strip()
        if paragraph:
            wrapped_lines.extend(textwrap.wrap(paragraph, width=22))
    if not wrapped_lines:
        wrapped_lines = ["Peace comes from within."]

    line_files = []
    for idx, line in enumerate(wrapped_lines):
        fname = f"quote_line_{idx}.txt"
        with open(fname, "w", encoding="utf-8", newline="") as f:
            f.write(line.strip())
        line_files.append(fname)
    return line_files

async def _edge_tts_save(spoken_text, voice_name):
    # Slow rate (-22%), deep pitch (-8Hz), and soft delivery for a whisper-like cadence
    communicate = edge_tts.Communicate(
        text=spoken_text,
        voice=voice_name,
        rate="-22%",
        pitch="-8Hz",
        volume="-10%"
    )
    await communicate.save("voice_raw.mp3")

def generate_deep_whisper_voiceover(text):
    """Generates a deep, calm, breathy whisper-style voiceover with ASMR mic processing."""
    # Insert long, unhurried breathing pauses between phrases
    spoken_text = (
        text.replace("\r", " ")
        .replace("\n", " ... ... ")
        .replace(". ", " ... ... ")
        .replace(", ", " ... ")
        .replace("; ", " ... ")
        .strip()
    )

    raw_generated = False

    # 1. Try Microsoft Neural Deep Voices via Python asyncio API
    for voice_name in WHISPER_CALM_VOICES:
        try:
            asyncio.run(_edge_tts_save(spoken_text, voice_name))
            if os.path.exists("voice_raw.mp3") and os.path.getsize("voice_raw.mp3") > 1000:
                print(f"Generated deep calm voiceover using {voice_name} ({os.path.getsize('voice_raw.mp3')} bytes).")
                raw_generated = True
                break
        except Exception as e:
            print(f"edge_tts ({voice_name}) failed: {e}")

    # 2. Backup: gTTS + FFmpeg Deep Whisper Pitch-Shift
    if not raw_generated:
        try:
            print("Switching to gTTS + FFmpeg Deep Whisper Pitch-Shift backup...")
            from gtts import gTTS
            tts = gTTS(text=spoken_text, lang="en", tld="co.uk", slow=False)
            tts.save("voice_gtts.mp3")
            subprocess.run([
                "ffmpeg", "-y", "-i", "voice_gtts.mp3",
                "-af", "asetrate=24000*0.82,aresample=24000,atempo=0.92",
                "voice_raw.mp3"
            ], check=True)
            if os.path.exists("voice_raw.mp3") and os.path.getsize("voice_raw.mp3") > 1000:
                raw_generated = True
                print("Generated deep whisper voiceover via gTTS backup successfully.")
        except Exception as e:
            print(f"gTTS backup failed: {e}")

    # 3. ASMR Whisper & Deep Proximity Mastering Chain (44100Hz Stereo):
    # - equalizer=f=115:g=5 -> Warm close-mic chest resonance (proximity effect)
    # - equalizer=f=850:g=-5 -> Carves out harsh "loud talking" nasal midrange so it sounds whispered
    # - equalizer=f=3800:g=3 -> Adds soft, intimate breath/air detail
    # - lowpass=f=5200 -> Removes sharp digital sibilance
    # - acompressor -> Keeps every word velvety-smooth and even in volume
    # - extrastereo=m=1.25 + aecho -> Gentle binaural closeness
    if raw_generated:
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", "voice_raw.mp3",
                "-af", (
                    "aformat=sample_rates=44100:channel_layouts=stereo,"
                    "highpass=f=60,"
                    "equalizer=f=115:width_type=o:w=1.8:g=5,"
                    "equalizer=f=850:width_type=o:w=1.5:g=-5,"
                    "equalizer=f=3800:width_type=o:w=2.0:g=3,"
                    "lowpass=f=5200,"
                    "acompressor=threshold=-20dB:ratio=3:attack=15:release=250:makeup=2,"
                    "extrastereo=m=1.25,"
                    "aecho=0.8:0.86:45:0.14,"
                    "volume=1.9"
                ),
                "-ar", "44100", "-ac", "2",
                "voice.mp3"
            ], check=True)
            print(f"Mastered deep whisper stereo voice.mp3 successfully ({os.path.getsize('voice.mp3')} bytes).")
            return True
        except Exception as e:
            print(f"Whisper EQ warning ({e}), converting directly to warm stereo...")
            subprocess.run([
                "ffmpeg", "-y", "-i", "voice_raw.mp3",
                "-af", "lowpass=f=5000,volume=1.9", "-ar", "44100", "-ac", "2",
                "voice.mp3"
            ], check=True)
            return True

    print("WARNING: All TTS engines failed; generating silent fallback track.")
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
            f"cinematic dark moody relaxing aesthetic {search_keyword} nature photography, 8k, no text"
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
            print("Generated background from Pollinations AI successfully.")
            return
    except Exception as e:
        print(f"Pollinations fallback skipped: {e}")

    print("Generating built-in dark midnight aesthetic background via FFmpeg...")
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=#0d1b2a:s=1080x1920:r=30:d={VIDEO_DURATION}",
        "-vf", "noise=alls=8:allf=t+u",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "raw_video.mp4"
    ], check=True)

def fetch_asmr_audio(matched_key):
    """Fetches niche ASMR sound from Freesound or synthesizes soothing stereo rain ASMR."""
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
                        print("Downloaded ASMR audio from Freesound.")
                        return
        except Exception as e:
            print(f"Freesound ASMR fallback triggered: {e}")

    print("Synthesizing stereo rain ASMR audio via FFmpeg...")
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anoisesrc=d={VIDEO_DURATION}:c=pink:r=44100:a=0.10",
        "-af", "bandpass=f=800:width_type=h:w=600,tremolo=f=0.35:d=0.30,extrastereo=m=2.0",
        "asmr.mp3"
    ], check=True)

def fetch_relaxing_piano():
    """Uses repo piano.mp3, fetches soft piano from Freesound, or synthesizes warm piano chords."""
    for filename in ["piano.mp3", "bgm.mp3", "audio/piano.mp3", "audio/default.mp3"]:
        if os.path.exists(filename):
            return filename

    if FREESOUND_API_KEY:
        try:
            query = urllib.parse.quote("soft felt piano calm ambient sleep instrumental")
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
                        print("Downloaded piano track from Freesound.")
                        return "cloud_piano.mp3"
        except Exception as e:
            print(f"Freesound Piano fallback triggered: {e}")

    print("Synthesizing warm ambient piano chords via FFmpeg...")
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
        "lowpass=f=550,aecho=0.8:0.88:120|240:0.4|0.3,volume=0.18",
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
    
    # Slow down short clips smoothly so they match 16.0s without looping
    raw_vid_duration = get_duration("raw_video.mp4")
    if 0.5 < raw_vid_duration < VIDEO_DURATION:
        pts_multiplier = (VIDEO_DURATION + 0.5) / raw_vid_duration
        print(f"Video is {raw_vid_duration:.2f}s (< 16s). Reducing speed (setpts={pts_multiplier:.4f}*PTS) to match 16s.")
    else:
        pts_multiplier = 1.0

    vo_duration = get_duration("voice.mp3")
    vo_start = 2.5
    vo_end = min(VIDEO_DURATION - 1.0, vo_start + vo_duration + 0.4)
    print(f"Whisper Voiceover duration: {vo_duration:.2f}s (Ducking background from {vo_start:.1f}s to {vo_end:.1f}s)")

    piano_duration = get_duration(piano_file)
    piano_start = random.uniform(0, piano_duration - VIDEO_DURATION - 1) if piano_duration > 20 else 0

    text_alpha = (
        "if(lt(t,1.0),0,"
        "if(lt(t,2.0),(t-1.0)/1.0,"
        "if(lt(t,14.5),1,"
        "if(lt(t,15.8),(15.8-t)/1.3,0))))"
    )

    line_height = 80
    total_height = len(line_files) * line_height
    drawtext_chain = []
    for idx, fname in enumerate(line_files):
        y_offset = f"(h-{total_height})/2+{idx * line_height}"
        drawtext_chain.append(
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf:"
            f"textfile={fname}:"
            f"fontcolor=white:"
            f"fontsize=54:"
            f"x=(w-text_w)/2:"
            f"y={y_offset}:"
            f"shadowcolor=black@0.90:shadowx=4:shadowy=5:"
            f"enable='gte(t,1.0)':"
            f"alpha='{text_alpha}'"
        )
    drawtext_filters_str = ",".join(drawtext_chain)

    v_filter = (
        f"[0:v]setpts={pts_multiplier:.4f}*PTS,"
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,setsar=1,fps=30,"
        f"eq=brightness=-0.05:contrast=1.08:saturation=0.85,"
        f"vignette=PI/4,"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.48:t=fill,"
        f"{drawtext_filters_str},"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={FADE_START}:d={FADE_DURATION}[v]"
    )

    # 3-Layer Ultra-Relaxing Audio Mix:
    # - Piano: Warm 1800Hz felt-piano lowpass filter, ducks to 14% during whisper
    # - ASMR: Soft 4200Hz lowpass + binaural stereo widening, ducks to 20% during whisper
    # - Voice: Deep intimate whisper enters at 2.5s (2500ms)
    a_filter = (
        f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"lowpass=f=1800,volume='if(between(t,{vo_start:.2f},{vo_end:.2f}),0.14,0.30)':eval=frame[piano];"
        f"[2:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"lowpass=f=4200,extrastereo=m=1.6,volume='if(between(t,{vo_start:.2f},{vo_end:.2f}),0.20,0.38)':eval=frame[asmr];"
        f"[3:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"adelay=2500|2500,apad=whole_dur={VIDEO_DURATION}[voice];"
        f"[piano][asmr][voice]amix=inputs=3:duration=first:dropout_transition=0:normalize=0,"
        f"afade=t=in:st=0:d=1.2,"
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
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "final_output.mp4"
    ]
    subprocess.run(cmd, check=True)

def upload_video_with_fallback():
    """Uploads to Cloudinary first; falls back to free Catbox/Litterbox MP4 hosting if Cloudinary fails."""
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
                video_url = r.text.strip()
                print(f"Uploaded to Litterbox backup host successfully: {video_url}")
                return video_url
    except Exception as e2:
        print(f"Litterbox backup failed: {e2}")

    with open("final_output.mp4", "rb") as f:
        r = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": ("final_output.mp4", f, "video/mp4")},
            timeout=60
        )
        video_url = r.text.strip()
        print(f"Uploaded to Catbox permanent host successfully: {video_url}")
        return video_url

def main():
    matched_key = detect_niche_keyword()
    download_visual_background()
    fetch_asmr_audio(matched_key)
    piano_file = fetch_relaxing_piano()
    generate_deep_whisper_voiceover(overlay_text)
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
