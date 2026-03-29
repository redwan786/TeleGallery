"""
TeleGallery Backend — Stateless Flask API
All credentials come from request headers. Nothing stored in .env.
Deploy on Render as a Web Service.
"""

import os
import tempfile
import json
import asyncio
import queue
import threading
from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from supabase import create_client
from telegram_uploader import TelegramUploader
from pyrogram_uploader import PyrogramUploader

app = Flask(__name__)
CORS(app)

MAX_BOT_API_SIZE = 50 * 1024 * 1024  # 50 MB


def get_headers():
    """Extract credentials from request headers."""
    return {
        'bot_token': request.headers.get('X-Bot-Token', ''),
        'channel_id': request.headers.get('X-Channel-Id', ''),
        'supabase_url': request.headers.get('X-Supabase-Url', ''),
        'supabase_key': request.headers.get('X-Supabase-Key', ''),
        'api_id': request.headers.get('X-Api-Id', ''),
        'api_hash': request.headers.get('X-Api-Hash', ''),
    }


def get_supabase(creds):
    return create_client(creds['supabase_url'], creds['supabase_key'])


# ─── Health ───────────────────────────────────────────
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


# ─── Test Connections ─────────────────────────────────
@app.route('/api/setup/test', methods=['POST'])
def test_connections():
    creds = get_headers()
    results = {}

    # Test Telegram Bot
    try:
        uploader = TelegramUploader(creds['bot_token'])
        me = uploader.get_me()
        results['telegram'] = {'ok': True, 'bot': me.get('username', '')}
    except Exception as e:
        results['telegram'] = {'ok': False, 'error': str(e)}

    # Test Supabase
    try:
        sb = get_supabase(creds)
        sb.table('photos').select('id').limit(1).execute()
        results['supabase'] = {'ok': True}
    except Exception as e:
        results['supabase'] = {'ok': False, 'error': str(e)}

    all_ok = all(r.get('ok') for r in results.values())
    return jsonify({'ok': all_ok, 'results': results})


# ─── Upload ───────────────────────────────────────────
@app.route('/api/upload', methods=['POST'])
def upload_file():
    creds = get_headers()

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    album = request.form.get('album', 'All Photos')

    # Save to temp
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='_' + file.filename)
    file.save(tmp.name)
    tmp.close()

    try:
        file_size = os.path.getsize(tmp.name)
        uploader = TelegramUploader(creds['bot_token'])

        # Determine file type
        mime = file.content_type or ''
        if mime.startswith('video'):
            file_type = 'video'
        else:
            file_type = 'photo'

        # ALWAYS upload as document to preserve original quality (no compression)
        result = uploader.send_file(
            chat_id=creds['channel_id'],
            file_path=tmp.name,
            file_type='document',
        )

        # Extract file_id from document
        tg_doc = result.get('document', {})
        file_id = tg_doc.get('file_id', '')
        thumbnail_id = ''
        width = 0
        height = 0
        duration = 0

        # For photos, also send as photo to get a compressed thumbnail
        if file_type == 'photo':
            try:
                thumb_result = uploader.send_file(
                    chat_id=creds['channel_id'],
                    file_path=tmp.name,
                    file_type='photo',
                )
                photos = thumb_result.get('photo', [])
                if photos:
                    # Smallest size = thumbnail
                    thumbnail_id = photos[0].get('file_id', '')
                    # Largest size for dimensions
                    largest = photos[-1]
                    width = largest.get('width', 0)
                    height = largest.get('height', 0)
            except Exception:
                pass  # Thumbnail is optional

        # For videos, get metadata from document thumb
        if file_type == 'video':
            thumb = tg_doc.get('thumb') or tg_doc.get('thumbnail')
            if thumb:
                thumbnail_id = thumb.get('file_id', '')

        # Save to Supabase
        sb = get_supabase(creds)
        row = {
            'file_id': file_id,
            'file_type': file_type,
            'file_name': file.filename,
            'file_size': file_size,
            'mime_type': mime,
            'album': album,
            'thumbnail_id': thumbnail_id,
            'width': width,
            'height': height,
            'duration': duration,
        }
        sb.table('photos').insert(row).execute()

        return jsonify({'ok': True, 'file_id': file_id, 'row': row})

    finally:
        os.unlink(tmp.name)


