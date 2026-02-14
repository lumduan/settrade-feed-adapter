import multiprocessing as mp
import subprocess
import sys

def run_symbol(symbol: str):
    cmd = [
        sys.executable,
        "-m",
        "scripts.benchmark_adapter",
        "--num-messages", "70000",
        "--warmup", "10000",
        "--num-runs", "5",
        "--symbol", symbol,
        "--gc-disabled",
    ]
    subprocess.run(cmd, check=True)

if __name__ == "__main__":
    symbols = ["AOT", "AOTH26"]

    processes = []
    for s in symbols:
        p = mp.Process(target=run_symbol, args=(s,))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()