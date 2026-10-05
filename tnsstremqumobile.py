import sys
import subprocess
import threading
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path

# Install streamlit jika belum ada
try:
    import streamlit as st
    import streamlit.components.v1 as components
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "streamlit"])
    import streamlit as st
    import streamlit.components.v1 as components


APP_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = APP_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# Dipakai agar tombol Stop bisa menghentikan proses FFmpeg yang sedang aktif.
FFMPEG_PROCESS = None
PROCESS_LOCK = threading.Lock()


def safe_filename(name: str) -> str:
    """Buat nama file aman untuk disimpan di server."""
    name = Path(name).name
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ()"
    cleaned = "".join(c if c in allowed else "_" for c in name).strip()
    return cleaned or "video.mp4"


def save_uploaded_file(uploaded_file, slot: int) -> str:
    """Simpan upload ke folder uploads dengan nama slot agar urutannya jelas."""
    original = safe_filename(uploaded_file.name)
    stem = Path(original).stem
    suffix = Path(original).suffix.lower()
    filename = f"video_{slot}_{stem}{suffix}"
    path = UPLOAD_DIR / filename
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return str(path)


def make_concat_playlist(video_paths, repeat_count=1):
    """Buat playlist FFmpeg video sesuai jumlah slot, dengan jumlah putaran eksplisit."""
    playlist = UPLOAD_DIR / "playlist.txt"
    repeat_count = max(1, int(repeat_count))
    with open(playlist, "w", encoding="utf-8") as f:
        for _ in range(repeat_count):
            for path in video_paths:
                p = Path(path).resolve().as_posix().replace("'", "'\\''")
                f.write(f"file '{p}'\n")
    return str(playlist)


def make_audio_playlist(audio_paths, repeat_count=1):
    """Buat playlist audio yang diulang secara eksplisit agar jumlah putaran pasti."""
    playlist = UPLOAD_DIR / "audio_playlist.txt"
    repeat_count = max(1, int(repeat_count))
    with open(playlist, "w", encoding="utf-8") as f:
        for _ in range(repeat_count):
            for path in audio_paths:
                p = Path(path).resolve().as_posix().replace("'", "'\\''")
                f.write(f"file '{p}'\n")
    return str(playlist)


