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

VO_START_DELAY = 2.0      # Voiceover starts at 2.0 seconds
EXTRA_TAIL_SECONDS = 4.0  # Extra 4.0 seconds of calm ASMR + Piano after voiceover finishes
FADE_DURATION = 1.5       # Smooth 1.5s fade-out at the very end

ASMR_QUERY_MAP = {
    "rain": "gentle rain falling window ambient asmr",
    "ocean": "calm ocean waves beach water ambient",
    "water": "gentle stream river flowing water nature",
    "forest": "forest wind birds leaves nature ambient",
    "fire": "cozy fireplace crackling wood fire asmr",
    "night": "night crickets calm wind ambient",
    "snow": "soft winter wind snow ambient"
}

def ensure_assets():
    """Downloads Kokoro-82M ONNX models and Classic Poetic Book Serif Font (Crimson Text / EB Garamond)."""
    model_path = "kokoro-v1.0.int8.onnx"
    voices_path = "voices-v1.0.bin"
    font_path = "PoetrySerif.ttf"

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

    if not os.path.exists(font_path) or os.path.getsize(font_path) < 40_000:
        font_urls = [
            "https://raw.githubusercontent.com/google/fonts/main/ofl/crimsontext/CrimsonText-Regular.ttf",
            "https://raw.githubusercontent.com/google/fonts/main/ofl/lora/Lora%5Bwght%5D.ttf",
            "https://raw.githubusercontent.com/google/fonts/main/ofl/cormorantgaramond/CormorantGaramond%5Bwght%5D.ttf"
        ]
        for f_url in font_urls:
            try:
                r = requests.get(f_url, timeout=25)
                if r.status_code == 200 and len(r.content) > 40_000:
                    with open(font_path, "wb") as f:
                        f.write(r.content)
                    print(f"Downloaded poetic serif font successfully from {f_url}")
                    break
            except Exception as e:
                print(f"Font attempt skipped: {e}")

    return model_path, voices_path

def get_quote_font_path():
    """Returns PoetrySerif.ttf or EBGaramond if available, otherwise DejaVuSerif."""
    candidates = [
        "PoetrySerif.ttf",
        "CormorantGaramond.ttf",
        "/usr/share/fonts/truetype/ebgaramond/EBGaramond12-Regular.ttf",
        "/usr/share/fonts/opentype/ebgaramond/EBGaramond12-Regular.otf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"
    ]
    for path in candidates:
        if os.path.exists(path) and os.path.getsize(path) > 20_000:
            return path
    return "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"

def prepare_poetic_lines(text):
    """
    Formats overlay_text into left-aligned poetic lines & stanzas matching the Sleeping Poet style.
    Returns a list of (filename, relative_y_offset) tuples and total_block_height.
    """
    cleaned = text.replace("\r", "").replace("\u2028", "\n").replace("\u2029", "\n").strip()
    if not cleaned:
        cleaned = "Peace comes from within.\nDo not seek it without."

    # Check if user already formatted multi-stanza text; if it's a single line with multiple sentences, split sentences into stanzas
    raw_lines = cleaned.split("\n")
    if len(raw_lines) == 1 and ". " in cleaned:
        stanzas = [s.strip() for s in re.split(r'(?<=[.!?])\s+', cleaned) if s.strip()]
    else:
        # Split on empty lines (\n\n) to detect stanzas
        stanzas = [s.strip() for s in re.split(r'\n\s*\n', cleaned) if s.strip()]

    line_height = 60       # Crisp poetic line spacing for fontsize 45
    stanza_gap = 40        # Extra vertical space between stanzas (like the screenshot)
    current_y = 0
    line_items = []
    file_idx = 0

    for s_idx, stanza in enumerate(stanzas):
        for subline in stanza.split("\n"):
            subline = subline.strip()
            if not subline:
                continue
            wrapped = textwrap.wrap(subline, width=28)
            for w_line in wrapped:
                fname = f"quote_line_{file_idx}.txt"
                with open(fname, "w", encoding="utf-8", newline="") as f:
                    f.write(w_line.strip())
                line_items.append((fname, current_y))
                current_y += line_height
                file_idx += 1
        if s_idx < len(stanzas) - 1:
            current_y += stanza_gap

    total_height = max(line_height, current_y - line_height)
    return line_items, total_height

def get_duration(filename):
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", filename
        ]
        return float(subprocess.check_output(cmd).decode().strip())
    except Exception:
        return 10.0

