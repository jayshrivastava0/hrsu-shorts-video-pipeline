"""Kaggle smoke test for running the gemma_director rollouts on Kaggle (2x T4).

Checks, in order of risk:
  1. HyperFrames (hyperframes@0.8.147) installs, passes doctor, snapshots and renders headless.
  2. gemma4:31b (the production director's open weights) loads 100% on GPU at num_ctx 32768,
     whether it supports thinking, and its generation speed with images.
  3. gemma4:26b-a4b (fast same-family fallback) for comparison.
Results -> /kaggle/working/smoke.json
"""
import base64
import json
import os
import subprocess
import time
import urllib.request

HF = "hyperframes@0.8.147"
MODELS = ["gemma4:31b", "gemma4:26b-a4b"]
NUM_CTX = 32768
W = "/kaggle/working"
results = {}


def sh(cmd, check=False, timeout=3600, cwd=None):
    print("$", cmd, flush=True)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    out = (p.stdout + p.stderr)[-3000:]
    print(out, flush=True)
    if check and p.returncode:
        raise RuntimeError(f"{cmd} -> {p.returncode}")
    return p.returncode, out


def save():
    json.dump(results, open(f"{W}/smoke.json", "w"), indent=1)


results["gpus"] = sh("nvidia-smi --query-gpu=name,memory.total --format=csv")[1]
results["ffmpeg"] = sh("ffmpeg -version | head -1")[1]

# ---- 1. HyperFrames
sh("curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y -qq nodejs",
   timeout=900)
results["node"] = sh("node --version && npx --version")[1]
sh("apt-get install -y -qq libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 "
   "libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 "
   "libcairo2 fonts-liberation fonts-noto-color-emoji > /dev/null 2>&1", timeout=900)
results["hf_doctor"] = sh(f"npx --yes {HF} doctor", timeout=900)
rc, out = sh(f"npx --yes {HF} init smoke --non-interactive --example=blank --resolution=portrait",
             cwd=W, timeout=900)
results["hf_init"] = [rc, out]
proj = f"{W}/smoke"
with open(f"{proj}/index.html", encoding="utf-8") as f:
    html = f.read()
# a visible element so snapshots/renders are not blank
html = html.replace("</body>", '<div style="position:absolute;top:40%;width:100%;text-align:center;'
                    'font:bold 90px sans-serif;color:#d4af37">HRSU smoke</div></body>')
open(f"{proj}/index.html", "w", encoding="utf-8").write(html)
results["hf_snapshot"] = sh(f"npx --yes {HF} snapshot --at 0.5", cwd=proj, timeout=900)
t = time.time()
results["hf_render"] = sh(f"npx --yes {HF} render --quality high --output renders/video.mp4",
                          cwd=proj, timeout=1800)
results["hf_render_s"] = round(time.time() - t)
results["hf_render_probe"] = sh(f"ffprobe -v error -show_entries format=duration:stream=width,height "
                                f"-of json {proj}/renders/video.mp4")[1]
save()

# ---- 2. Ollama + gemma4
sh("apt-get install -y -qq zstd > /dev/null 2>&1")
sh("curl -fsSL https://ollama.com/install.sh | sh", timeout=900)
env = "OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=1"
subprocess.Popen(f"{env} nohup ollama serve > {W}/ollama.log 2>&1 &", shell=True)
for _ in range(60):
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
        break
    except Exception:
        time.sleep(2)
results["ollama"] = sh("ollama --version")[1]

img = None
for root, _, files in os.walk(proj):
    for fn in files:
        if fn.endswith(".png"):
            img = os.path.join(root, fn)
            break
PROMPT = ("You are the art director of a 60-second explainer for industrial procurement managers. "
          "In 6 numbered lines, storyboard the first 20 seconds of a video on why calcium nitrate "
          "dosing stops hydrogen sulfide forming in sewers: per line give time range, what is on "
          "screen, and the one idea it conveys." + (" The attached image is the brand title card; "
                                                     "say what you see on it first." if img else ""))
for m in MODELS:
    r = {}
    t = time.time()
    r["pull"] = sh(f"ollama pull {m}", timeout=3600)[0]
    r["pull_s"] = round(time.time() - t)
    r["show"] = sh(f"ollama show {m}")[1]
    think = "thinking" in r["show"]
    msg = {"role": "user", "content": PROMPT}
    if img:
        msg["images"] = [base64.b64encode(open(img, "rb").read()).decode()]
    body = {"model": m, "stream": False, "messages": [msg],
            **({"think": True} if think else {}),
            "options": {"num_ctx": NUM_CTX, "num_predict": 8000}}
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        out = json.loads(urllib.request.urlopen(req, timeout=3600).read())
        r.update({"gen_s": round(time.time() - t, 1), "eval_tokens": out.get("eval_count", 0),
                  "tok_per_s": round(out.get("eval_count", 0) / max(1e-6, out.get("eval_duration", 1) / 1e9), 1),
                  "prompt_tok_per_s": round(out.get("prompt_eval_count", 0) / max(1e-6, out.get("prompt_eval_duration", 1) / 1e9), 1),
                  "think_sent": think, "thinking_chars": len(out["message"].get("thinking") or ""),
                  "answer": out["message"]["content"][:1500]})
    except Exception as e:  # noqa: BLE001
        r["error"] = str(e)[:500]
    r["ollama_ps"] = sh("ollama ps")[1]
    r["nvidia_smi"] = sh("nvidia-smi --query-gpu=memory.used --format=csv")[1]
    results[m] = r
    save()
    sh(f"ollama stop {m}")
    sh(f"ollama rm {m}")  # free disk for the next model

save()
print("DONE")
