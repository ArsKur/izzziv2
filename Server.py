import http.server
import urllib.request
import urllib.error
import json
import os
import ssl
import uuid
import re
import time

ssl._create_default_https_context = ssl._create_unverified_context

# На Render задаётся в Dashboard → Environment Variables
# Только Base64-строка БЕЗ слова "Basic"
GIGACHAT_KEY = os.environ.get('GIGACHAT_KEY', '').strip()
if GIGACHAT_KEY.lower().startswith('basic '):
    GIGACHAT_KEY = GIGACHAT_KEY[6:].strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

MIME_MAP = {
    '.html': 'text/html; charset=utf-8',
    '.css':  'text/css; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.json': 'application/json',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif':  'image/gif',
    '.svg':  'image/svg+xml',
    '.ico':  'image/x-icon',
    '.woff': 'font/woff',
    '.woff2':'font/woff2',
    '.ttf':  'font/ttf',
    '.md':   'text/plain; charset=utf-8',
}

def get_mime(path):
    ext = os.path.splitext(path)[1].lower()
    return MIME_MAP.get(ext, 'application/octet-stream')


# ── Кеш токена — живёт 28 минут, обновляется автоматически ───────────
_token_cache = {'token': None, 'expires': 0}

def get_gigachat_token():
    now = time.time()
    if _token_cache['token'] and now < _token_cache['expires']:
        return _token_cache['token']

    print('[GigaChat] Получаем новый токен...')
    req = urllib.request.Request(
        'https://ngw.devices.sberbank.ru:9443/api/v2/oauth',
        data=b'scope=GIGACHAT_API_PERS',
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
            'RqUID': str(uuid.uuid4()),
            'Authorization': f'Basic {GIGACHAT_KEY}'
        },
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())

    _token_cache['token'] = data['access_token']
    _token_cache['expires'] = now + 1700
    print('[GigaChat] Токен получен, действителен ~28 минут')
    return _token_cache['token']


# ── Генерация картинки с retry ────────────────────────────────────────
def gigachat_generate_image(query):
    """
    Возвращает (bytes, content_type) или (None, None).
    Делает 2 попытки с автообновлением токена.
    """
    for attempt in range(2):
        try:
            token = get_gigachat_token()
            prompt = f'Нарисуй картинку: {query}. Стиль: фотореалистичный. Формат: широкий 16:9.'

            chat_req = urllib.request.Request(
                'https://gigachat.devices.sberbank.ru/api/v1/chat/completions',
                data=json.dumps({
                    'model': 'GigaChat-2-Max',
                    'messages': [{'role': 'user', 'content': prompt}],
                    'function_call': 'auto',
                    'max_tokens': 3000,
                    'temperature': 0.7
                }).encode(),
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {token}'
                },
                method='POST'
            )
            with urllib.request.urlopen(chat_req, timeout=90) as resp:
                chat_data = json.loads(resp.read())

            content = chat_data.get('choices', [{}])[0].get('message', {}).get('content', '')
            print(f'[GigaChat image] attempt {attempt+1}, content: {content[:150]}')

            file_id = None
            if '<img' in content:
                match = re.search(r'src=["\']([^"\']+)["\']', content)
                if match:
                    file_id = match.group(1)

            if not file_id:
                attachments = chat_data.get('choices', [{}])[0].get('message', {}).get('attachments', [])
                if attachments:
                    file_id = attachments[0].get('id') or attachments[0].get('file_id')

            if not file_id:
                print(f'[GigaChat image] attempt {attempt+1}: file_id не найден, повтор...')
                _token_cache['expires'] = 0
                continue

            file_req = urllib.request.Request(
                f'https://gigachat.devices.sberbank.ru/api/v1/files/{file_id}/content',
                headers={
                    'Accept': 'application/jpg',
                    'Authorization': f'Bearer {token}'
                }
            )
            with urllib.request.urlopen(file_req, timeout=30) as file_resp:
                img_data = file_resp.read()
                content_type = file_resp.headers.get('Content-Type', 'image/jpeg')

            print(f'[GigaChat image] успешно, размер: {len(img_data)} байт')
            return img_data, content_type

        except Exception as e:
            print(f'[GigaChat image] attempt {attempt+1} error: {e}')
            _token_cache['expires'] = 0

    return None, None


