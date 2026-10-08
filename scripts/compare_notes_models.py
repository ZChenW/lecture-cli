"""Compare DeepSeek models using identical synthetic lecture text; no microphone."""
import asyncio
import json
import os
import time

import httpx
from lecture_cli.cli import configuration
from lecture_cli.final_notes import FINAL_SYSTEM

SOURCE = """课程：自造线性代数测试。
[L1] An eigenvector v is nonzero and satisfies A v equals lambda v. Without the nonzero condition every scalar would qualify.
[L2] For A equal to diag(2,3), write v=(x,y). The equations are 2x=lambda x and 3y=lambda y. For lambda=2, y=0 and x must be nonzero. For lambda=3, x=0 and y must be nonzero.
[L3] If lambda differs from both 2 and 3, both coordinates must be zero, a contradiction. The vector (1,1) is not an eigenvector because (2,3) is not its scalar multiple.
[L4] Correction: not every nonzero vector is an eigenvector of a diagonal matrix. Homework due Friday: analyze diag(4,4). Its answer was not given in class.
请完整保留以上信息，用约 500 字写详细中文笔记，不补写作业答案。"""


async def probe(model, key):
    began = time.monotonic()
    result = dict(model=model, first_text_s=None, content='', finish=None, keepalives=0)
    try:
        async with asyncio.timeout(90):
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
                async with client.stream('POST', 'https://api.deepseek.com/chat/completions',
                    headers={'Authorization': f'Bearer {key}'},
                    json=dict(model=model, messages=[dict(role='system', content=FINAL_SYSTEM),
                              dict(role='user', content=SOURCE)], thinking={'type':'disabled'},
                              stream=True, max_tokens=1800)) as response:
                    result['http'] = response.status_code
                    result['headers_s'] = round(time.monotonic()-began, 2)
                    if response.status_code != 200:
                        result['error'] = f'HTTP {response.status_code}'
                        return result
                    async for line in response.aiter_lines():
                        if line.startswith(':'):
                            result['keepalives'] += 1
                        if not line.startswith('data:'):
                            continue
                        data = line[5:].strip()
                        if data == '[DONE]': break
                        payload = json.loads(data)
                        for choice in payload.get('choices', []):
                            content = choice.get('delta', {}).get('content') or ''
                            if content and result['first_text_s'] is None:
                                result['first_text_s'] = round(time.monotonic()-began, 2)
                            result['content'] += content
                            if choice.get('finish_reason'):
                                result['finish'] = choice['finish_reason']
    except (TimeoutError, httpx.HTTPError, ValueError) as exc:
        result['error'] = type(exc).__name__
    finally:
        result['elapsed_s'] = round(time.monotonic()-began, 2)
    return result


async def main():
    configuration()
    key = os.environ['LECTURE_NOTES_API_KEY']
    results = await asyncio.gather(*(probe(model, key) for model in ('deepseek-flash', 'deepseek-v4-pro')))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
