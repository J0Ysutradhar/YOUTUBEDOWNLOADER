import os
import re
import json
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, Response, stream_with_context, session
from pytubefix import YouTube
from pytube.exceptions import RegexMatchError, VideoUnavailable
from datetime import datetime # For footer year

app = Flask(__name__)
app.secret_key = os.urandom(24) # Needed for session management if you use it. Good practice.

# --- Configuration ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DOWNLOAD_FOLDER = os.path.join(BASE_DIR, 'downloads')
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

# --- Global dictionary to store progress (simplified for demo) ---
# For a multi-user app, this needs to be more robust (e.g., session-based or unique IDs)
download_progress_store = {}

# --- Helper Functions ---
def sanitize_filename(title):
    """Cleans a title to be a valid filename."""
    title = re.sub(r'[\\/*?:"<>|]', "", title) # Remove invalid characters
    title = title.replace(" ", "_") # Replace spaces with underscores
    return title[:100] # Limit length

def format_duration(seconds):
    """Formats seconds into HH:MM:SS or MM:SS."""
    if seconds is None: return "N/A"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    if hours > 0:
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"

def get_progress_key(video_id, itag, stream_type):
    """Generates a unique key for progress tracking."""
    return f"{video_id}_{itag}_{stream_type}"

# --- Pytube Progress Callback ---
def on_progress_callback(stream, chunk, bytes_remaining):
    """Pytube progress callback function."""
    total_size = stream.filesize
    bytes_downloaded = total_size - bytes_remaining
    percentage = (bytes_downloaded / total_size) * 100
    
    # Update global progress store
    # Important: The key needs to be known by the callback context.
    # We'll set it in session or pass it somehow before starting download.
    progress_key = session.get('current_progress_key')
    if progress_key and progress_key in download_progress_store:
        download_progress_store[progress_key]['progress'] = percentage
        download_progress_store[progress_key]['downloaded_mb'] = bytes_downloaded / (1024 * 1024)
        download_progress_store[progress_key]['total_mb'] = total_size / (1024 * 1024)
    # print(f"Progress for {progress_key}: {percentage:.2f}%")


# --- Routes ---
@app.route('/', methods=['GET', 'POST'])
def index():
    video_info = None
    error = None
    youtube_url_input = ""

    if request.method == 'POST':
        youtube_url_input = request.form.get('youtube_url')
        if not youtube_url_input:
            error = "Please enter a YouTube URL."
        else:
            try:
                yt = YouTube(youtube_url_input, on_progress_callback=on_progress_callback)

                # Video streams (progressive MP4, sorted by resolution)
                video_streams_data = []
                # Prioritize progressive streams as they contain audio and video
                progressive_streams = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc()
                
                # If no progressive, consider adaptive (more complex, would need merging or separate audio)
                # For simplicity, we'll focus on progressive for now for combined A/V
                # You could extend this to list adaptive video-only streams too.
                
                for stream in progressive_streams:
                     if stream.resolution: # Ensure resolution is available
                        video_streams_data.append({
                            'itag': stream.itag,
                            'resolution': stream.resolution,
                            'filesize': stream.filesize,
                            'subtype': stream.subtype
                        })

                # Audio stream (highest quality)
                audio_stream_data = None
                best_audio = yt.streams.filter(only_audio=True, file_extension='mp4').order_by('abr').desc().first()
                # Pytube might default to 'webm' for audio sometimes, mp4 is more compatible.
                # If no mp4 audio, try webm or others if desired.
                if not best_audio: # fallback to webm if no mp4 audio
                    best_audio = yt.streams.filter(only_audio=True).order_by('abr').desc().first()

                if best_audio:
                    audio_stream_data = {
                        'itag': best_audio.itag,
                        'abr': best_audio.abr,
                        'filesize': best_audio.filesize,
                        'subtype': best_audio.subtype # e.g., 'mp4' or 'webm'
                    }

                video_info = {
                    'video_id': yt.video_id,
                    'title': yt.title,
                    'author': yt.author,
                    'thumbnail_url': yt.thumbnail_url,
                    'length_formatted': format_duration(yt.length),
                    'views': yt.views,
                    'description': yt.description,
                    'video_streams': video_streams_data,
                    'audio_stream': audio_stream_data,
                    'filename_base': sanitize_filename(yt.title)
                }

            except RegexMatchError:
                error = "Invalid YouTube URL format. Please check the link."
            except VideoUnavailable:
                error = "The video is unavailable. It might be private, deleted, or restricted."
            except Exception as e:
                print(f"An unexpected error occurred: {e}")
                error = f"An error occurred: {str(e)}. Please try again."
    
    current_year = datetime.now().year
    return render_template('index.html', video_info=video_info, error=error, youtube_url=youtube_url_input, annee_actuelle=current_year)