def run_ffmpeg(video_paths, stream_key, is_shorts, log_callback):
    """Streaming playlist 1-3 video tanpa batas."""
    global FFMPEG_PROCESS

    output_url = f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"

    if not video_paths:
        log_callback("ERROR: Minimal 1 video diperlukan.")
        return

    # Playlist video diputar berurutan lalu di-loop tanpa batas.
    playlist = make_concat_playlist(video_paths, 1)

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "info",
        "-re",
        "-stream_loop", "-1",
        "-f", "concat",
        "-safe", "0",
        "-i", playlist,
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-r", "20",
        "-pix_fmt", "yuv420p",
        "-profile:v", "main",
        "-threads", "2",
        "-b:v", "2500k",
        "-maxrate", "2500k",
        "-bufsize", "3600k",
        "-g", "50",
        "-keyint_min", "50",
        "-sc_threshold", "0",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "2",
        "-af", "aresample=async=1:first_pts=0",
        "-fps_mode", "cfr",
    ]

    if is_shorts:
        cmd += [
            "-vf",
            "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2",
        ]
    else:
        cmd += [
            "-vf",
            "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        ]

    cmd += [
        "-flvflags", "no_duration_filesize",
        "-muxdelay", "0",
        "-muxpreload", "0",
        "-f", "flv",
        output_url,
    ]

    log_callback("Mode: UPLOAD VIDEO / PLAYLIST")
    log_callback("Mode streaming: TANPA BATAS")
    log_callback("Urutan video:")
    for i, path in enumerate(video_paths, 1):
        log_callback(f"  {i}. {Path(path).name}")
    log_callback("Playlist akan berulang otomatis tanpa batas.")
    log_callback("Menjalankan FFmpeg ke YouTube...")

    try:
        with PROCESS_LOCK:
            FFMPEG_PROCESS = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

        process = FFMPEG_PROCESS
        for line in process.stdout:
            line = line.strip()
            if line:
                log_callback(line)
        process.wait()
        log_callback(f"FFmpeg berhenti dengan kode: {process.returncode}")
    except FileNotFoundError:
        log_callback("ERROR: FFmpeg tidak ditemukan. Pastikan FFmpeg sudah terpasang dan tersedia di PATH.")
    except Exception as e:
        log_callback(f"Error: {e}")
    finally:
        with PROCESS_LOCK:
            FFMPEG_PROCESS = None
        log_callback("Streaming selesai atau dihentikan.")

def stop_ffmpeg():
    global FFMPEG_PROCESS
    with PROCESS_LOCK:
        process = FFMPEG_PROCESS
        if process and process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            except Exception:
                pass
        FFMPEG_PROCESS = None


THEME_CSS = """
<style>
:root {
    --bg: #000000;
    --panel: #0c0c0c;
    --panel-2: #141414;
    --line: #2a2a2a;
    --red: #e50914;
    --red-dark: #8f0610;
    --red-soft: rgba(229, 9, 20, .14);
    --text: #f5f5f5;
    --muted: #9a9a9a;
    --radius: 14px;
}

/* ---------- Dasar halaman ---------- */
html, body, .stApp, [data-testid="stAppViewContainer"] {
    background: var(--bg) !important;
    color: var(--text) !important;
}
[data-testid="stHeader"] { background: transparent !important; }
[data-testid="stToolbar"], #MainMenu, footer { visibility: hidden; }

.block-container {
    width: 100% !important;
    max-width: 760px !important;
    margin: 0 auto !important;
    padding: 1rem 1rem calc(2.5rem + env(safe-area-inset-bottom, 0px)) !important;
}

/* ---------- Teks ---------- */
h1, h2, h3, h4, p, label, li, span,
[data-testid="stMarkdownContainer"],
[data-testid="stWidgetLabel"] {
    color: var(--text) !important;
}
h1 {
    font-size: clamp(1.6rem, 6vw, 2.3rem) !important;
    line-height: 1.15 !important;
    font-weight: 800 !important;
    margin-bottom: .3rem !important;
    padding-bottom: .55rem !important;
    border-bottom: 3px solid var(--red);
}
h1 a { color: var(--text) !important; text-decoration: none !important; }
h1 a:hover { color: var(--red) !important; }
h4 {
    font-size: clamp(.92rem, 3.2vw, 1.05rem) !important;
    line-height: 1.45 !important;
    font-weight: 500 !important;
    color: var(--muted) !important;
    margin-top: .2rem !important;
}
h3 {
    font-size: clamp(1rem, 3.8vw, 1.2rem) !important;
    line-height: 1.25 !important;
    font-weight: 750 !important;
    margin-top: 1.1rem !important;
    padding-left: .65rem;
    border-left: 4px solid var(--red);
}
[data-testid="stCaptionContainer"], small { color: var(--muted) !important; }

/* ---------- Input ---------- */
input, textarea { font-size: 16px !important; color: var(--text) !important; }
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="select"] > div {
    background: var(--panel-2) !important;
    border-radius: var(--radius) !important;
    border-color: var(--line) !important;
}
[data-baseweb="input"]:focus-within {
    border-color: var(--red) !important;
    box-shadow: 0 0 0 3px var(--red-soft) !important;
}
input::placeholder { color: #6b6b6b !important; }

/* ---------- Radio ---------- */
[data-testid="stRadio"] > div { gap: .5rem !important; flex-wrap: wrap; }
[data-testid="stRadio"] label {
    background: var(--panel-2);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: .65rem .9rem !important;
    min-height: 48px;
    align-items: center;
    flex: 1 1 240px;
}
[data-testid="stRadio"] label:has(input:checked) {
    border-color: var(--red);
    background: var(--red-soft);
}
[data-testid="stRadio"] [data-baseweb="radio"] > div:first-child { border-color: var(--red) !important; }
[data-testid="stRadio"] input:checked + div { background-color: var(--red) !important; }

/* ---------- Checkbox ---------- */
[data-testid="stCheckbox"] label { min-height: 44px; align-items: center; }
[data-testid="stCheckbox"] [data-baseweb="checkbox"] > span:first-child {
    border-color: var(--red) !important;
}
[data-testid="stCheckbox"] input:checked + div,
[data-testid="stCheckbox"] [aria-checked="true"] > span:first-child {
    background-color: var(--red) !important;
}

/* ---------- Tombol ---------- */
div[data-testid="stButton"] button,
div[data-testid="stDownloadButton"] button {
    width: 100%;
    min-height: 52px;
    font-size: 1rem;
    font-weight: 800;
    border-radius: var(--radius) !important;
    transition: transform .08s ease, background .15s ease;
}
div[data-testid="stButton"] button:active { transform: scale(.98); }

/* Mulai: merah solid */
[data-testid="stHorizontalBlock"] > div:nth-child(1) div[data-testid="stButton"] button {
    background: var(--red) !important;
    border: 1px solid var(--red) !important;
    color: #fff !important;
}
[data-testid="stHorizontalBlock"] > div:nth-child(1) div[data-testid="stButton"] button:hover:not(:disabled) {
    background: #ff1e2b !important;
}
/* Hentikan: outline merah */
[data-testid="stHorizontalBlock"] > div:nth-child(2) div[data-testid="stButton"] button {
    background: transparent !important;
    border: 2px solid var(--red) !important;
    color: var(--red) !important;
}
[data-testid="stHorizontalBlock"] > div:nth-child(2) div[data-testid="stButton"] button:hover:not(:disabled) {
    background: var(--red-soft) !important;
}
div[data-testid="stButton"] button:disabled {
    background: #1a1a1a !important;
    border-color: #2a2a2a !important;
    color: #5a5a5a !important;
}
button:focus-visible, input:focus-visible {
    outline: 2px solid var(--red) !important;
    outline-offset: 2px;
}

/* ---------- Upload ---------- */
[data-testid="stFileUploader"] { width: 100%; }
[data-testid="stFileUploaderDropzone"] {
    background: var(--panel) !important;
    min-height: 88px;
    padding: 14px !important;
    border: 1.5px dashed var(--red-dark) !important;
    border-radius: var(--radius) !important;
}
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--red) !important; }
[data-testid="stFileUploaderDropzone"] [data-testid="stFileUploaderDropzoneInstructions"],
[data-testid="stFileUploaderDropzone"] small { display: none !important; }
[data-testid="stFileUploaderDropzone"]::after {
    content: "📱 Pilih video dari galeri HP • Maks. 300 MB";
    display: block;
    margin-top: 5px;
    font-size: .82rem;
    line-height: 1.3;
    color: var(--muted);
    text-align: center;
}
[data-testid="stFileUploaderDropzone"] button {
    background: var(--red) !important;
    color: #fff !important;
    border: none !important;
}
[data-testid="stFileUploaderFile"] { color: var(--text) !important; }

/* ---------- Expander & alert ---------- */
[data-testid="stExpander"] {
    background: var(--panel) !important;
    border: 1px solid var(--line) !important;
    border-left: 4px solid var(--red) !important;
    border-radius: var(--radius) !important;
}
[data-testid="stExpander"] summary { font-weight: 700; }
[data-testid="stAlert"] {
    background: var(--panel-2) !important;
    border: 1px solid var(--line) !important;
    border-radius: var(--radius) !important;
}
[data-testid="stAlert"] * { color: var(--text) !important; }

/* ---------- Log ---------- */
[data-testid="stText"], .stText {
    background: var(--panel) !important;
    border: 1px solid var(--line);
    border-left: 4px solid var(--red);
    border-radius: var(--radius);
    padding: .75rem .9rem;
    color: #ff6b73 !important;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace !important;
    font-size: .78rem !important;
    line-height: 1.45;
    max-height: 320px;
    overflow: auto;
    white-space: pre-wrap !important;
    word-break: break-word;
}

/* ---------- Layout kolom ---------- */
[data-testid="stHorizontalBlock"] { width: 100% !important; gap: .65rem !important; }
[data-testid="stHorizontalBlock"] > div { min-width: 0 !important; }

/* ---------- HP ---------- */
@media (max-width: 640px) {
    .block-container {
        max-width: 100% !important;
        padding: .65rem .65rem calc(2rem + env(safe-area-inset-bottom, 0px)) !important;
    }
    h1 { font-size: 1.5rem !important; }
    h4 { font-size: .9rem !important; }
    h3 { font-size: 1.05rem !important; }
    [data-testid="stMarkdownContainer"] p,
    [data-testid="stCaptionContainer"] { font-size: .9rem; }

    /* Tombol aksi menjadi vertikal di HP */
    [data-testid="stHorizontalBlock"]:has(div[data-testid="stButton"]) { flex-direction: column !important; }
    [data-testid="stHorizontalBlock"]:has(div[data-testid="stButton"]) > div {
        width: 100% !important;
        flex: 1 1 100% !important;
    }
    div[data-testid="stButton"] button { min-height: 54px; }

    [data-testid="stFileUploaderDropzone"] { min-height: 82px; padding: 10px !important; }
    [data-testid="stRadio"] label { flex: 1 1 100%; }
}

@media (max-width: 380px) {
    .block-container { padding-left: .45rem !important; padding-right: .45rem !important; }
    h1 { font-size: 1.35rem !important; }
}

@media (prefers-reduced-motion: reduce) {
    * { transition: none !important; }
}
</style>
"""


def main():
    st.set_page_config(
        page_title="STREMQU | YouTube Live Streaming",
        page_icon="🎬",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.markdown(
        """
        <a href="/" alt="hit counter" target="_blank" style="display:none;">
            <img src="//sstatic1.histats.com/0.gif?5023868&101" alt="hit counter" border="0">
        </a>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(THEME_CSS, unsafe_allow_html=True)

    st.markdown(
        '<h1><a href="https://www.youtube.com/@thexextsolutionid?sub_confirmation=1" target="_blank" style="text-decoration:none;">STREMQU MOBILE by TNS</a></h1>',
        unsafe_allow_html=True
    )
    st.markdown(
        '<h4>Live Streaming Langsung dari Galeri HP tanpa habiskan kuota.</h4>',
        unsafe_allow_html=True
    )

    with st.expander("⚠️ DISCLAIMER", expanded=True):
        st.markdown("""
        - Tools ini mengikuti kebijakan dan batasan penyedia layanan.
        - Durasi streaming tergantung pada ukuran video dan kebijakan penyedia layanan.
        - Tidak menjamin jumlah view, subscriber, atau hasil tertentu.
        - Mohon gunakan layanan streaming gratis ini dengan bijak.
        """)

    show_ads = st.checkbox("📢 Tampilkan Iklan", value=False)
    if show_ads:
        components.html(
            """
            <div style="background:#141414;color:#9a9a9a;padding:20px;border-radius:14px;border:1px solid #2a2a2a;text-align:center;font-family:sans-serif">
                <script type='text/javascript'
                        src='//pl26562103.profitableratecpm.com/28/f9/95/28f9954a1d5bbf4924abe123c76a68d2.js'>
                </script>
                <p>Iklan akan muncul di sini</p>
            </div>
            """,
            height=300
        )

    st.markdown("### UPLOAD VIDEO / PLAYLIST")
    st.caption("Minimal 1 video • Maksimal 3 video • Maks. 300 MB per video")

    selected_paths = []

    for slot in range(1, 4):
        uploaded_file = st.file_uploader(
            f"Upload Video {slot}",
            type=["mp4", "flv", "mov", "mkv", "webm"],
            label_visibility="collapsed",
            key=f"video_uploader_{slot}",
            help="Maksimal 300 MB per video.",
        )

        if uploaded_file is not None:
            if uploaded_file.size > 300 * 1024 * 1024:
                st.error(f"Video {slot} melebihi batas 300 MB.")
            else:
                saved = save_uploaded_file(uploaded_file, slot)
                st.session_state[f"playlist_video_path_{slot}"] = saved
                st.success(f"Video {slot} siap: {uploaded_file.name}")

        selected = st.session_state.get(f"playlist_video_path_{slot}")
        if selected and Path(selected).exists():
            selected_paths.append(selected)
            st.caption(f"✅ Aktif: {Path(selected).name}")

    if selected_paths:
        st.write("**Playlist aktif:**")
        for i, path in enumerate(selected_paths, 1):
            st.write(f"{i}. {Path(path).name}")
    else:
        st.info("Belum ada video. Tambahkan minimal 1 video.")

    st.markdown("### 2. STREAM KEY YOUTUBE")
    stream_key = st.text_input(
        "Stream Key YouTube",
        type="password",
        label_visibility="collapsed",
        placeholder="Masukkan Stream Key YouTube",
    )

    st.markdown("### 3. TAMPILAN STREAMING")
    display_mode = st.radio(
        "Pilih tampilan streaming",
        ["Mode Horizontal / Landscape (16:9)", "Mode Vertikal / Portrait / Shorts (9:16)"],
        index=0,
        horizontal=True,
        label_visibility="collapsed",
    )
    is_shorts = display_mode.startswith("Mode Vertikal")

    # Streaming selalu Tanpa batas. Tidak ada menu jumlah pengulangan/durasi.
    playback_mode = "Tanpa batas"
    repeat_count = 1
    duration_hours = None


    log_placeholder = st.empty()
    logs = st.session_state.get("logs", [])

    def log_callback(msg):
        logs.append(msg)
        st.session_state["logs"] = logs[-100:]
        try:
            log_placeholder.text("\n".join(st.session_state["logs"][-20:]))
        except Exception:
            print(msg)

    streaming = FFMPEG_PROCESS is not None and FFMPEG_PROCESS.poll() is None

    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Mulai Streaming", disabled=streaming, use_container_width=True):
            if not selected_paths:
                st.error("Upload minimal 1 video terlebih dahulu!")
            elif len(selected_paths) > 3:
                st.error("Maksimal 3 video.")
            elif not stream_key:
                st.error("Stream Key YouTube harus diisi!")
            else:
                st.session_state["logs"] = []
                thread = threading.Thread(
                    target=run_ffmpeg,
                    args=(selected_paths, stream_key, is_shorts, log_callback),
                    daemon=True,
                )
                thread.start()
                time.sleep(0.5)
                st.success("Streaming dimulai ke YouTube!")

    with col2:
        if st.button("⏹️ Hentikan Streaming", disabled=not streaming, use_container_width=True):
            stop_ffmpeg()
            st.warning("Streaming dihentikan!")

    if FFMPEG_PROCESS is not None and FFMPEG_PROCESS.poll() is None:
        st.info("🔴 Streaming sedang berjalan")

    if st.session_state.get("logs"):
        log_placeholder.text("\n".join(st.session_state["logs"][-20:]))


if __name__ == '__main__':
    main()
