"""Tool definitions + validator + dispatcher.

The LLM never gets a shell. It may only propose one of the tools below as
structured JSON. validate_action() treats that proposal as untrusted input:
unknown tool, missing/extra args, wrong types, programs outside the allowlist
and shell metacharacters are all rejected before anything reaches xv6.
"""

import re

from xv6_bridge import BridgeError

# Programs that exist in xv6's /  (keep in sync with UPROGS in the Makefile)
ALLOWED_PROGRAMS = {
    "ls", "cat", "echo", "grep", "wc", "mkdir", "rm", "ln",
    "hello", "forktest", "stressfs", "usertests", "kill",
}
DESTRUCTIVE = {"rm", "kill"}
# Pipes and simple redirection are fine; command chaining/backgrounding is not.
FORBIDDEN = re.compile(r"[;&`$\\\n\r]")
PATH_RE = re.compile(r"^/?[A-Za-z0-9_.\-/]{1,60}$")

TOOLS = {
    "boot_xv6":     {"args": {}, "desc": "Build (if needed) and boot xv6 in QEMU."},
    "shutdown_xv6": {"args": {}, "desc": "Stop the QEMU guest."},
    "status":       {"args": {}, "desc": "Is xv6 running?"},
    "list_files":   {"args": {"path": str}, "desc": "ls a directory in xv6 ('/' for root)."},
    "read_file":    {"args": {"path": str}, "desc": "cat a file in xv6."},
    "run_command":  {"args": {"command": str},
                     "desc": "Run one xv6 shell command line. Allowed programs: "
                             + ", ".join(sorted(ALLOWED_PROGRAMS))
                             + ". Pipes '|' and redirection '<' '>' are allowed; "
                               "';', '&', '`', '$' are not."},
}


def openai_tool_schema():
    """Same tools in the OpenAI function-calling format."""
    out = []
    for name, spec in TOOLS.items():
        props = {a: {"type": "string"} for a in spec["args"]}
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec["desc"],
                "parameters": {"type": "object", "properties": props,
                               "required": list(spec["args"])},
            },
        })
    return out


class ValidationError(Exception):
    pass


def _check_command(cmd):
    if not cmd.strip():
        raise ValidationError("empty command")
    if len(cmd) > 200:
        raise ValidationError("command too long")
    if FORBIDDEN.search(cmd):
        raise ValidationError("forbidden character in command (; & ` $ \\ newline)")
    for stage in cmd.split("|"):
        words = re.sub(r"[<>]", " ", stage).split()
        if not words:
            raise ValidationError("empty pipeline stage")
        prog = words[0].lstrip("/")
        if prog not in ALLOWED_PROGRAMS:
            raise ValidationError(f"program '{prog}' is not in the allowlist")


def validate_action(action):
    """Raise ValidationError unless action is a well-formed allowed tool call."""
    if not isinstance(action, dict):
        raise ValidationError("action must be an object")
    name = action.get("tool")
    args = action.get("args", {})
    if name not in TOOLS:
        raise ValidationError(f"unknown tool '{name}'")
    if not isinstance(args, dict):
        raise ValidationError("args must be an object")
    spec = TOOLS[name]["args"]
    missing = set(spec) - set(args)
    extra = set(args) - set(spec)
    if missing:
        raise ValidationError(f"missing args: {sorted(missing)}")
    if extra:
        raise ValidationError(f"unexpected args: {sorted(extra)}")
    for k, t in spec.items():
        if not isinstance(args[k], t):
            raise ValidationError(f"arg '{k}' must be {t.__name__}")
    if "path" in args and not PATH_RE.match(args["path"]):
        raise ValidationError("bad path")
    if name == "run_command":
        _check_command(args["command"])


class ToolExecutor:
    def __init__(self, bridge):
        self.bridge = bridge

    def execute(self, action):
        """Validate, then run. Always returns an observation dict; never raises."""
        try:
            validate_action(action)
        except ValidationError as e:
            return {"ok": False, "rejected": True, "output": f"REJECTED by validator: {e}"}
        name, args = action["tool"], action.get("args", {})
        try:
            if name == "boot_xv6":
                out = self.bridge.boot()
            elif name == "shutdown_xv6":
                out = self.bridge.shutdown()
            elif name == "status":
                out = "running" if self.bridge.is_running() else "stopped"
            elif name == "list_files":
                out = self.bridge.run(f"ls {args['path']}")
            elif name == "read_file":
                out = self.bridge.run(f"cat {args['path']}")
            elif name == "run_command":
                out = self.bridge.run(args["command"])
            return {"ok": True, "output": out}
        except BridgeError as e:
            return {"ok": False, "output": f"bridge error: {e}"}
