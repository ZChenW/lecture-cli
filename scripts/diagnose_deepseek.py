"""Bounded diagnostic with synthetic input. Never print secrets or classroom text."""
import argparse
import json
import os
from pathlib import Path
import signal
import time
from urllib.parse import urlsplit

import httpx
from lecture_cli.cli import configuration


def emit(**values):
    print(json.dumps(values, ensure_ascii=False), flush=True)


class ProbeDeadline(Exception):
    pass


def alarm(signum, frame):
    raise ProbeDeadline('probe deadline')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker-pid', type=int)
    parser.add_argument('--seconds', type=int, default=40)
    parser.add_argument('--mode', choices=('all', 'models', 'nonstream', 'stream'), default='all')
    args = parser.parse_args()
    config = configuration()
    key = os.environ.get('DEEPSEEK_API_KEY', '')
    if args.worker_pid:
        raw = Path(f'/proc/{args.worker_pid}/environ').read_bytes()
        worker_env = dict(entry.split(b'=', 1) for entry in raw.split(b'\0') if b'=' in entry)
        active_key = worker_env.get(b'DEEPSEEK_API_KEY', b'').decode()
        emit(active_key_matches_config=active_key == key)
        key = active_key or key
        for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'NO_PROXY', 'no_proxy'):
            value = worker_env.get(name.encode())
            os.environ.pop(name, None)
            if value:
                os.environ[name] = value.decode()
    if not key:
        raise SystemExit('No configured API key')
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        if os.environ.get(name):
            proxy = urlsplit(os.environ[name])
            emit(proxy_variable=name, proxy_scheme=proxy.scheme, proxy_host=proxy.hostname, proxy_port=proxy.port)
    signal.signal(signal.SIGALRM, alarm)
    headers = {'Authorization': f'Bearer {key}'}
    for mode in (('models', 'nonstream', 'stream') if args.mode == 'all' else (args.mode,)):
        began = time.monotonic()
        signal.alarm(args.seconds)
        bytes_seen = chunks = 0
        try:
            with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False) as client:
                if mode == 'models':
                    response = client.get('https://api.deepseek.com/models', headers=headers)
                    emit(mode=mode, http=response.status_code, elapsed=round(time.monotonic()-began, 2),
                         selected_model_listed=any(item.get('id') == config['model'] for item in response.json().get('data', [])))
                    continue
                body = dict(model=config['model'], messages=[dict(role='user', content='Reply only with OK.')],
                            max_tokens=16, thinking={'type': 'disabled'}, stream=mode == 'stream')
                with client.stream('POST', 'https://api.deepseek.com/chat/completions', headers=headers, json=body) as response:
                    emit(mode=mode, phase='headers', http=response.status_code, elapsed=round(time.monotonic()-began, 2),
                         content_type=response.headers.get('content-type'))
                    for chunk in response.iter_bytes():
                        chunks += 1
                        bytes_seen += len(chunk)
                        if chunks <= 4:
                            emit(mode=mode, phase='body', elapsed=round(time.monotonic()-began, 2),
                                 bytes=len(chunk), whitespace_only=not chunk.strip(),
                                 sse_data=b'data:' in chunk, keepalive=b'keep-alive' in chunk)
                    emit(mode=mode, phase='complete', elapsed=round(time.monotonic()-began, 2), bytes=bytes_seen)
        except (httpx.HTTPError, ProbeDeadline, ValueError) as exc:
            emit(mode=mode, error=type(exc).__name__, elapsed=round(time.monotonic()-began, 2), bytes=bytes_seen, chunks=chunks)
        finally:
            signal.alarm(0)


if __name__ == '__main__':
    main()
