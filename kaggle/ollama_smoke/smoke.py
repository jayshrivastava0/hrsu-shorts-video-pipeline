"""Kaggle smoke test: can Ollama run bigger open models fully on Kaggle's GPUs?

Installs Ollama, pulls each model, loads it at a large context, and reports GPU placement and
generation speed with thinking on. Results -> /kaggle/working/smoke.json
"""
import json
import subprocess
import time
import urllib.request

MODELS = ["qwen3:14b", "gemma3:12b"]
NUM_CTX = 32768


def sh(cmd, check=True):
    print("$", cmd, flush=True)
    return subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True)


print(sh("nvidia-smi --query-gpu=name,memory.total --format=csv").stdout)
sh("apt-get install -y -qq zstd >/dev/null 2>&1 || true", check=False)
sh("curl -fsSL https://ollama.com/install.sh | sh")
env = "OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_HOST=127.0.0.1:11434"
subprocess.Popen(f"{env} nohup ollama serve > /kaggle/working/ollama.log 2>&1 &", shell=True)
for _ in range(60):
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
        break
    except Exception:
        time.sleep(2)
print(sh("ollama --version").stdout)

PROMPT = ("Explain in 4 sentences, for a procurement manager, why calcium nitrate dosing stops "
          "hydrogen sulfide forming in sewers. Be mechanistically precise.")
results = {}
for m in MODELS:
    t = time.time()
    sh(f"ollama pull {m}")
    pull_s = time.time() - t
    body = {"model": m, "stream": False, **({"think": True} if m.startswith("qwen3") else {}),
            "messages": [{"role": "user", "content": PROMPT}],
            "options": {"num_ctx": NUM_CTX, "num_predict": 4000}}
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    r = json.loads(urllib.request.urlopen(req, timeout=1800).read())
    gen_s = time.time() - t
    ps = sh("ollama ps").stdout
    toks = r.get("eval_count", 0)
    results[m] = {"pull_s": round(pull_s), "gen_s": round(gen_s, 1), "eval_tokens": toks,
                  "tok_per_s": round(toks / max(1e-6, r.get("eval_duration", 1) / 1e9), 1),
                  "ollama_ps": ps, "answer": r["message"]["content"][:800],
                  "thinking_chars": len(r["message"].get("thinking") or "")}
    print(json.dumps(results[m], indent=1), flush=True)
    sh(f"ollama stop {m}", check=False)

json.dump(results, open("/kaggle/working/smoke.json", "w"), indent=1)
print("DONE")
