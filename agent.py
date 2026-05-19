from __future__ import annotations

import ast
import operator as op
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List


# Safe arithmetic evaluator: no eval(), no imports, no code execution.
_ALLOWED_OPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.Pow: op.pow,
    ast.USub: op.neg,
    ast.Mod: op.mod,
}


def safe_calculate(expr: str) -> str:
    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
            return _ALLOWED_OPS[type(node.op)](_eval(node.operand))
        raise ValueError("Unsupported expression")

    tree = ast.parse(expr, mode="eval")
    return str(_eval(tree))


@dataclass
class Tool:
    name: str
    description: str
    run: Callable[[str], str]


@dataclass
class SimpleAIAgent:
    memory_path: Path = Path("memory.txt")
    tools: Dict[str, Tool] = field(default_factory=dict)

    def __post_init__(self):
        self.tools = {
            "calculate": Tool("calculate", "Solve basic arithmetic", safe_calculate),
            "remember": Tool("remember", "Save a note to memory", self.remember),
            "recall": Tool("recall", "Read saved memory", self.recall),
            "time": Tool("time", "Return current timestamp", lambda _: datetime.now().isoformat(timespec="seconds")),
        }

    def remember(self, text: str) -> str:
        self.memory_path.write_text(self.recall("") + f"\n- {text}".strip() + "\n", encoding="utf-8")
        return "Saved to memory."

    def recall(self, _: str) -> str:
        if not self.memory_path.exists():
            return "No memory yet."
        return self.memory_path.read_text(encoding="utf-8").strip() or "No memory yet."

    def plan(self, task: str) -> List[str]:
        task_lower = task.lower()
        if any(word in task_lower for word in ["calculate", "посчитай", "сколько", "+", "-", "*", "/"]):
            return ["calculate"]
        if any(word in task_lower for word in ["remember", "запомни", "save"]):
            return ["remember"]
        if any(word in task_lower for word in ["recall", "вспомни", "memory", "память"]):
            return ["recall"]
        if any(word in task_lower for word in ["time", "время", "дата"]):
            return ["time"]
        return ["recall", "time"]

    def extract_argument(self, tool_name: str, task: str) -> str:
        if tool_name == "calculate":
            allowed = "0123456789+-*/(). %"
            expr = "".join(ch for ch in task if ch in allowed).strip()
            return expr or task
        if tool_name == "remember":
            for prefix in ["remember", "запомни", "save"]:
                task = task.replace(prefix, "", 1).strip()
            return task
        return task

    def run(self, task: str) -> str:
        steps = self.plan(task)
        log = [f"Task: {task}", f"Plan: {' -> '.join(steps)}"]
        for step in steps:
            tool = self.tools[step]
            arg = self.extract_argument(step, task)
            try:
                result = tool.run(arg)
            except Exception as exc:
                result = f"Tool error: {exc}"
            log.append(f"Action: {step}({arg!r})")
            log.append(f"Result: {result}")
        return "\n".join(log)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Minimal local AI-style agent")
    parser.add_argument("task", nargs="*", default=["посчитай 2 + 2 * 10"])
    args = parser.parse_args()

    agent = SimpleAIAgent()
    print(agent.run(" ".join(args.task)))
