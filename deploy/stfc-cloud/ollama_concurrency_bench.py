"""Time the shared VM's Ollama with one chat, then several at once.

Run inside the agent container, which reaches Ollama on the compose network
and has the model name in its environment (README.md "Concurrency"):

    docker compose exec -T agent uv run --no-sync python - < ollama_concurrency_bench.py

Each request carries the agent's real system prompt and an attached CIF, about
4k tokens in all (production prompts: median 4.1k tokens), answers with
thinking off and qwen3.8's non-thinking sampling, as the agent sends it, and
is capped at 300 output tokens. Prints, per concurrency level, how long the
last chat took and the token rates.
"""

import asyncio
import os
import time

import httpx

from goldilocks_agent.graph import system_message

BASE = os.environ.get("OLLAMA_API_BASE", "http://ollama:11434")
MODEL = os.environ.get("GOLDILOCKS_AGENT_MODEL", "ollama_chat/qwen3.8:27b").split(
    "/", 1
)[1]
CIF_ROW = "  Fe  Fe{i}  1.0  {x:.4f}  {y:.4f}  {z:.4f}  1.0000\n"


def attached_cif(variant: int) -> str:
    rows = "".join(
        CIF_ROW.format(i=i, x=(i * 0.137) % 1, y=(i * 0.291) % 1, z=(i * 0.413) % 1)
        for i in range(85)
    )
    return f"[Attached file: Fe_{variant}.cif]\n```cif\ndata_Fe\n{rows}```"


def request(variant: int) -> dict:
    return {
        "model": MODEL,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.7, "top_p": 0.8, "num_predict": 300},
        "messages": [
            system_message(None),
            {
                "role": "user",
                "content": f"{attached_cif(variant)}\n\nIn about 200 words, what "
                "would you check before running DFT on this structure?",
            },
        ],
    }


async def chat(client: httpx.AsyncClient, variant: int) -> dict:
    start = time.monotonic()
    response = await client.post(f"{BASE}/api/chat", json=request(variant))
    response.raise_for_status()
    data = response.json()
    return {
        "wall": time.monotonic() - start,
        "tokens": data["eval_count"],
        "decode_tok_s": data["eval_count"] / (data["eval_duration"] / 1e9),
        "prompt_tokens": data.get("prompt_eval_count", 0),
    }


async def main() -> None:
    async with httpx.AsyncClient(timeout=900) as client:
        await chat(client, 0)  # load the model, so times exclude it
        variant = 1
        # 8 at once on 4 slots shows what a burst beyond the slots costs.
        for users in (1, 2, 4, 8):
            start = time.monotonic()
            results = await asyncio.gather(
                *(chat(client, variant + i) for i in range(users))
            )
            variant += users
            wall = time.monotonic() - start
            tokens = sum(r["tokens"] for r in results)
            print(
                f"{users} at once: last finished {wall:5.1f} s | "
                f"aggregate {tokens / wall:5.1f} tok/s | per chat while decoding "
                + ", ".join(f"{r['decode_tok_s']:.1f}" for r in results)
                + f" tok/s | prompt ~{results[0]['prompt_tokens']} tokens"
            )


asyncio.run(main())
