import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from bot import main

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ('/', '/health'):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b'Bot process running')

    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    if os.getenv('BOT_TOKEN'):
        threading.Thread(target=main, daemon=True).start()
    else:
        print('BOT_TOKEN is missing: HTTP health server only', flush=True)
    port = int(os.getenv('PORT', '10000'))
    ThreadingHTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()