def generate_kokoro_asmr_voiceover(text):
    """
    Generates an ultra-realistic voiceover by blending 55% af_nicole (breathy ASMR whisper)
    + 45% af_heart (warm emotional depth), with a 1.1s meditative silence gap between sentences.
    """
    raw_chunks = re.split(r'(?<=[.!?])\s+|\n+', text.replace("\r", ""))
    phrases = [c.strip() for c in raw_chunks if c.strip()]
    if not phrases:
        phrases = ["Peace comes from within."]

    raw_generated = False

    try:
        model_path, voices_path = ensure_assets()
        kokoro = Kokoro(model_path, voices_path)

        try:
            nicole_style = kokoro.get_voice_style("af_nicole")
            heart_style = kokoro.get_voice_style("af_heart")
            voice_target = (nicole_style * 0.55) + (heart_style * 0.45)
            print("Using custom 55% af_nicole + 45% af_heart blended ASMR voice vector.")
        except Exception:
            voice_target = "af_nicole"

        audio_segments = []
        sample_rate = 24000

        for i, phrase in enumerate(phrases):
            samples, sr = kokoro.create(
                phrase,
                voice=voice_target,
                speed=0.83,
                lang="en-us"
            )
            sample_rate = sr
            audio_segments.append(samples)

            if i < len(phrases) - 1:
                pause_samples = np.zeros(int(sample_rate * 1.10), dtype=np.float32)
                audio_segments.append(pause_samples)

        full_audio = np.concatenate(audio_segments)
        sf.write("voice_raw.wav", full_audio, sample_rate)
        if os.path.exists("voice_raw.wav") and os.path.getsize("voice_raw.wav") > 1000:
            print("Generated blended Kokoro-82M ASMR voiceover successfully!")
            raw_generated = True
    except Exception as e:
        print(f"Kokoro-82M error ({e}), falling back to Edge-TTS...")

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
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=10.0",
        "voice.mp3"
    ], check=True)
    return False

def detect_niche_keyword():
    combined = f"{niche} {search_keyword}".lower()
    for key in ASMR_QUERY_MAP:
        if key in combined:
            return key
    return "rain"

def download_visual_background(target_duration):
    if PIXABAY_API_KEY:
        try:
            q = urllib.parse.quote(search_keyword)
            vertical_url = (
                f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}"
                f"&q={q}&orientation=vertical&per_page=15&safesearch=true"
            )
            r = requests.get(vertical_url, timeout=20)
            hits = r.json().get("hits", []) if r.status_code == 200 else []

            if not hits:
                any_url = f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}&q={q}&per_page=15&safesearch=true"
                r2 = requests.get(any_url, timeout=20)
                hits = r2.json().get("hits", []) if r2.status_code == 200 else []

            if not hits:
                fallback_url = (
                    f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}"
                    f"&q=calm+nature+rain&orientation=vertical&per_page=15&safesearch=true"
                )
                r3 = requests.get(fallback_url, timeout=20)
                hits = r3.json().get("hits", []) if r3.status_code == 200 else []

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
                        print("Downloaded HD video from Pixabay successfully.")
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
                "-t", f"{target_duration:.2f}",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "raw_video.mp4"
            ], check=True)
            return
    except Exception as e:
        print(f"Pollinations fallback skipped: {e}")

    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=#0d1b2a:s=1080x1920:r=30:d={target_duration:.2f}",
        "-vf", "noise=alls=8:allf=t+u",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "raw_video.mp4"
    ], check=True)

def fetch_asmr_audio(matched_key, target_duration):
    if FREESOUND_API_KEY:
        try:
            query = urllib.parse.quote(ASMR_QUERY_MAP.get(matched_key, f"{search_keyword} ambient"))
            url = (
                f"https://freesound.org/apiv2/search/text/"
                f"?query={query}&filter=duration:[15 TO 180]&fields=id,name,previews"
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
        "-i", f"anoisesrc=d={target_duration:.2f}:c=pink:r=44100:a=0.10",
        "-af", "bandpass=f=900:width_type=h:w=700,tremolo=f=0.4:d=0.35,extrastereo=m=2.0",
        "asmr.mp3"
    ], check=True)

