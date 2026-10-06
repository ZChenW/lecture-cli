"""Offline sustained ASR probe. Repeats a supplied fixture; never uses a mic or API."""
import argparse
import asyncio
import json
import os
import time
import sys


from lecture_cli.asr import backend, runtime_environment, select_device

runtime_env = runtime_environment()
if runtime_env.get('LD_LIBRARY_PATH') != os.environ.get('LD_LIBRARY_PATH'):
    os.execve(sys.executable, [sys.executable, *sys.argv], runtime_env)
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ.setdefault('OMP_NUM_THREADS', '4')

async def main(args):
    from whisperlivekit.test_harness import TestHarness, load_audio_pcm
    pcm = load_audio_pcm(args.audio) * args.repeat
    kwargs = dict(model_size=args.asr_model, lan='en', backend='faster-whisper',
                  backend_policy='localagreement', pcm_input=True, diarization=False,
                  asr_coalesce_min_s=args.coalesce)
    if args.chunk: kwargs['min_chunk_size'] = args.chunk
    peak_lag = 0
    words = 0
    print('loading ASR', file=sys.stderr, flush=True)
    device, notice = select_device(args.asr_device)
    with backend(device):
        h = TestHarness(**kwargs)
        await h.__aenter__()
    loaded_device = h._processor.transcription.asr.model.model.device
    print(json.dumps(dict(requested=args.asr_device, loaded_device=loaded_device, notice=notice)), file=sys.stderr, flush=True)
    try:
        last_report = 0
        def update(state):
            nonlocal peak_lag, words, last_report
            peak_lag = max(peak_lag, state.remaining_time_transcription_processing)
            words = state.committed_word_count
            if state.audio_position - last_report >= 30:
                print(json.dumps(dict(audio_s=round(state.audio_position,1), lag_s=state.remaining_time_transcription_processing, words=words)), file=sys.stderr, flush=True)
                last_report = state.audio_position
        h.on_update(update)
        began = time.monotonic()
        for offset in range(0,len(pcm),16000):
            await h.feed_pcm(pcm[offset:offset+16000], speed=args.speed)
        feed_time=time.monotonic()-began
        await h.finish(timeout=180)
        durations=h.metrics.transcription_durations
        print(json.dumps(dict(device=loaded_device, model=args.asr_model, coalesce=args.coalesce,chunk=args.chunk,audio_s=len(pcm)/32000,
                              elapsed_s=round(time.monotonic()-began,2),feed_s=round(feed_time,2),
                              peak_lag_s=peak_lag,calls=len(durations),inference_s=round(sum(durations),2),
                              speed=args.speed, words=words,text=h.state.committed_text),ensure_ascii=False),flush=True)
    finally:
        await h.__aexit__(None,None,None)

p=argparse.ArgumentParser()
p.add_argument('audio')
p.add_argument('--repeat',type=int,default=6)
p.add_argument('--coalesce',type=float,default=0)
p.add_argument('--chunk',type=float,default=0)
p.add_argument('--speed',type=float,default=1)
p.add_argument('--asr-model', default='base.en')
p.add_argument('--asr-device', choices=['auto', 'cuda', 'cpu'], default='auto')
asyncio.run(main(p.parse_args()))