class Handler(http.server.BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]

        if path == '/' or path == '/landing.html':
            self._serve_file('landing.html')
        elif path == '/app' or path == '/app/' or path == '/index.html':
            self._serve_file('index.html')
        elif path == '/health':
            # Render периодически пингует этот эндпоинт
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'status': 'ok',
                'gigachat': bool(GIGACHAT_KEY)
            }).encode())
        elif path.startswith('/templates/'):
            filename = os.path.basename(path)
            if not filename.endswith('.html') or '..' in path:
                self._send_404(); return
            self._serve_file_path(os.path.join(BASE_DIR, 'templates', filename))
        elif path.startswith('/assets/'):
            if '..' in path: self._send_404(); return
            self._serve_file_path(os.path.join(BASE_DIR, path[1:]))
        elif path.startswith('/generated/'):
            # На Render generated/ эфемерная — файлы пропадают при деплое.
            # Картинки отдаются напрямую через /image, HTML через /save-generated
            if '..' in path: self._send_404(); return
            self._serve_file_path(os.path.join(BASE_DIR, path[1:]))
        elif path.startswith('/images/'):
            if '..' in path: self._send_404(); return
            self._serve_file_path(os.path.join(BASE_DIR, path[1:]))
        elif path == '/image':
            # GET /image?q=... — генерируем и отдаём картинку напрямую
            qs = self.path.split('?')[1] if '?' in self.path else ''
            params = dict(p.split('=', 1) for p in qs.split('&') if '=' in p)
            query = urllib.request.unquote(params.get('q', 'business'))
            self._serve_gigachat_image(query)
        else:
            self._send_404()

    def _serve_file(self, fname):
        self._serve_file_path(os.path.join(BASE_DIR, fname))

    def _serve_file_path(self, filepath):
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            mime = get_mime(filepath)
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self._send_404()
        except Exception as e:
            print(f'File serve error: {e}')
            self.send_response(500)
            self.end_headers()

    def _send_404(self):
        self.send_response(404)
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'Not Found')

    def _send_svg_placeholder(self, text='генерация...'):
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
            f'<rect width="1280" height="720" fill="#1a1a2e"/>'
            f'<text x="640" y="360" text-anchor="middle" font-family="sans-serif" '
            f'font-size="20" fill="rgba(255,255,255,0.4)">{text}</text>'
            f'</svg>'
        ).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'image/svg+xml')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(svg)))
        self.end_headers()
        self.wfile.write(svg)

    def _serve_gigachat_image(self, query):
        try:
            img_data, content_type = gigachat_generate_image(query)
            if not img_data:
                self._send_svg_placeholder('изображение не сгенерировано')
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.send_header('Content-Length', str(len(img_data)))
            self.end_headers()
            self.wfile.write(img_data)
        except Exception as e:
            print(f'serve image error: {e}')
            self._send_svg_placeholder('ошибка генерации')

    def do_POST(self):

        # ── /token ────────────────────────────────────────────────────
        if self.path == '/token':
            try:
                token = get_gigachat_token()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'access_token': token}).encode())
            except Exception as e:
                print(f'Token error: {e}')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # ── /chat ─────────────────────────────────────────────────────
        elif self.path == '/chat':
            try:
                length = int(self.headers['Content-Length'])
                body = json.loads(self.rfile.read(length))
                messages = body['messages']
                token = body.get('token') or get_gigachat_token()

                req = urllib.request.Request(
                    'https://gigachat.devices.sberbank.ru/api/v1/chat/completions',
                    data=json.dumps({
                        'model': 'GigaChat-2',
                        'messages': messages,
                        'max_tokens': 3000,
                        'temperature': 0.7
                    }).encode(),
                    headers={
                        'Content-Type': 'application/json',
                        'Authorization': f'Bearer {token}'
                    },
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                print(f'Chat error: {e}')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # ── /generate-image ───────────────────────────────────────────
        # На Render НЕ сохраняем на диск — возвращаем base64
        # Тело: { "query": "..." }
        # Ответ: { "ok": true, "base64": "...", "content_type": "image/jpeg" }
        elif self.path == '/generate-image':
            try:
                length = int(self.headers['Content-Length'])
                body = json.loads(self.rfile.read(length))
                query = body.get('query', 'business')

                img_data, content_type = gigachat_generate_image(query)

                if not img_data:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'не удалось сгенерировать после 2 попыток'}).encode())
                    return

                import base64
                b64 = base64.b64encode(img_data).decode('utf-8')

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'ok': True,
                    'base64': b64,
                    'content_type': content_type,
                    # data-url удобно вставлять прямо в src=""
                    'data_url': f'data:{content_type};base64,{b64}'
                }).encode())

            except Exception as e:
                print(f'generate-image error: {e}')
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # ── /save-generated ───────────────────────────────────────────
        elif self.path == '/save-generated':
            try:
                length = int(self.headers['Content-Length'])
                body = json.loads(self.rfile.read(length))
                filename = os.path.basename(body.get('filename', 'site.html'))
                html = body.get('html', '')
                if not filename.endswith('.html'):
                    filename += '.html'
                gen_dir = os.path.join(BASE_DIR, 'generated')
                os.makedirs(gen_dir, exist_ok=True)
                with open(os.path.join(gen_dir, filename), 'w', encoding='utf-8') as f:
                    f.write(html)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True, 'url': f'/generated/{filename}'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f'[{self.address_string()}] {format % args}')


if __name__ == '__main__':
    # Render сам передаёт PORT — не меняйте эту строку
    port = int(os.environ.get('PORT', 8000))
    print(f'Сервер запущен: http://localhost:{port}/')
    print(f'  GET  /              -> лендинг')
    print(f'  GET  /app           -> конструктор')
    print(f'  GET  /health        -> health check')
    print(f'  GET  /image?q=...   -> GigaChat картинка (inline)')
    print(f'  POST /token         -> токен (из кеша или свежий)')
    print(f'  POST /chat          -> чат GigaChat (3000 токенов)')
    print(f'  POST /generate-image -> картинка -> base64 + data_url')
    print(f'  POST /save-generated -> сохранить HTML')
    print(f'GigaChat ключ: {"есть ✓" if GIGACHAT_KEY else "НЕТ ✗ — задайте GIGACHAT_KEY в Environment Variables"}')
    httpd = http.server.HTTPServer(('0.0.0.0', port), Handler)
    httpd.serve_forever()
