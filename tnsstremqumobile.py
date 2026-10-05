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


def download_video_from_url(url: str, slot: int, filename_hint: str = "") -> str:
    """Download video dari URL langsung atau Google Drive ke server."""
    url = url.strip()
    if not url:
        raise ValueError("Link video kosong.")

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Link harus diawali http:// atau https://")

    # Google Drive: gunakan gdown agar link sharing file dapat diunduh.
    if "drive.google.com" in parsed.netloc or "docs.google.com" in parsed.netloc:
        try:
            import gdown
        except ImportError as exc:
            raise RuntimeError("Library gdown belum terpasang. Tambahkan gdown di requirements.txt.") from exc
        hint = safe_filename(filename_hint or f"video_{slot}.mp4")
        if not Path(hint).suffix:
            hint += ".mp4"
        target = UPLOAD_DIR / f"video_{slot}_drive_{hint}"
        result = gdown.download(url=url, output=str(target), quiet=True, fuzzy=True)
        if not result or not target.exists() or target.stat().st_size == 0:
            raise RuntimeError("Google Drive gagal diunduh. Pastikan file disetel 'Anyone with the link'.")
        if target.stat().st_size > 300 * 1024 * 1024:
            try:
                target.unlink()
            except Exception:
                pass
            raise ValueError("Ukuran video melebihi batas maksimal 300 MB.")
        return str(target)

    # URL file langsung (MP4/MKV/WebM, dll).
    hint = filename_hint.strip()
    if not hint:
        name = Path(urllib.parse.unquote(parsed.path)).name
        hint = name or f"video_{slot}.mp4"
    hint = safe_filename(hint)
    if not Path(hint).suffix:
        hint += ".mp4"
    target = UPLOAD_DIR / f"video_{slot}_link_{hint}"

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)

    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("Link tidak menghasilkan file video.")
    if target.stat().st_size > 300 * 1024 * 1024:
        try:
            target.unlink()
        except Exception:
            pass
        raise ValueError("Ukuran video melebihi batas maksimal 300 MB.")
    return str(target)