def fetch_relaxing_piano(target_duration):
    for filename in ["piano.mp3", "bgm.mp3", "audio/piano.mp3", "audio/default.mp3"]:
        if os.path.exists(filename):
            return filename

    if FREESOUND_API_KEY:
        try:
            query = urllib.parse.quote("soft relaxing piano calm ambient instrumental")
            url = (
                f"https://freesound.org/apiv2/search/text/"
                f"?query={query}&filter=duration:[20 TO 240]&fields=id,name,previews"
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
        "-f", "lavfi", "-i", f"sine=frequency=261.63:duration={target_duration:.2f}",
        "-f", "lavfi", "-i", f"sine=frequency=329.63:duration={target_duration:.2f}",
        "-f", "lavfi", "-i", f"sine=frequency=392.00:duration={target_duration:.2f}",
        "-f", "lavfi", "-i", f"sine=frequency=493.88:duration={target_duration:.2f}",
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

def render_ffmpeg(piano_file, vo_duration, total_duration):
    line_items, total_height = prepare_poetic_lines(overlay_text)
    quote_font = get_quote_font_path()

    fade_start = total_duration - FADE_DURATION
    text_fade_start = total_duration - FADE_DURATION
    text_fade_end = total_duration - 0.2

    # Slow down short clips smoothly so they cover the full dynamic total_duration
    raw_vid_duration = get_duration("raw_video.mp4")
    if 0.5 < raw_vid_duration < total_duration:
        pts_multiplier = (total_duration + 0.5) / raw_vid_duration
        print(f"Video is {raw_vid_duration:.2f}s (< {total_duration:.2f}s). Stretching speed (setpts={pts_multiplier:.4f}*PTS).")
    else:
        pts_multiplier = 1.0

    vo_start = VO_START_DELAY
    vo_end = vo_start + vo_duration + 0.3

    piano_duration = get_duration(piano_file)
    piano_start = random.uniform(0, piano_duration - total_duration - 1) if piano_duration > (total_duration + 4) else 0

    drawtext_chain = []

    # Left-aligned poetic lines (x=76, fontsize=45) starting at 0.5s with stanza spacing
    for idx, (fname, rel_y) in enumerate(line_items):
        start_t = 0.5 + (idx * 0.35)
        fade_in_end = start_t + 0.85
        line_alpha = (
            f"if(lt(t,{start_t:.2f}),0,"
            f"if(lt(t,{fade_in_end:.2f}),(t-{start_t:.2f})/0.85,"
            f"if(lt(t,{text_fade_start:.2f}),1,"
            f"if(lt(t,{text_fade_end:.2f}),({text_fade_end:.2f}-t)/1.3,0))))"
        )
        # Vertically positioned slightly above the bottom Reels caption area, just like the screenshot
        y_offset = f"(h-{total_height})/2-70+{rel_y}"
        drawtext_chain.append(
            f"drawtext=fontfile={quote_font}:"
            f"textfile={fname}:"
            f"fontcolor=#f5f5f3:"
            f"fontsize=45:"
            f"x=76:"
            f"y={y_offset}:"
            f"shadowcolor=black@0.85:shadowx=2:shadowy=3:"
            f"enable='gte(t,{start_t:.2f})':"
            f"alpha='{line_alpha}'"
        )

    drawtext_filters_str = ",".join(drawtext_chain)

    v_filter = (
        f"[0:v]setpts={pts_multiplier:.4f}*PTS,"
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,setsar=1,fps=30,"
        f"eq=brightness=-0.05:contrast=1.08:saturation=0.82,"
        f"colorbalance=rs=-0.02:bs=0.03:rh=0.03:gh=0.01:bh=-0.02,"
        f"noise=alls=4:allf=t+u,"
        f"vignette=PI/4,"
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.46:t=fill,"
        f"{drawtext_filters_str},"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={fade_start:.2f}:d={FADE_DURATION}[v]"
    )

    a_filter = (
        f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"lowpass=f=3000,volume='if(between(t,{vo_start:.2f},{vo_end:.2f}),0.16,0.35)':eval=frame[piano];"
        f"[2:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"volume='if(between(t,{vo_start:.2f},{vo_end:.2f}),0.20,0.42)':eval=frame[asmr];"
        f"[3:a]aformat=sample_rates=44100:channel_layouts=stereo,"
        f"adelay=2000|2000,apad=whole_dur={total_duration:.2f}[voice];"
        f"[piano][asmr][voice]amix=inputs=3:duration=first:dropout_transition=0:normalize=0,"
        f"afade=t=in:st=0:d=1.0,"
        f"afade=t=out:st={fade_start:.2f}:d={FADE_DURATION}[a]"
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
        "-t", f"{total_duration:.2f}",
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

    # Step 1: Generate Voiceover FIRST so we know its exact length
    generate_kokoro_asmr_voiceover(overlay_text)
    vo_duration = get_duration("voice.mp3")

    # Step 2: Calculate Dynamic Total Video Duration = 2.0s intro + full voiceover + 4.0s extra tail
    total_duration = max(12.0, round(VO_START_DELAY + vo_duration + EXTRA_TAIL_SECONDS, 2))
    print(f"Voiceover length: {vo_duration:.2f}s -> Dynamic Total Video Duration set to: {total_duration:.2f}s (includes +4.0s tail)")

    # Step 3: Download/generate visual & audio tracks matched to total_duration
    download_visual_background(total_duration)
    fetch_asmr_audio(matched_key, total_duration)
    piano_file = fetch_relaxing_piano(total_duration)

    # Step 4: Render final video with left-aligned poetic layout and dynamic duration
    render_ffmpeg(piano_file, vo_duration, total_duration)

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
