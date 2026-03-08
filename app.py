import os
import subprocess
import signal
import time
import platform
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, request, jsonify, send_from_directory

app = Flask(__name__)

# Setup file logging
log_file = os.path.join(os.path.dirname(__file__), 'streams', 'error.log')
os.makedirs(os.path.dirname(log_file), exist_ok=True)

file_handler = RotatingFileHandler(log_file, maxBytes=1048576, backupCount=3)
file_handler.setLevel(logging.ERROR)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
app.logger.addHandler(file_handler)

# Also log to console
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
app.logger.addHandler(console_handler)

app.logger.setLevel(logging.INFO)

# Global variable to hold the FFmpeg process
ffmpeg_process = None

# Directory where HLS files will be stored – using absolute path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HLS_DIR = os.path.join(BASE_DIR, 'streams')
os.makedirs(HLS_DIR, exist_ok=True)

app.logger.info(f"HLS directory: {HLS_DIR}")

def get_ffmpeg_command(video_device, audio_device, output_dir):
    """
    Build the FFmpeg command based on the operating system.
    Includes optional audio input.
    """
    system = platform.system()

    # Base settings
    cmd = ['ffmpeg', '-re']

    # Input specifications depend on OS
    if system == 'Linux':
        # Video input
        cmd.extend(['-f', 'v4l2', '-i', video_device])
        # Audio input (if provided)
        if audio_device:
            cmd.extend(['-f', 'alsa', '-i', audio_device])
    elif system == 'Darwin':  # macOS
        # Video input (video only, no audio)
        cmd.extend(['-f', 'avfoundation', '-i', f'{video_device}:none'])
        # Audio input (if provided) – separate input
        if audio_device:
            # On macOS, avfoundation input can be "none:audio_device" or separate? Usually combined.
            # For simplicity, we'll assume user wants both in one input if audio provided? Actually avfoundation can take video+audio combined.
            # But to keep it simple, we'll just handle video for now. Audio on macOS is more complex.
            app.logger.warning('Audio on macOS not fully implemented in this version')
    elif system == 'Windows':
        # Video input
        cmd.extend([
            '-f', 'dshow',
            '-rtbufsize', '100M',
            '-framerate', '30',
            '-i', f'video={video_device}'
        ])
        # Audio input (if provided)
        if audio_device:
            cmd.extend([
                '-f', 'dshow',
                '-rtbufsize', '100M',
                '-i', f'audio={audio_device}'
            ])
    else:
        raise Exception('Unsupported OS')

    # Video encoding settings
    video_settings = [
        '-c:v', 'libx264',
        '-preset', 'veryfast',
        '-tune', 'zerolatency',
        '-b:v', '1500k',
        '-maxrate', '2000k',
        '-bufsize', '3000k',
        '-pix_fmt', 'yuv420p',
        '-r', '30',
        '-s', '1280x720',
    ]
    cmd.extend(video_settings)

    # Audio encoding (if audio input present)
    if audio_device:
        cmd.extend([
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ar', '44100'
        ])

    # HLS output settings
    hls_settings = [
        '-f', 'hls',
        '-hls_time', '2',
        '-hls_list_size', '10',
        '-hls_flags', 'delete_segments',
        '-hls_segment_filename', os.path.join(output_dir, 'segment_%03d.ts'),
        os.path.join(output_dir, 'index.m3u8')
    ]
    cmd.extend(hls_settings)

    return cmd


@app.route('/')
def viewer():
    try:
        return render_template('viewer.html')
    except Exception as e:
        app.logger.exception('Viewer template error')
        return jsonify({'error': 'Viewer template missing'}), 500

@app.route('/admin')
def admin():
    try:
        return render_template('admin.html')
    except Exception as e:
        app.logger.exception('Admin template error')
        return jsonify({'error': 'Admin template missing'}), 500

@app.route('/start', methods=['POST'])
def start_stream():
    global ffmpeg_process
    try:
        app.logger.info('Received /start request')
        if ffmpeg_process and ffmpeg_process.poll() is None:
            return jsonify({'status': 'error', 'message': 'Broadcast already running'}), 400

        data = request.get_json()
        if not data:
            return jsonify({'status': 'error', 'message': 'No JSON data received'}), 400

        # Get video device name and strip any accidental quotes
        video_device = data.get('device', '').strip()
        video_device = video_device.strip('"').strip("'")
        if not video_device:
            return jsonify({'status': 'error', 'message': 'Camera device required'}), 400

        # Get audio device name (optional)
        audio_device = data.get('audio_device', '').strip()
        audio_device = audio_device.strip('"').strip("'") if audio_device else None

        app.logger.info(f'Video device: {video_device}, Audio device: {audio_device if audio_device else "None"}')

        # Ensure HLS directory is writable
        test_file = os.path.join(HLS_DIR, 'test_write.tmp')
        try:
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            app.logger.info('Streams directory is writable')
        except Exception as e:
            app.logger.exception('Cannot write to streams directory')
            return jsonify({'status': 'error', 'message': f'Cannot write to streams directory: {str(e)}'}), 500

        cmd = get_ffmpeg_command(video_device, audio_device, HLS_DIR)
        app.logger.info('Starting FFmpeg: %s', ' '.join(cmd))

        # Log file for FFmpeg stderr
        log_path = os.path.join(HLS_DIR, 'ffmpeg.log')
        with open(log_path, 'w') as log_file:
            ffmpeg_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0
            )

        # Give FFmpeg a moment to start
        time.sleep(2)
        if ffmpeg_process.poll() is not None:
            # Process exited immediately – read log for error
            with open(log_path, 'r') as f:
                error_log = f.read()
            app.logger.error('FFmpeg exited immediately. Log: %s', error_log)
            return jsonify({'status': 'error', 'message': f'FFmpeg exited: {error_log}'}), 500

        app.logger.info('Broadcast started successfully')
        return jsonify({'status': 'ok', 'message': 'Broadcast started'})

    except Exception as e:
        app.logger.exception('Unhandled exception in /start')
        return jsonify({'status': 'error', 'message': f'Server error: {str(e)}'}), 500

@app.route('/stop', methods=['POST'])
def stop_stream():
    global ffmpeg_process
    try:
        if not ffmpeg_process or ffmpeg_process.poll() is not None:
            return jsonify({'status': 'error', 'message': 'No broadcast running'}), 400

        ffmpeg_process.terminate()
        ffmpeg_process.wait(timeout=5)
        ffmpeg_process = None
        return jsonify({'status': 'ok', 'message': 'Broadcast stopped'})

    except subprocess.TimeoutExpired:
        ffmpeg_process.kill()
        ffmpeg_process = None
        return jsonify({'status': 'ok', 'message': 'Broadcast force-stopped'})
    except Exception as e:
        app.logger.exception('Unhandled exception in /stop')
        return jsonify({'status': 'error', 'message': f'Server error: {str(e)}'}), 500

@app.route('/status', methods=['GET'])
def stream_status():
    try:
        if ffmpeg_process and ffmpeg_process.poll() is None:
            return jsonify({'active': True})
        else:
            return jsonify({'active': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/streams/<path:filename>')
def serve_stream(filename):
    try:
        return send_from_directory(HLS_DIR, filename)
    except Exception as e:
        return jsonify({'error': 'File not found'}), 404

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