def save_uploaded_audio(uploaded_file, slot: int) -> str:
    """Simpan MP3 berdasarkan slot agar urutan playlist selalu 1 -> 5."""
    original = safe_filename(uploaded_file.name)
    stem = Path(original).stem
    suffix = Path(original).suffix.lower()
    filename = f"audio_{slot}_{stem}{suffix}"
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
           MOBILE FRIENDLY
           ========================= */
        .block-container {
            max-width: 900px;
            padding-top: 1rem;
            padding-left: 1rem;
            padding-right: 1rem;
        }

        [data-testid="stFileUploaderDropzone"] {
            border-radius: 12px;
            padding: 10px;
        }

        [data-testid="stFileUploaderDropzone"] [data-testid="stFileUploaderDropzoneInstructions"],
        [data-testid="stFileUploaderDropzone"] small {
            display: none !important;
        }

        [data-testid="stFileUploaderDropzone"]::after {
            content: "Maksimal 300 MB per video";
            display: block;
            margin-top: 4px;
            font-size: 0.82rem;
            color: #6b7280;
            text-align: center;
        }

        .mobile-card {
            border: 1px solid #e5e7eb;
            border-radius: 14px;
            padding: 12px;
            margin: 8px 0 14px 0;
            background: rgba(255,255,255,.02);
        }

        .source-title {
            font-size: 1rem;
            font-weight: 700;
            margin-bottom: 6px;
        }

        div[data-testid="stButton"] button,
        div[data-testid="stDownloadButton"] button {
            min-height: 44px;
            border-radius: 10px;
        }

        @media (max-width: 640px) {
            .block-container {
                padding: .75rem .65rem 2rem .65rem;
            }

            h1 {
                font-size: 1.65rem !important;
                line-height: 1.2 !important;
            }

            h2, h3 {
                line-height: 1.25 !important;
            }

            [data-testid="stHorizontalBlock"] {
                gap: .45rem;
            }

            [data-testid="stRadio"] label {
                font-size: .92rem !important;
            }

            .mobile-card {
                padding: 10px;
            }
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
    st.caption("Minimal 1 video • Maksimal 3 video • Video diputar berurutan dan otomatis Tanpa batas.")
    st.info("Upload dari galeri HP, Link langsung, atau Google Drive. Maksimal 300 MB per video.")

    selected_paths = []

    for slot in range(1, 4):
        st.markdown(f'<div class="mobile-card"><div class="source-title">🎬 Video {slot}</div></div>', unsafe_allow_html=True)

        source = st.radio(
            f"Sumber Video {slot}",
            ["Upload dari galeri HP", "Link langsung", "Google Drive"],
            horizontal=True,
            key=f"playlist_video_source_{slot}",
            label_visibility="collapsed",
        )

        if source == "Upload dari galeri HP":
            uploaded_file = st.file_uploader(
                f"Pilih Video {slot}",
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

        elif source == "Link langsung":
            video_url = st.text_input(
                f"URL Video {slot}",
                placeholder="https://contoh.com/video.mp4",
                key=f"playlist_video_url_{slot}",
                help="Gunakan direct link yang bisa diakses tanpa login.",
            )
            link_name = st.text_input(
                "Nama file (opsional)",
                placeholder=f"video_{slot}.mp4",
                key=f"playlist_video_name_{slot}",
            )
            if st.button(f"⬇️ Ambil Video {slot}", key=f"playlist_download_link_{slot}", use_container_width=True):
                if not video_url.strip():
                    st.error(f"Masukkan URL Video {slot} terlebih dahulu.")
                else:
                    try:
                        with st.spinner(f"Mengunduh Video {slot} ke server..."):
                            saved = download_video_from_url(video_url, slot, link_name)
                        st.session_state[f"playlist_video_path_{slot}"] = saved
                        st.success(f"Video {slot} siap: {Path(saved).name}")
                    except Exception as e:
                        st.error(f"Gagal mengambil Video {slot}: {e}")

        else:
            drive_url = st.text_input(
                f"Link Google Drive Video {slot}",
                placeholder="https://drive.google.com/file/d/.../view?usp=sharing",
                key=f"playlist_video_drive_url_{slot}",
                help="File Google Drive harus dapat diakses dengan 'Anyone with the link'.",
            )
            drive_name = st.text_input(
                "Nama file (opsional)",
                placeholder=f"video_{slot}.mp4",
                key=f"playlist_video_drive_name_{slot}",
            )
            if st.button(f"☁️ Ambil Video {slot}", key=f"playlist_download_drive_{slot}", use_container_width=True):
                if not drive_url.strip():
                    st.error(f"Masukkan link Google Drive Video {slot} terlebih dahulu.")
                else:
                    try:
                        with st.spinner(f"Mengunduh Video {slot} dari Google Drive ke server..."):
                            saved = download_video_from_url(drive_url, slot, drive_name)
                        st.session_state[f"playlist_video_path_{slot}"] = saved
                        st.success(f"Video {slot} siap: {Path(saved).name}")
                    except Exception as e:
                        st.error(f"Gagal mengambil Video {slot}: {e}")

        candidates = sorted(
            UPLOAD_DIR.glob(f"video_{slot}_*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            st.session_state[f"playlist_video_path_{slot}"] = str(candidates[0])

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

    st.caption("♾️ DURASI STREAMING: Tanpa batas")

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
                st.success("Streaming dimulai ke YouTube — Tanpa batas!")

    with col2:
        if st.button("⏹️ Hentikan Streaming", disabled=not streaming, use_container_width=True):
            stop_ffmpeg()
            st.warning("Streaming dihentikan!")

    if FFMPEG_PROCESS is not None and FFMPEG_PROCESS.poll() is None:
        st.info("🔴 Streaming sedang berjalan • ♾️ Tanpa batas")

    if st.session_state.get("logs"):
        log_placeholder.text("\n".join(st.session_state["logs"][-20:]))


if __name__ == '__main__':
    main()