# ─── Large File Upload (Pyrogram + SSE) ──────────────
@app.route('/api/upload/large', methods=['POST'])
def upload_large_file():
    """Upload files > 50MB via Pyrogram with SSE progress streaming."""
    creds = get_headers()

    if not creds.get('api_id') or not creds.get('api_hash'):
        return jsonify({'error': 'API ID and API Hash required for large file uploads'}), 400

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    album = request.form.get('album', 'All Photos')

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='_' + file.filename)
    file.save(tmp.name)
    tmp.close()

    file_size = os.path.getsize(tmp.name)
    mime = file.content_type or ''
    file_type = 'video' if mime.startswith('video') else 'photo'
    original_filename = file.filename

    progress_queue = queue.Queue()

    def run_pyrogram_upload():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            uploader = PyrogramUploader(
                api_id=creds['api_id'],
                api_hash=creds['api_hash'],
                bot_token=creds['bot_token'],
            )

            def on_progress(current, total):
                pct = round((current / total) * 100)
                progress_queue.put(('progress', pct))

            result = loop.run_until_complete(
                uploader.send_file(
                    chat_id=creds['channel_id'],
                    file_path=tmp.name,
                    file_type=file_type,
                    progress_callback=on_progress,
                )
            )
            loop.run_until_complete(uploader.disconnect())

            doc = result.get('document') or {}
            file_id = doc.get('file_id', '')
            thumbnail_id = ''
            width = 0
            height = 0

            thumb = doc.get('thumbnail')
            if thumb:
                thumbnail_id = thumb.get('file_id', '')

            photo_data = result.get('photo')
            if photo_data:
                if not thumbnail_id and photo_data.get('thumbs'):
                    thumbnail_id = photo_data['thumbs'][0].get('file_id', '')
                width = photo_data.get('width', 0)
                height = photo_data.get('height', 0)

            sb = get_supabase(creds)
            row = {
                'file_id': file_id,
                'file_type': file_type,
                'file_name': original_filename,
                'file_size': file_size,
                'mime_type': mime,
                'album': album,
                'thumbnail_id': thumbnail_id,
                'width': width,
                'height': height,
                'duration': 0,
            }
            sb.table('photos').insert(row).execute()
            progress_queue.put(('done', {'ok': True, 'file_id': file_id, 'row': row}))

        except Exception as e:
            progress_queue.put(('error', str(e)))
        finally:
            os.unlink(tmp.name)

    thread = threading.Thread(target=run_pyrogram_upload, daemon=True)
    thread.start()

    def generate_sse():
        while True:
            try:
                event_type, data = progress_queue.get(timeout=600)
                if event_type == 'progress':
                    yield f"data: {json.dumps({'type': 'progress', 'percent': data})}\n\n"
                elif event_type == 'done':
                    yield f"data: {json.dumps({'type': 'done', **data})}\n\n"
                    break
                elif event_type == 'error':
                    yield f"data: {json.dumps({'type': 'error', 'error': data})}\n\n"
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"

    return Response(
        stream_with_context(generate_sse()),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


# ─── Photos List ──────────────────────────────────────
@app.route('/api/photos', methods=['GET'])
def list_photos():
    creds = get_headers()
    sb = get_supabase(creds)

    album = request.args.get('album')
    favorites = request.args.get('favorites')
    trash = request.args.get('trash')

    query = sb.table('photos').select('*').order('uploaded_at', desc=True)

    if trash == 'true':
        query = query.eq('is_deleted', True)
    else:
        query = query.eq('is_deleted', False)

    if favorites == 'true':
        query = query.eq('is_favorite', True)

    if album and album != 'All Photos':
        query = query.eq('album', album)

    result = query.execute()
    return jsonify(result.data)


# ─── Search ───────────────────────────────────────────
@app.route('/api/photos/search', methods=['GET'])
def search_photos():
    creds = get_headers()
    sb = get_supabase(creds)
    q = request.args.get('q', '')

    if not q:
        return jsonify([])

    # Use ilike for simple search
    result = (
        sb.table('photos')
        .select('*')
        .eq('is_deleted', False)
        .or_(f"title.ilike.%{q}%,file_name.ilike.%{q}%,album.ilike.%{q}%")
        .order('uploaded_at', desc=True)
        .execute()
    )
    return jsonify(result.data)


# ─── File Stream (Telegram → Client) ─────────────────
@app.route('/api/file/<file_id>', methods=['GET'])
def stream_file(file_id):
    creds = get_headers()

    # Check if large file download requested (Pyrogram)
    use_pyrogram = request.args.get('pyrogram') == 'true'

    if use_pyrogram and creds.get('api_id') and creds.get('api_hash'):
        return _stream_file_pyrogram(file_id, creds)

    # Default: Bot API (works for files ≤ 20MB)
    uploader = TelegramUploader(creds['bot_token'])
    try:
        file_url = uploader.get_file_url(file_id)
        import requests as req
        resp = req.get(file_url, stream=True)

        content_type = resp.headers.get('Content-Type', 'application/octet-stream')

        def generate():
            for chunk in resp.iter_content(chunk_size=8192):
                yield chunk

        return Response(
            stream_with_context(generate()),
            content_type=content_type,
            headers={'Cache-Control': 'public, max-age=86400'},
        )
    except Exception as e:
        # If Bot API fails (likely >20MB), try Pyrogram fallback
        if creds.get('api_id') and creds.get('api_hash'):
            return _stream_file_pyrogram(file_id, creds)
        return jsonify({'error': str(e)}), 500


def _stream_file_pyrogram(file_id, creds):
    """Download and stream a file via Pyrogram MTProto (supports up to 2GB)."""
    import mimetypes

    tmp = tempfile.NamedTemporaryFile(delete=False, prefix='tg_dl_')
    tmp_path = tmp.name
    tmp.close()

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        uploader = PyrogramUploader(
            api_id=creds['api_id'],
            api_hash=creds['api_hash'],
            bot_token=creds['bot_token'],
        )

        loop.run_until_complete(uploader.download_file(file_id, tmp_path))
        loop.run_until_complete(uploader.disconnect())

        file_size = os.path.getsize(tmp_path)
        content_type = mimetypes.guess_type(tmp_path)[0] or 'application/octet-stream'

        def generate():
            try:
                with open(tmp_path, 'rb') as f:
                    while True:
                        chunk = f.read(65536)  # 64KB chunks
                        if not chunk:
                            break
                        yield chunk
            finally:
                os.unlink(tmp_path)

        return Response(
            stream_with_context(generate()),
            content_type=content_type,
            headers={
                'Cache-Control': 'public, max-age=86400',
                'Content-Length': str(file_size),
            },
        )
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return jsonify({'error': str(e)}), 500


# ─── Thumbnail Stream ────────────────────────────────
@app.route('/api/thumbnail/<file_id>', methods=['GET'])
def stream_thumbnail(file_id):
    return stream_file(file_id)


# ─── Favorite Toggle ─────────────────────────────────
@app.route('/api/photos/<int:photo_id>/favorite', methods=['PUT'])
def toggle_favorite(photo_id):
    creds = get_headers()
    sb = get_supabase(creds)

    current = sb.table('photos').select('is_favorite').eq('id', photo_id).single().execute()
    new_val = not current.data['is_favorite']
    sb.table('photos').update({'is_favorite': new_val}).eq('id', photo_id).execute()

    return jsonify({'ok': True, 'is_favorite': new_val})


# ─── Album Change ────────────────────────────────────
@app.route('/api/photos/<int:photo_id>/album', methods=['PUT'])
def change_album(photo_id):
    creds = get_headers()
    sb = get_supabase(creds)
    data = request.get_json()
    album = data.get('album', 'All Photos')

    sb.table('photos').update({'album': album}).eq('id', photo_id).execute()
    return jsonify({'ok': True})


# ─── Soft Delete ──────────────────────────────────────
@app.route('/api/photos/<int:photo_id>', methods=['DELETE'])
def soft_delete(photo_id):
    creds = get_headers()
    sb = get_supabase(creds)

    sb.table('photos').update({'is_deleted': True}).eq('id', photo_id).execute()
    return jsonify({'ok': True})


# ─── Permanent Delete ────────────────────────────────
@app.route('/api/photos/<int:photo_id>/permanent', methods=['DELETE'])
def permanent_delete(photo_id):
    creds = get_headers()
    sb = get_supabase(creds)

    sb.table('photos').delete().eq('id', photo_id).execute()
    return jsonify({'ok': True})


# ─── Restore from Trash ──────────────────────────────
@app.route('/api/photos/<int:photo_id>/restore', methods=['POST'])
def restore_photo(photo_id):
    creds = get_headers()
    sb = get_supabase(creds)

    sb.table('photos').update({'is_deleted': False}).eq('id', photo_id).execute()
    return jsonify({'ok': True})


# ─── Albums ──────────────────────────────────────────
@app.route('/api/albums', methods=['GET'])
def list_albums():
    creds = get_headers()
    sb = get_supabase(creds)

    result = sb.table('albums').select('*').order('created_at', desc=False).execute()
    return jsonify(result.data)


@app.route('/api/albums', methods=['POST'])
def create_album():
    creds = get_headers()
    sb = get_supabase(creds)
    data = request.get_json()
    name = data.get('name', '').strip()

    if not name:
        return jsonify({'error': 'Album name required'}), 400

    result = sb.table('albums').insert({'name': name}).execute()
    return jsonify(result.data[0] if result.data else {})


# ─── Run ──────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)