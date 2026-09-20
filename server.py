#!/usr/bin/env python3
import json, sqlite3, pathlib
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent
DB = ROOT / 'db' / 'game.db'
PUBLIC = ROOT / 'public'
MIN_CORRECT = 7

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(PUBLIC), **k)

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}')

    def do_GET(self):
        path = urlparse(self.path).path
        con = db()
        try:
            if path == '/api/levels':
                prog = con.execute("SELECT highest_cleared FROM progress WHERE player_id='default'").fetchone()[0]
                rows = con.execute('SELECT level_id, title FROM levels ORDER BY level_id').fetchall()
                out = []
                for r in rows:
                    out.append({
                        'levelId': r['level_id'],
                        'title': r['title'],
                        'unlocked': r['level_id'] <= prog + 1,
                        'cleared': r['level_id'] <= prog,
                    })
                return self._json(200, out)
            if path.startswith('/api/levels/') and path.endswith('/questions'):
                lid = int(path.split('/')[3])
                qs = con.execute('SELECT id, type, stem, options FROM questions WHERE level_id=? ORDER BY id', (lid,)).fetchall()
                return self._json(200, [{
                    'id': q['id'], 'type': q['type'], 'stem': q['stem'], 'options': json.loads(q['options'])
                } for q in qs])
            if path == '/api/wrong-book':
                rows = con.execute('''SELECT w.question_id, w.level_id, q.stem, q.options, q.type
                    FROM wrong_book w JOIN questions q ON q.id=w.question_id ORDER BY w.added_at''').fetchall()
                return self._json(200, [{
                    'id': r['question_id'], 'levelId': r['level_id'], 'stem': r['stem'],
                    'options': json.loads(r['options']), 'type': r['type']
                } for r in rows])
            if path == '/api/progress':
                prog = con.execute("SELECT highest_cleared FROM progress WHERE player_id='default'").fetchone()[0]
                n = con.execute('SELECT COUNT(*) FROM wrong_book').fetchone()[0]
                return self._json(200, {'highestCleared': prog, 'wrongBookCount': n})
        finally:
            con.close()
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        data = self._read()
        con = db()
        try:
            if path.startswith('/api/levels/') and path.endswith('/submit'):
                lid = int(path.split('/')[3])
                answers = data.get('answers') or {}  # {qid: index}
                qs = con.execute('SELECT id, answer_index, explain FROM questions WHERE level_id=?', (lid,)).fetchall()
                correct = 0
                detail = []
                for q in qs:
                    picked = answers.get(q['id'])
                    ok = picked == q['answer_index']
                    if ok:
                        correct += 1
                    else:
                        con.execute('INSERT OR REPLACE INTO wrong_book(question_id, level_id, added_at) VALUES(?,?,datetime("now"))', (q['id'], lid))
                    detail.append({'id': q['id'], 'correct': ok, 'answerIndex': q['answer_index'], 'explain': q['explain']})
                passed = correct >= MIN_CORRECT
                if passed:
                    prog = con.execute("SELECT highest_cleared FROM progress WHERE player_id='default'").fetchone()[0]
                    if lid > prog:
                        con.execute("UPDATE progress SET highest_cleared=? WHERE player_id='default'", (lid,))
                con.commit()
                return self._json(200, {'correct': correct, 'total': len(qs), 'passed': passed, 'detail': detail})
            if path == '/api/wrong-book/submit':
                answers = data.get('answers') or {}
                cleared = []
                remaining = []
                for qid, picked in answers.items():
                    row = con.execute('SELECT answer_index FROM questions WHERE id=?', (qid,)).fetchone()
                    if not row:
                        continue
                    if picked == row['answer_index']:
                        con.execute('DELETE FROM wrong_book WHERE question_id=?', (qid,))
                        cleared.append(qid)
                    else:
                        remaining.append(qid)
                con.commit()
                # do not touch progress
                return self._json(200, {'cleared': cleared, 'remaining': remaining})
            if path == '/api/reset':
                con.execute("UPDATE progress SET highest_cleared=0 WHERE player_id='default'")
                con.execute('DELETE FROM wrong_book')
                con.commit()
                return self._json(200, {'ok': True})
        finally:
            con.close()
        self._json(404, {'error': 'not found'})

if __name__ == '__main__':
    PORT = 43210
    print(f'Serving on http://127.0.0.1:{PORT}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), H).serve_forever()
