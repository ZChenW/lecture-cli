"""Generate an inspectable notes sample from synthetic text; no recording or ASR."""
import argparse
import json
from pathlib import Path
import tempfile
import time

from lecture_cli.cli import configuration
from lecture_cli.final_notes import generate
from lecture_cli.glossary import load_glossary
from lecture_cli.storage import Journal, atomic_text, write_json
from lecture_cli.worker import complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replay', type=Path, help='本地重放已保存的构造样例响应，不调用 API')
    args = parser.parse_args()
    saved = json.loads(args.replay.read_text()) if args.replay else None
    config = {'model': saved['responses'][0]['model']} if saved else configuration()
    fixture = Path(__file__).resolve().parents[1] / 'examples' / 'math481'
    records = json.loads((fixture / 'source.json').read_text())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    responses = []

    def generate_response(messages, model, tokens):
        if saved:
            entry = saved['responses'][len(responses)]
            if entry['max_tokens'] != tokens:
                raise ValueError('保存的响应与当前请求阶段不匹配')
            response = entry['response']
        else:
            response = complete(messages, model, tokens)
        responses.append(dict(model=model, max_tokens=tokens, response=response))
        return response
    with tempfile.TemporaryDirectory(prefix='lecture-preview-', dir='/tmp') as temporary:
        directory = Path(temporary)
        write_json(directory / 'session.json', dict(
            course='MATH481 构造验收样例', started='2026-09-25', demo=True,
            output=str(output), model=config['model'], **load_glossary(fixture)))
        atomic_text(directory / 'transcript.jsonl', ''.join(json.dumps(r) + '\n' for r in records))
        journal = Journal(directory)
        try:
            generate(journal, records, generate_response)
            journal.render(finished=True)
            status = dict(journal.db.execute('SELECT key, value FROM info'))['detail_status']
        finally:
            journal.close()
    if not saved:
        write_json(output.with_suffix('.responses.json'), dict(
            synthetic=True, elapsed_seconds=round(time.monotonic() - began, 2), responses=responses))
    mode = 'replayed responses' if saved else 'completed API calls'
    print(f'{status}: {output} ({len(responses)} {mode})')
    return 0 if status == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
