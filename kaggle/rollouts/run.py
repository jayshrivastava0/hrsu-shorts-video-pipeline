"""HRSU-VideoBench base rollouts on Kaggle (2x T4): gemma_director with the production model's
open weights, k rollouts per task, rendered with HyperFrames. Inputs come from the private
dataset jayshrivastava/hrsu-rollout-inputs (director scripts, videobench/rollout.py, frozen task
skeletons, the HyperFrames skills dir). Outputs -> /kaggle/working/videobench/<task>/<label>/.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

CONFIG = {
    "model": "gemma4:31b",
    "tasks": ["h2s-sewers", "mining-dust"],
    "k": 2,
    "num_ctx": 32768,
    "think_budget": 8000,
    "deadline_h": 10.5,   # Kaggle kills GPU sessions at 12 h; don't start a rollout after this
}
W = "/kaggle/working"
T0 = time.time()
HF = "hyperframes@0.8.147"


def sh(cmd, timeout=3600, cwd=None):
    print("$", cmd, flush=True)
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    print((p.stdout + p.stderr)[-2000:], flush=True)
    return p.returncode, p.stdout + p.stderr


# ---- inputs
src = glob.glob("/kaggle/input/**/hrsu_rollout_inputs.tgz", recursive=True)
INP = f"{W}/inputs"
if src:
    os.makedirs(INP, exist_ok=True)
    sh(f"tar xzf {src[0]} -C {INP}")
else:  # Kaggle auto-extracted the archive
    INP = os.path.dirname(glob.glob("/kaggle/input/**/repo/videobench/rollout.py", recursive=True)[0])
    INP = os.path.dirname(os.path.dirname(INP))
REPO = f"{W}/repo"
shutil.copytree(f"{INP}/repo", REPO, dirs_exist_ok=True)
OUT = f"{W}/videobench"
for t in CONFIG["tasks"]:
    shutil.copytree(f"{INP}/skeletons/{t}", f"{OUT}/{t}/_skeleton", dirs_exist_ok=True)

# ---- toolchain: node + headless-chrome libs + HyperFrames, Ollama
sh("curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y -qq nodejs", 900)
sh("apt-get install -y -qq zstd libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 "
   "libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 "
   "libpango-1.0-0 libcairo2 fonts-liberation fonts-noto-color-emoji > /dev/null 2>&1", 900)
sh(f"npx --yes {HF} --version", 900)
sh("pip install -q ollama", 600)
sh("curl -fsSL https://ollama.com/install.sh | sh", 900)
env = ("OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_HOST=127.0.0.1:11434 "
       "OLLAMA_NUM_PARALLEL=1 OLLAMA_KEEP_ALIVE=24h")
subprocess.Popen(f"{env} nohup ollama serve > {W}/ollama.log 2>&1 &", shell=True)
for _ in range(60):
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
        break
    except Exception:
        time.sleep(2)
m = CONFIG["model"]
sh(f"ollama pull {m}", 3600)
_, show = sh(f"ollama show {m}")
think = "1" if "thinking" in show else ""   # thinking on wherever the model supports it

# ---- rollouts
log = []
for rep in range(CONFIG["k"]):
    for t in CONFIG["tasks"]:
        if (time.time() - T0) / 3600 > CONFIG["deadline_h"]:
            log.append({"task": t, "rep": rep, "skipped": "deadline"})
            continue
        label = f"base-{m.replace(':', '-')}-r{rep}"
        cmd = (f"{sys.executable} -m videobench.rollout run {t} {label} --model {m} "
               f"--think '{think}' --think-budget {CONFIG['think_budget']} "
               f"--num-ctx {CONFIG['num_ctx']} --timeout 3600 --concurrency 1")
        t1 = time.time()
        p = subprocess.run(cmd, shell=True, cwd=REPO, timeout=6 * 3600,
                           env={**os.environ, "VIDEOBENCH_OUT": OUT, "PYTHONUTF8": "1",
                                "GEMMA_DIRECTOR_SKILLS": f"{INP}/skills"})
        log.append({"task": t, "rep": rep, "label": label, "rc": p.returncode,
                    "secs": round(time.time() - t1)})
        json.dump({"config": CONFIG, "think": think, "rollouts": log},
                  open(f"{W}/rollouts.json", "w"), indent=1)
        print(log[-1], flush=True)

# keep outputs small: drop the input copies
shutil.rmtree(f"{W}/inputs", ignore_errors=True)
shutil.rmtree(REPO, ignore_errors=True)
json.dump({"config": CONFIG, "think": think, "rollouts": log}, open(f"{W}/rollouts.json", "w"), indent=1)
print("DONE")
