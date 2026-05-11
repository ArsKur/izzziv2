import http.server
import urllib.request
import urllib.error
import json
import os
import ssl
import uuid
import mimetypes

ssl._create_default_https_context = ssl._create_unverified_context

# Ключи берутся из переменных окружения Render — никогда не хардкодь их здесь
GIGACHAT_KEY = os.environ.get('GIGACHAT_KEY', '')
PEXELS_KEY   = os.environ.get('PEXELS_KEY', '')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# MIME-типы
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


class Handler(http.server.BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = self.path.split('?')[0]  # strip query params

        # ── Корень / лендинг ──────────────────────────────────────────
        if path == '/' or path == '/index.html':
            self._serve_file('index.html')

        # ── Шаблоны тем — /templates/*.html ───────────────────────────
        elif path.startswith('/templates/'):
            # Разрешаем только .html файлы из папки templates/
            filename = os.path.basename(path)
            if not filename.endswith('.html') or '..' in path:
                self._send_404()
                return
            filepath = os.path.join(BASE_DIR, 'templates', filename)
            self._serve_file_path(filepath)

        # ── Статические ассеты /assets/* ──────────────────────────────
        elif path.startswith('/assets/'):
            if '..' in path:
                self._send_404()
                return
            filepath = os.path.join(BASE_DIR, path[1:])
            self._serve_file_path(filepath)

        # ── Сгенерированные страницы /generated/* ─────────────────────
        elif path.startswith('/generated/'):
            if '..' in path:
                self._send_404()
                return
            filepath = os.path.join(BASE_DIR, path[1:])
            self._serve_file_path(filepath)

        # ── Изображения из папки /images/ ─────────────────────────────
        elif path.startswith('/images/'):
            if '..' in path:
                self._send_404()
                return
            filepath = os.path.join(BASE_DIR, path[1:])
            self._serve_file_path(filepath)

        # ── Прокси изображений через Pexels ───────────────────────────
        elif path == '/image':
            query_string = self.path.split('?')[1] if '?' in self.path else ''
            params = dict(p.split('=', 1) for p in query_string.split('&') if '=' in p)
            query = urllib.request.unquote(params.get('q', 'business'))
            self._serve_pexels_image(query)

        else:
            self._send_404()

    # ─── Отдать файл по имени относительно BASE_DIR ───────────────────
    def _serve_file(self, fname):
        filepath = os.path.join(BASE_DIR, fname)
        self._serve_file_path(filepath)

    # ─── Отдать файл по абсолютному пути ──────────────────────────────
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

    # ─── Pexels прокси ────────────────────────────────────────────────
    def _serve_pexels_image(self, query):
        try:
            req = urllib.request.Request(
                f'https://api.pexels.com/v1/search?query={urllib.request.quote(query)}&per_page=1&orientation=landscape',
                headers={
                    'Authorization': PEXELS_KEY,
                    'User-Agent': 'Mozilla/5.0',
                    'Accept': 'application/json'
                }
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())

            if data.get('photos') and len(data['photos']) > 0:
                src = data['photos'][0]['src']
                img_url = src.get('large2x') or src.get('large') or src.get('medium')
                img_req = urllib.request.Request(img_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(img_req, timeout=15) as img_resp:
                    img_data = img_resp.read()
                    content_type = img_resp.headers.get('Content-Type', 'image/jpeg')
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(img_data)))
                self.end_headers()
                self.wfile.write(img_data)
            else:
                self._send_404()

        except Exception as e:
            print(f'Image error: {e}')
            self.send_response(500)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()

    # ─── POST ─────────────────────────────────────────────────────────
    def do_POST(self):
        # GigaChat token
        if self.path == '/token':
            try:
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
                    data = resp.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # GigaChat chat
        elif self.path == '/chat':
            try:
                length = int(self.headers['Content-Length'])
                body = json.loads(self.rfile.read(length))
                token = body['token']
                messages = body['messages']

                req = urllib.request.Request(
                    'https://gigachat.devices.sberbank.ru/api/v1/chat/completions',
                    data=json.dumps({
                        'model': 'GigaChat-2',
                        'messages': messages,
                        'max_tokens': 1200,
                        'temperature': 0.7
                    }).encode(),
                    headers={
                        'Content-Type': 'application/json',
                        'Authorization': f'Bearer {token}'
                    },
                    method='POST'
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_response(500)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())

        # Опционально: сохранить сгенерированный HTML
        # POST /save-generated — тело: { "filename": "my-site.html", "html": "..." }
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

                filepath = os.path.join(gen_dir, filename)
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(html)

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'ok': True,
                    'url': f'/generated/{filename}'
                }).encode())
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
    port = int(os.environ.get('PORT', 8000))
    print(f'Сервер запущен на порту {port}')
    print(f'Структура проекта:')
    print(f'  {BASE_DIR}/index.html          — конструктор')
    print(f'  {BASE_DIR}/templates/*.html    — шаблоны тем')
    print(f'  {BASE_DIR}/assets/             — статика')
    print(f'  {BASE_DIR}/generated/          — сохранённые сайты')
    httpd = http.server.HTTPServer(('0.0.0.0', port), Handler)
    httpd.serve_forever()
