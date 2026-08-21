"""aicode — a minimal AI coding agent CLI.

Drives any OpenAI-compatible chat-completions endpoint (Aliyun Bailian MaaS,
OpenAI, vLLM, ...) through a tool-use loop: the model reads and writes files
and runs shell commands inside a working directory until the task is done.
"""

__version__ = "0.1.0"
