"""Prints p50/p95 of user-stopped -> first audio, and the average time in each stage.

Usage: python scripts/latency_stats.py [latency.jsonl]
"""

import json
import math
import sys


def percentile(values, p):
    # Nearest-rank: the smallest value that at least p% of the values are <= to
    ordered = sorted(values)
    rank = math.ceil(p / 100 * len(ordered))
    return ordered[max(rank, 1) - 1]


def average(values):
    return sum(values) / len(values)


path = sys.argv[1] if len(sys.argv) > 1 else "latency.jsonl"
with open(path) as f:
    turns = [json.loads(line) for line in f if line.strip()]

if not turns:
    sys.exit(f"No turns in {path}")

total = [t["tts_first_audio_ms"] - t["user_stopped_ms"] for t in turns]
stt = [t["stt_final_ms"] - t["user_stopped_ms"] for t in turns]
llm = [t["llm_first_token_ms"] - t["stt_final_ms"] for t in turns]
tts = [t["tts_first_audio_ms"] - t["llm_first_token_ms"] for t in turns]

print(f"Turns: {len(turns)} ({sum(t['interrupted'] for t in turns)} interrupted)")
print()
print("User stopped -> first audio")
print(f"  p50: {percentile(total, 50)} ms")
print(f"  p95: {percentile(total, 95)} ms")
print()
print("Average per stage")
print(f"  user stopped -> STT final:      {average(stt):.0f} ms")
print(f"  STT final -> LLM first token:   {average(llm):.0f} ms  (includes the end-of-turn wait)")
print(f"  LLM first token -> first audio: {average(tts):.0f} ms")
