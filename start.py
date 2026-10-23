# Render entrypoint; bot.py now includes the HTTP health server.
from bot import Health, db, poll, TOKEN
from http.server import ThreadingHTTPServer
import os, threading

if __name__ == '__main__':
    c=db();c.close()
    if TOKEN: threading.Thread(target=poll,daemon=True).start()
    else: print('BOT_TOKEN is not configured',flush=True)
    ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','10000'))),Health).serve_forever()
