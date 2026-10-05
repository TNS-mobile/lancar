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

    st.markdown(
        """
        <style>
        /* =========================
           STREMQU — RESPONSIVE UI
           ========================= */
        :root { --radius: 14px; }

        .block-container {
            width: 100% !important;
            max-width: 920px !important;
            margin: 0 auto !important;
            padding: 1rem 1rem 2.5rem !important;
        }

        h1 {
            font-size: clamp(1.65rem, 5vw, 2.25rem) !important;
            line-height: 1.15 !important;
            margin-bottom: .35rem !important;
        }
        h4 {
            font-size: clamp(.95rem, 3vw, 1.1rem) !important;
            line-height: 1.45 !important;
            margin-top: 0 !important;
        }
        h2, h3 { line-height: 1.25 !important; }

        /* Semua input nyaman disentuh di HP */
        input, textarea, button,
        [data-baseweb="select"] > div,
        [data-testid="stFileUploaderDropzone"] {
            border-radius: var(--radius) !important;
        }

        input, textarea { font-size: 16px !important; }

        div[data-testid="stButton"] button,
        div[data-testid="stDownloadButton"] button {
            width: 100%;
            min-height: 48px;
            font-size: 1rem;
            font-weight: 700;
        }

        /* Upload area */
        [data-testid="stFileUploader"] {
            width: 100%;
        }
        [data-testid="stFileUploaderDropzone"] {
            min-height: 88px;
            padding: 14px !important;
            border: 1px dashed #9ca3af !important;
        }
        [data-testid="stFileUploaderDropzone"] [data-testid="stFileUploaderDropzoneInstructions"],
        [data-testid="stFileUploaderDropzone"] small {
            display: none !important;
        }
        [data-testid="stFileUploaderDropzone"]::after {
            content: "📱 Pilih video dari galeri HP • Maks. 300 MB";
            display: block;
            margin-top: 5px;
            font-size: .82rem;
            line-height: 1.3;
            color: #6b7280;
            text-align: center;
        }

        .mobile-card {
            border: 1px solid #e5e7eb;
            border-radius: 16px;
            padding: 12px;
            margin: 10px 0 14px;
            background: rgba(255,255,255,.03);
            box-shadow: 0 1px 5px rgba(0,0,0,.04);
        }
        .source-title {
            font-size: 1rem;
            font-weight: 750;
            line-height: 1.25;
            margin-bottom: 8px;
        }

        /* Jangan biarkan kolom memaksa horizontal scroll */
        [data-testid="stHorizontalBlock"] {
            width: 100% !important;
            gap: .65rem !important;
        }
        [data-testid="stHorizontalBlock"] > div {
            min-width: 0 !important;
        }

        /* Expander / info / success lebih rapat */
        [data-testid="stExpander"],
        [data-testid="stAlert"] {
            border-radius: var(--radius) !important;
        }

        @media (max-width: 640px) {
            .block-container {
                max-width: 100% !important;
                padding: .65rem .55rem 2rem !important;
            }

            h1 { font-size: 1.55rem !important; }
            h4 { font-size: .93rem !important; }
            h2 { font-size: 1.2rem !important; }
            h3 { font-size: 1.05rem !important; }

            [data-testid="stMarkdownContainer"] p,
            [data-testid="stCaptionContainer"] {
                font-size: .9rem;
            }

            .mobile-card {
                padding: 10px;
                margin: 8px 0 10px;
            }

            /* Tombol aksi menjadi vertikal di HP */
            [data-testid="stHorizontalBlock"]:has(div[data-testid="stButton"]) {
                flex-direction: column !important;
            }
            [data-testid="stHorizontalBlock"]:has(div[data-testid="stButton"]) > div {
                width: 100% !important;
                flex: 1 1 100% !important;
            }

            div[data-testid="stButton"] button {
                min-height: 50px;
            }

            [data-testid="stFileUploaderDropzone"] {
                min-height: 82px;
                padding: 10px !important;
            }
        }

        @media (max-width: 380px) {
            .block-container { padding-left: .4rem !important; padding-right: .4rem !important; }
            h1 { font-size: 1.4rem !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<h1><a href="https://www.youtube.com/@thexextsolutionid?sub_confirmation=1" target="_blank" style="text-decoration:none;">STREMQU by TNS</a></h1>',
        unsafe_allow_html=True
    )
    st.markdown(
        '<h4>Tools live streaming YouTube pribadi tanpa habiskan kuota, sewa RDP, VPS dll.</h4>',
        unsafe_allow_html=True
    )

    with st.expander("⚠️ DISCLAIMER", expanded=True):
        st.markdown("""
        - Layanan gratis mengikuti kebijakan dan batasan penyedia hosting.
        - Durasi streaming tergantung resource yang tersedia pada Streamlit.
        - Tidak menjamin jumlah view, penonton, subscriber, atau hasil tertentu.
        """)

    show_ads = st.checkbox("📢 Tampilkan Iklan", value=False)
    if show_ads:
        components.html(
            """
            <div style="background:#f0f2f6;padding:20px;border-radius:10px;text-align:center">
                <script type='text/javascript'
                        src='//pl26562103.profitableratecpm.com/28/f9/95/28f9954a1d5bbf4924abe123c76a68d2.js'>
                </script>
                <p style="color:#888">Iklan akan muncul di sini</p>
            </div>
            """,
            height=300
        )

    st.markdown("### 1. UPLOAD VIDEO / PLAYLIST")
    st.caption("Minimal 1 video • Maksimal 3 video • Video diputar berurutan.")
    st.info("📱 Pilih video dari galeri HP • Maksimal 300 MB per video")

    selected_paths = []

    for slot in range(1, 4):
        st.markdown(f'<div class="mobile-card"><div class="source-title">🎬 Video {slot}</div></div>', unsafe_allow_html=True)

        uploaded_file = st.file_uploader(
            f"Upload Video {slot} dari Galeri HP",
            type=["mp4", "flv", "mov", "mkv", "webm"],
            label_visibility="collapsed",
            key=f"video_uploader_{slot}",
            help=f"Video {slot}. Maksimal 300 MB.",
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
    is_shorts = st.checkbox("Mode Shorts (720x1280)")

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

