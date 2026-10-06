"""Exercise the real capture/file pipeline, without microphone or notes API.

Run with the main venv. A private temporary directory and its outputs are deleted
on exit; only the JSON report on stdout persists if the caller redirects it.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from lecture_cli.asr import capture_environment, capture_python, resolve_asr_model
from lecture_cli.storage import events, read_json, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("--asr-model", required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args()
    model = resolve_asr_model(args.asr_model)
    env = capture_environment(model)
    env.pop("DEEPSEEK_API_KEY", None)
    env.update(HF_HUB_OFFLINE="1", OMP_NUM_THREADS="4")
    with tempfile.TemporaryDirectory(prefix="lecture-asr-bench-", dir="/tmp") as tmp:
        root = Path(tmp)
        audio = root / "input.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-stream_loop", str(args.repeat - 1),
                        "-i", str(Path(args.audio).resolve()), "-ar", "16000", "-ac", "1", str(audio)], check=True)
        duration = float(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio)]))
        write_json(root / "session.json", dict(asr_model=model, asr_device="cuda", language="en",
                                               context="", audio_file=str(audio), fast=args.fast))
        began = time.monotonic()
        loaded = first_text = feed_done = None
        peak_lag = peak_vram = 0
        samples = []
        last_report = 0
        with (root / "capture.log").open("w") as log:
            proc = subprocess.Popen([capture_python(model), "-c",
                "import sys; from pathlib import Path; from lecture_cli.capture import run; sys.exit(run(Path(sys.argv[1])))",
                str(root)], env=env, stdout=log, stderr=log)
            try:
                while proc.poll() is None:
                    now = time.monotonic()
                    if now - began > 180 + duration * 2:
                        raise TimeoutError("ASR benchmark exceeded its deadline")
                    state = read_json(root / "asr-state.json")
                    if state.get("asr_device") and loaded is None:
                        loaded = now
                    if state.get("count") and first_text is None:
                        first_text = now
                    if state.get("status") == "转录收尾中" and feed_done is None:
                        feed_done = now
                    lag = state.get("lag", 0)
                    peak_lag = max(peak_lag, lag)
                    if now - last_report >= 5:
                        gpu = subprocess.check_output(["nvidia-smi", "--query-gpu=memory.used",
                            "--format=csv,noheader,nounits"], text=True)
                        peak_vram = max(peak_vram, int(gpu.strip().splitlines()[0]))
                        samples.append(dict(seconds=round(now - (loaded or began), 1),
                                            audio_s=state.get("seconds", 0), lag_s=lag))
                        last_report = now
                    time.sleep(0.2)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
        ended = time.monotonic()
        state = read_json(root / "asr-state.json")
        result = dict(model=model, returncode=proc.returncode, audio_s=duration, fast=args.fast,
                      load_s=round(loaded - began, 2) if loaded else None,
                      run_s=round(ended - loaded, 2) if loaded else None,
                      first_text_s=round(first_text - loaded, 2) if first_text and loaded else None,
                      drain_s=round(ended - feed_done, 2) if feed_done else None,
                      peak_lag_s=peak_lag, peak_gpu_mib=peak_vram, samples=samples,
                      state=state, text=" ".join(r["text"] for r in events(root)))
        if proc.returncode:
            result["log_tail"] = (root / "capture.log").read_text()[-6000:]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return proc.returncode or (0 if result["text"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
