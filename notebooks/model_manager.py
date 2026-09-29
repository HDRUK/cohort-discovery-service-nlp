"""Keep one model resident at a time so benchmarking does not exhaust memory.

Usable from a notebook (`from model_manager import use, status`) or the shell
(`python notebooks/model_manager.py status`).
"""

import json
import subprocess
import time
import sys
import urllib.request

OLLAMA_URL = "http://localhost:11434"
SWAP_WARN_FRACTION = 0.80

# A loaded model costs more than its file: context and KV cache add a roughly fixed
# amount on top. Measured on this setup: 0.52 GB on disk -> 2.75 GB resident,
# 2.1 -> 3.7, 5.2 -> 5.5. Proportional scaling badly under-predicts the small end,
# so estimate with a constant term and round up rather than down.
LOAD_OVERHEAD_GB = 2.0
LOAD_SCALE = 1.05


def _api(path):
    with urllib.request.urlopen(f"{OLLAMA_URL}{path}", timeout=10) as response:
        return json.load(response)


def _sysctl(name):
    return subprocess.run(
        ["sysctl", "-n", name], capture_output=True, text=True
    ).stdout.strip()


def memory():
    total_ram = int(_sysctl("hw.memsize")) / 1e9

    page_size, free_pages, inactive_pages = 16384, 0, 0
    for line in subprocess.run(
        ["vm_stat"], capture_output=True, text=True
    ).stdout.splitlines():
        if "page size of" in line:
            page_size = int(line.split("page size of")[1].split("bytes")[0].strip())
        elif line.startswith("Pages free:"):
            free_pages = int(line.split(":")[1].strip().rstrip("."))
        elif line.startswith("Pages inactive:"):
            inactive_pages = int(line.split(":")[1].strip().rstrip("."))

    swap_total = swap_used = 0.0
    for field in _sysctl("vm.swapusage").split():
        if field.endswith("M") and field[0].isdigit():
            value = float(field.rstrip("M")) / 1000
            if swap_total == 0.0:
                swap_total = value
            elif swap_used == 0.0:
                swap_used = value

    return {
        "ram_total_gb": round(total_ram, 1),
        "ram_available_gb": round((free_pages + inactive_pages) * page_size / 1e9, 1),
        "swap_used_gb": round(swap_used, 1),
        "swap_total_gb": round(swap_total, 1),
        "swap_pressure": round(swap_used / swap_total, 2) if swap_total else 0.0,
    }


def available():
    return {
        entry["name"]: round(entry.get("size", 0) / 1e9, 2)
        for entry in _api("/api/tags").get("models") or []
    }


def loaded():
    return {
        entry["name"]: round(entry.get("size", 0) / 1e9, 2)
        for entry in _api("/api/ps").get("models") or []
    }


def unload(name):
    subprocess.run(["ollama", "stop", name], capture_output=True)


def unload_all(keep=None):
    stopped = []
    for name in loaded():
        if name != keep:
            unload(name)
            stopped.append(name)
    return stopped


def will_fit(name):
    """(fits, explanation). Predicts the loaded footprint from the on-disk size."""
    sizes = available()
    if name not in sizes:
        return False, f"{name} is not pulled. Run: ollama pull {name}"

    needed = sizes[name] * LOAD_SCALE + LOAD_OVERHEAD_GB
    resident = loaded()
    if name in resident:
        return True, f"{name} is already resident ({resident[name]} GB)"

    free_now = memory()["ram_available_gb"]
    reclaimable = sum(size for other, size in resident.items() if other != name)
    headroom = free_now + reclaimable

    if needed > headroom:
        return False, (
            f"{name} needs about {needed:.1f} GB; only {headroom:.1f} GB is reachable "
            f"({free_now:.1f} GB free + {reclaimable:.1f} GB from evicting other models). "
            "Close something, or pick a smaller model."
        )
    return True, f"{name} needs about {needed:.1f} GB, {headroom:.1f} GB reachable"


def switch(name, force=False):
    """Alias for use(), reading better in a benchmark loop."""
    return use(name, force=force)


class model:
    """Context manager: evict everything else, run the block, then free.

        with model("phi4"):
            ...
    """

    def __init__(self, name, force=False, release=True):
        self.name = name
        self.force = force
        self.release = release

    def __enter__(self):
        return use(self.name, force=self.force)

    def __exit__(self, *exc):
        if self.release:
            unload(self.name)
        return False


def use(name, force=False):
    """Make `name` the only resident model, preloaded. Returns a status dict."""
    fits, reason = will_fit(name)
    if not fits and not force:
        raise MemoryError(reason)

    stopped = unload_all(keep=name)

    state = memory()
    if state["swap_pressure"] > SWAP_WARN_FRACTION:
        print(
            f"  warning: swap is {state['swap_pressure']:.0%} full "
            f"({state['swap_used_gb']}/{state['swap_total_gb']} GB). "
            "Timings taken now are not comparable with timings taken when it is not."
        )

    started = time.monotonic()
    request = json.dumps({"model": name, "keep_alive": "30m"}).encode()
    urllib.request.urlopen(
        urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=request,
            headers={"Content-Type": "application/json"},
        ),
        timeout=600,
    ).read()

    return {
        "model": name,
        "evicted": stopped,
        "load_secs": round(time.monotonic() - started, 1),
        "resident_gb": loaded().get(name),
        "note": reason,
        **memory(),
    }


def status():
    state = memory()
    resident = loaded()
    print(
        f"RAM {state['ram_available_gb']}/{state['ram_total_gb']} GB available   "
        f"swap {state['swap_used_gb']}/{state['swap_total_gb']} GB "
        f"({state['swap_pressure']:.0%})"
    )
    if state["swap_pressure"] > SWAP_WARN_FRACTION:
        print("  swap is under pressure — expect slow generation and noisy timings")
    print(f"\nresident ({len(resident)}):")
    for name, size in resident.items() or {}:
        print(f"  {name:20s} {size:5.2f} GB")
    if not resident:
        print("  (none)")
    print("\non disk:")
    for name, size in sorted(available().items(), key=lambda kv: kv[1]):
        fits, _ = will_fit(name)
        mark = "ok  " if fits else "TIGHT"
        print(f"  {mark} {name:20s} {size:5.2f} GB")
    return state


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "status":
        status()
    elif command in ("use", "switch"):
        print(json.dumps(use(sys.argv[2]), indent=2))
    elif command == "unload":
        print("evicted:", unload_all() or "(nothing was resident)")
    else:
        print(__doc__)
        print("commands: status | use <model> | unload")