@app.route('/download/<string:video_id>/<int:itag>/<string:type>')
def download_file(video_id, itag, type):
    """
    This route is hit by the 'a' tag's href. It downloads the file from YouTube
    and then sends it to the client. The progress for *this* download from YouTube
    is what the /progress SSE endpoint will monitor.
    """
    progress_key = get_progress_key(video_id, itag, type)
    session['current_progress_key'] = progress_key # Make key available to on_progress_callback
    
    download_progress_store[progress_key] = {
        'progress': 0, 
        'status': 'starting',
        'filename': 'N/A',
        'downloaded_mb': 0,
        'total_mb': 0
    }

    try:
        # Construct the full URL. Pytube needs the original URL or video ID to fetch streams.
        # Using video_id is safer if the original URL had playlist info etc.
        youtube_url = f"https://www.youtube.com/watch?v={video_id}"
        yt = YouTube(youtube_url, on_progress_callback=on_progress_callback)
        stream = yt.streams.get_by_itag(itag)

        if not stream:
            download_progress_store[progress_key]['status'] = 'error'
            download_progress_store[progress_key]['error'] = 'Stream not found.'
            # This error won't be directly sent to client via this route,
            # but the SSE /progress route can pick it up.
            return "Error: Stream not found.", 404

        filename = f"{sanitize_filename(yt.title)}.{stream.subtype}"
        if type == 'video' and stream.resolution:
             filename = f"{sanitize_filename(yt.title)}_{stream.resolution}.{stream.subtype}"
        
        download_progress_store[progress_key]['filename'] = filename
        download_progress_store[progress_key]['status'] = 'downloading'
        
        # Download to a temporary file in the DOWNLOAD_FOLDER
        # Pytube's download method blocks until complete.
        # The on_progress_callback will update download_progress_store during this.
        filepath = stream.download(output_path=DOWNLOAD_FOLDER, filename=filename)
        
        download_progress_store[progress_key]['status'] = 'completed'
        download_progress_store[progress_key]['progress'] = 100
        
        # Serve the file
        # Using as_attachment=True prompts the browser to download
        response = send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=True)
        
        # Clean up the session key for progress after download attempt
        # if 'current_progress_key' in session:
        #     del session['current_progress_key']
        # Don't delete the progress_key from download_progress_store immediately,
        # let the SSE serve the 'completed' status a few times.
        # Consider a cleanup mechanism for old entries in download_progress_store.

        return response

    except Exception as e:
        print(f"Error during download for {progress_key}: {e}")
        if progress_key in download_progress_store:
            download_progress_store[progress_key]['status'] = 'error'
            download_progress_store[progress_key]['error'] = str(e)
        # This error is harder to show to the user directly from this route
        # if send_file hasn't started. The SSE route is better for status updates.
        return f"An error occurred: {str(e)}", 500


@app.route('/progress/<string:video_id>/<int:itag>/<string:type>')
def progress_feed(video_id, itag, type):
    """Server-Sent Events endpoint for download progress."""
    progress_key = get_progress_key(video_id, itag, type)

    def generate_progress():
        # Initialize if not present (e.g. if SSE connects before download route fully sets up)
        if progress_key not in download_progress_store:
            download_progress_store[progress_key] = {
                'progress': 0, 
                'status': 'initializing', 
                'filename': 'N/A',
                'downloaded_mb': 0,
                'total_mb': 0
            }
            yield f"data: {json.dumps(download_progress_store[progress_key])}\n\n"


        last_progress = -1
        while True:
            # Check if the key exists and has data
            if progress_key in download_progress_store:
                current_data = download_progress_store[progress_key].copy() # Work with a copy
                current_progress = current_data.get('progress', 0)

                # Send update only if progress changed or status changed
                if current_progress != last_progress or current_data.get('status') == 'error' or current_data.get('status') == 'completed':
                    yield f"data: {json.dumps(current_data)}\n\n"
                    last_progress = current_progress
                
                if current_data.get('status') == 'completed' or current_data.get('status') == 'error':
                    # print(f"SSE: '{current_data.get('status')}' state for {progress_key}, closing stream.")
                    # After sending final status, can break or mark for cleanup
                    # For simplicity, we'll just break here. Client will close connection.
                    # In a real app, you might want to remove progress_key from download_progress_store after a timeout
                    break 
            else: # Key disappeared or was never there, possibly an issue or very fast download
                yield f"data: {json.dumps({'progress': 0, 'status': 'pending_start_or_error'})}\n\n"
                # This state indicates the download might not have started its tracking yet or an issue.
                # If it persists, client-side should handle it.
            
            # Polling interval for SSE updates
            # Pytube's on_progress callback is event-driven, but SSE needs to poll the store
            # This might be too frequent for many updates, adjust as needed.
            # time.sleep(0.2) # Using Flask's stream_with_context handles this better
            # For this to work well without explicit sleep, the callback should signal an event or use a queue.
            # Given the current structure, a small sleep in the loop would be more traditional for polling.
            # However, with stream_with_context, it's more about yielding when data is ready.
            # The while True loop will check rapidly.
            # If no data, consider a small sleep, e.g., import time; time.sleep(0.1)
            
            # For a robust solution, consider using Flask-SocketIO or a message queue.
            # For this demo, this polling approach with SSE is a balance.
            # Let's add a small delay to prevent tight looping if no progress
            import time
            time.sleep(0.25) # Check for updates 4 times a second

    return Response(stream_with_context(generate_progress()), mimetype='text/event-stream')

@app.route("/privacy/")
def privacy():
    return  render_template('privacy&policy.html')



@app.context_processor
def inject_current_year():
    return {'annee_actuelle': datetime.now().year}

if __name__ == '__main__':
    # Ensure DOWNLOAD_FOLDER exists
    if not os.path.exists(DOWNLOAD_FOLDER):
        os.makedirs(DOWNLOAD_FOLDER)
    app.run(host="0.0.0.0",debug=True, threaded=True) # threaded=True is important for concurrent requests (SSE + download)