# xv6 agent (stage 0)

Host-side Python agent that boots xv6-riscv in QEMU and runs commands on it,
with a small chat dashboard. Kernel policy (the irreversibility budget) comes later.

```
LLM (kiro-gateway :8000)  ->  agent.py  ->  tools.py (validator)  ->  xv6_bridge.py  ->  QEMU stdin/stdout  ->  xv6 sh
```

| File | Role |
|---|---|
| `xv6_bridge.py` | Builds xv6, starts QEMU `-nographic` with the console on pipes, runs a command as `cmd ; echo @@ENDn@@` and returns output up to the marker. Each boot uses a fresh copy of `fs.img` (`run.img`). |
| `tools.py` | Tool list, JSON-schema for function calling, and `validate_action()`: unknown tools, bad args, programs outside the allowlist and `; & \` $` are rejected before reaching xv6. |
| `agent.py` | `RulePlanner` (no LLM, deterministic) and LLM planner via kiro-gateway (OpenAI-compatible). Records a trace per step. |
| `server.py` + `static/index.html` | Dashboard on http://127.0.0.1:8765: chat + trace on the left, raw xv6 console on the right. |

## Run

```
./run.sh            # builds xv6 if needed, starts the dashboard
```

Gateway key is read from `KIRO_API_KEY`, else from `/workspace/proxy/kiro-gateway/.env`.
Model defaults to `claude-sonnet-4.5` (`AGENT_MODEL` to change). `CPUS=3 ./run.sh` for 3 harts.

Rules mode (no LLM): `boot`, `stop`, `status`, `ls /`, `cat README`, `run <cmd>`.

The dashboard has no authentication and binds to 127.0.0.1 only.

## Known gap (motivates the kernel part)

The validator allows `rm` because it is a legitimate tool. An injected instruction
like "delete README" therefore passes; nothing below the agent limits it yet.
The irreversibility budget in the kernel is what will cap that.
