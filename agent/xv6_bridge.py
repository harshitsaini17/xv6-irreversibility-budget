"""Host-to-guest bridge: starts xv6 in QEMU and runs shell commands on it.

How it works
------------
* QEMU is started with -nographic, so the xv6 console (UART) is connected
  to QEMU's stdin/stdout. We hold those as pipes.
* A reader thread copies every byte the guest prints into a buffer
  (self.console), which the dashboard also shows as the raw terminal.
* To run a command we type   <cmd> ; echo @@END<n>@@   followed by Enter.
  xv6's sh runs the command, then prints the marker on its own line,
  so we know exactly where the output ends (no guessing on timing).
  The console echoes typed characters, so the first line we get back
  is our own input; it is stripped from the observation.
"""

import os
import re
import shutil
import subprocess
import threading
import time

# agent/ lives inside the xv6-riscv tree, so the tree is one level up.
XV6_DIR = os.environ.get(
    "XV6_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
QEMU = os.environ.get("QEMU", "qemu-system-riscv64")
PROMPT = "$ "


class BridgeError(Exception):
    pass


class XV6Bridge:
    def __init__(self, xv6_dir=XV6_DIR, cpus=1):
        self.xv6_dir = os.path.abspath(xv6_dir)
        self.cpus = cpus
        self.proc = None
        self.console = ""            # everything the guest has printed
        self.lock = threading.Lock()  # protects self.console
        self.cmd_lock = threading.Lock()  # one command at a time
        self.counter = 0
        self.booted_at = None

    # ---------------- lifecycle ----------------
    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def build(self):
        """Run make so kernel/kernel and fs.img are up to date."""
        r = subprocess.run(
            ["make", "kernel/kernel", "fs.img"],
            cwd=self.xv6_dir, capture_output=True, text=True, timeout=600,
        )
        if r.returncode != 0:
            raise BridgeError("build failed:\n" + r.stdout[-2000:] + r.stderr[-2000:])
        return "build ok"

    def boot(self, timeout=60, fresh=True):
        """fresh=True: run on a copy of fs.img, so anything the agent deletes
        or writes is gone on the next boot and the built image stays clean.
        fresh=False: reuse run.img from the last boot (state persists)."""
        if self.is_running():
            return "xv6 is already running"
        self.build()
        run_img = os.path.join(self.xv6_dir, "run.img")
        if fresh or not os.path.exists(run_img):
            shutil.copyfile(os.path.join(self.xv6_dir, "fs.img"), run_img)
        cmd = [
            QEMU, "-machine", "virt", "-bios", "none",
            "-kernel", "kernel/kernel", "-m", "128M", "-smp", str(self.cpus),
            "-nographic",
            "-global", "virtio-mmio.force-legacy=false",
            "-drive", "file=run.img,if=none,format=raw,id=x0",
            "-device", "virtio-blk-device,drive=x0,bus=virtio-mmio-bus.0",
        ]
        with self.lock:
            self.console = ""
        self.proc = subprocess.Popen(
            cmd, cwd=self.xv6_dir,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0,
        )
        threading.Thread(target=self._reader, daemon=True).start()
        # Boot is done when the shell prints its first prompt.
        self._wait_for(lambda c: c.endswith(PROMPT), timeout, "boot")
        self.booted_at = time.time()
        return "xv6 booted:\n" + self.snapshot()

    def shutdown(self):
        if not self.is_running():
            return "xv6 is not running"
        # Ctrl-A then x is QEMU's quit key in -nographic mode.
        try:
            self.proc.stdin.write(b"\x01x")
            self.proc.stdin.flush()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        self.proc = None
        self.booted_at = None
        return "xv6 stopped"

    # ---------------- console I/O ----------------
    def _reader(self):
        proc = self.proc
        while True:
            b = proc.stdout.read(1)
            if not b:
                break
            with self.lock:
                self.console += b.decode("utf-8", "replace")
                if len(self.console) > 200_000:      # keep memory bounded
                    self.console = self.console[-100_000:]

    def snapshot(self, tail=4000):
        with self.lock:
            return self.console[-tail:]

    def _wait_for(self, cond, timeout, what):
        end = time.time() + timeout
        while time.time() < end:
            if not self.is_running():
                raise BridgeError(f"QEMU exited during {what}")
            with self.lock:
                if cond(self.console):
                    return
            time.sleep(0.05)
        raise BridgeError(f"timeout during {what}")

    def run(self, command, timeout=30):
        """Type one command into the xv6 shell and return its output."""
        if not self.is_running():
            raise BridgeError("xv6 is not running; boot it first")
        with self.cmd_lock:
            self.counter += 1
            marker = f"@@END{self.counter}@@"
            with self.lock:
                start = len(self.console)
            line = f"{command} ; echo {marker}\n"
            self.proc.stdin.write(line.encode())
            self.proc.stdin.flush()
            # The echoed input contains "echo @@ENDn@@"; the real output of
            # echo is the marker WITHOUT "echo " in front. Output that does
            # not end in a newline (e.g. cat of such a file) puts the marker
            # mid-line, so match on "not preceded by 'echo '" instead of "\n".
            pat = re.compile(r"(?<!echo )" + re.escape(marker))
            self._wait_for(lambda c: pat.search(c, start) is not None,
                           timeout, f"'{command}'")
            time.sleep(0.1)          # let the shell print its next prompt
            with self.lock:
                out = self.console[start:]
            out = out[: pat.search(out).start()].replace("\r", "")
            lines = out.split("\n")
            if lines and marker in lines[0]:   # drop our own echoed input
                lines = lines[1:]
            return "\n".join(lines).strip("\n")
