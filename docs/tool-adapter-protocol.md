# Replay 工具适配器编写指南

这份文档的目标不是解释 Replay 内部，而是让你能把自己的 agent 工具系统接进 Replay。

最重要的一句话：

> 在 agent 真正执行工具的那一行外面，套一层 `replay.invoke_tool()` 或 `replay.invoke_tool_sync()`。

不要在 LLM 返回 `tool_calls` 的地方接；那里只是模型输出。要在 agent 解析 `tool_calls` 后、真正调用本地工具/SDK/HTTP client 的地方接。

## 先找到工具执行点

在你的 agent 代码里搜索这些关键词：

```bash
rg "call_tool|tool_call|tools\\[|execute_tool|run_tool|function.name|tool_calls"
```

你要找的是类似下面的代码：

```python
result = await client.call_tool(name, arguments)
```

或：

```python
result = await tools[name](arguments)
```

或：

```python
result = await tool_client.call("google_search", q=query, num=10)
```

Replay adapter 就应该装在这些位置附近。

## 选择你的接入方式

按你的工具系统形态选一种即可。

| 你的代码长这样 | 用什么方式 |
| --- | --- |
| 单个地方直接调用工具函数 | 直接用 `invoke_tool` / `invoke_tool_sync` |
| `tools = {"search": search_fn}` 这种 registry | 用 `MappingToolAdapter` |
| `client.call_tool(name, arguments)` 这种方法 | 用 `MethodToolAdapter` |
| `client.call(name, **kwargs)` 或第三方 SDK 特殊签名 | 写一个继承 `BaseToolAdapter` 的自定义 adapter |
| LLM 返回 OpenAI `tool_calls` | 不在 LLM 层接；在执行 tool call 的工具 runner/client 处接 |

## 方式一：直接包装一个工具调用

如果你的 agent 里只有少量工具调用，先用这种方式。它最直观。

原代码：

```python
async def run_search(query: str) -> dict:
    return await search_api.search(query)
```

改成：

```python
import replay


async def run_search(query: str) -> dict:
    return await replay.invoke_tool(
        "search",
        {"query": query},
        lambda: search_api.search(query),
        namespace="my_agent",
        version="v1",
    )
```

同步工具：

```python
def run_calculator(expression: str) -> dict:
    return replay.invoke_tool_sync(
        "calculator",
        {"expression": expression},
        lambda: calculator.evaluate(expression),
        namespace="my_agent",
        version="v1",
    )
```

这里有三个必填项：

- `"search"`：稳定工具名。
- `{"query": query}`：可 JSON 序列化的工具输入。
- `lambda: search_api.search(query)`：真正执行工具的延迟调用。

不要写成这样：

```python
# 错误：工具会在进入 Replay 前立刻执行。
await replay.invoke_tool("search", {"query": query}, search_api.search(query))
```

## 方式二：适配 dict 工具 registry

如果你的工具系统是这样：

```python
TOOLS = {
    "search": search_tool,
    "calculator": calculator_tool,
}


async def search_tool(args: dict) -> dict:
    ...
```

接入方式：

```python
import replay


_REPLAY_TOOL_ADAPTER = None


def install_replay_tool_adapter() -> None:
    global _REPLAY_TOOL_ADAPTER
    if _REPLAY_TOOL_ADAPTER is not None:
        return

    adapter = replay.MappingToolAdapter(
        TOOLS,
        namespace="my_agent",
        version="v1",
    )
    adapter.install()
    _REPLAY_TOOL_ADAPTER = adapter
```

在 agent 启动时调用一次：

```python
import replay

replay.install()
install_replay_tool_adapter()
```

原来的工具调用不用改：

```python
result = await TOOLS["search"]({"query": "hello"})
```

适用条件：

- registry 是 mutable mapping，例如 dict。
- 每个工具函数接受一个 `dict` 参数。
- 每个工具函数返回 JSON-like 结果。

如果 registry 里的 `ToolSpec` 是不可变 dataclass，可以参考这种写法：先包装临时 registry，再把包装后的 handler 放回 spec。

```python
from dataclasses import replace


def install_replay_tool_adapter() -> None:
    registry = {name: spec.handler for name, spec in TOOL_SPECS.items()}
    adapter = replay.MappingToolAdapter(registry, namespace="agent2")
    adapter.install()

    for name, wrapped_handler in registry.items():
        TOOL_SPECS[name] = replace(TOOL_SPECS[name], handler=wrapped_handler)
```

## 方式三：适配 `client.call_tool(name, arguments)`

如果你的工具客户端是这样：

```python
result = await client.call_tool("lookup", {"id": 7})
```

直接用：

```python
adapter = replay.MethodToolAdapter(
    client,
    "call_tool",
    namespace="mcp",
    version="v1",
)
adapter.install()
```

原调用不用改：

```python
result = await client.call_tool("lookup", {"id": 7})
```

默认提取规则：

- 工具名来自 `kwargs["name"]`、`kwargs["tool_name"]` 或第 0 个位置参数。
- 参数来自 `kwargs["arguments"]`、`kwargs["args"]` 或第 1 个位置参数。
- 参数缺省时使用 `{}`。
- 参数必须是 `dict`。

如果方法签名是：

```python
await client.execute(session_id, tool_name, arguments)
```

可以指定位置：

```python
adapter = replay.MethodToolAdapter(
    client,
    "execute",
    name_arg=1,
    arguments_arg=2,
    namespace="my_agent",
)
adapter.install()
```

## 方式四：写自定义 adapter

如果你的工具调用是这种形态：

```python
await tool_client.call("google_search", q="test", num=10)
```

`MethodToolAdapter` 不够用，因为参数不是一个 `arguments` dict，而是散在 `**kwargs` 里。这时写自定义 adapter。

完整模板如下，通常只需要改 5 个地方：

```python
from __future__ import annotations

import functools
from typing import Any

import replay
from replay.semantic_runtime import RUNTIME


class MyToolAdapter(replay.BaseToolAdapter):
    def __init__(
        self,
        *,
        namespace: str | None = "my_agent",
        version: str | None = "v1",
    ) -> None:
        self.namespace = namespace
        self.version = version
        self.fs_capture = None
        self._target_cls: type[Any] | None = None
        self._original_call: Any = None

    def install(self) -> None:
        if self._original_call is not None:
            return

        # 1. 改成你的工具 client class。
        from my_agent.tools import ToolClient

        self._target_cls = ToolClient
        self._original_call = ToolClient.call
        adapter = self

        @functools.wraps(self._original_call)
        async def replay_call(client: Any, name: str, **kwargs: Any) -> Any:
            # 2. 改成你要记录的工具范围。
            # 如果所有工具都要记录，就删掉这个 if。
            if name not in {"google_search", "browser_open"}:
                return await adapter._original_call(
                    client,
                    name,
                    **RUNTIME.plain_snapshot(kwargs),
                )

            # 3. 构造稳定、可 JSON 序列化的 Replay arguments。
            arguments = dict(kwargs)

            # 4. 用 invoke_async 包住原工具调用。
            return await adapter.invoke_async(
                name,
                arguments,
                lambda: adapter._original_call(
                    client,
                    name,
                    **RUNTIME.plain_snapshot(kwargs),
                ),
            )

        replay_call._replay_tool_wrapper = True
        ToolClient.call = replay_call

    def uninstall(self) -> None:
        if self._target_cls is None or self._original_call is None:
            return
        # 5. 恢复原方法。
        self._target_cls.call = self._original_call
        self._target_cls = None
        self._original_call = None
```

安装：

```python
import replay

from integrations.my_agent.tool_adapter import MyToolAdapter

replay.install()
tool_adapter = MyToolAdapter()
tool_adapter.install()
```

同步版本：

```python
class MySyncToolAdapter(replay.BaseToolAdapter):
    def __init__(self, client) -> None:
        self.client = client
        self.namespace = "my_agent"
        self.version = "v1"
        self.fs_capture = None
        self._original_call = None

    def install(self) -> None:
        if self._original_call is not None:
            return

        self._original_call = self.client.call
        adapter = self

        @functools.wraps(self._original_call)
        def replay_call(name: str, **kwargs: Any) -> Any:
            arguments = dict(kwargs)
            return adapter.invoke_sync(
                name,
                arguments,
                lambda: adapter._original_call(
                    name,
                    **RUNTIME.plain_snapshot(kwargs),
                ),
            )

        replay_call._replay_tool_wrapper = True
        self.client.call = replay_call

    def uninstall(self) -> None:
        if self._original_call is None:
            return
        self.client.call = self._original_call
        self._original_call = None
```

## 自定义 adapter 的 5 个填空

写 adapter 时，不要先想 Replay 内部。按这 5 个问题填：

1. 原始工具调用入口在哪里？

   例如 `ToolClient.call`、`client.call_tool`、`TOOLS[name]`。

2. 工具名从哪里来？

   例如参数 `name`，或 `tool_call.function.name`。

3. Replay 要记录哪些参数？

   只放可复现、可 JSON 序列化的参数。不要放 session、client、文件句柄、HTTP response。

4. 原工具怎么调用？

   放进 `lambda`，不要提前执行。

5. record/replay 都需要哪些副作用？

   如果工具写文件，传 `fs_capture`；如果不写文件，保持 `None`。

## OpenAI tool_calls 应该接在哪里

假设 LLM 返回：

```json
{
  "tool_calls": [
    {
      "function": {
        "name": "search",
        "arguments": "{\"query\":\"hello\"}"
      }
    }
  ]
}
```

不要在“LLM response 里出现 tool_calls”这一步接 adapter。应该在 agent 执行工具时接：

```python
for tool_call in response.choices[0].message.tool_calls:
    name = tool_call.function.name
    arguments = json.loads(tool_call.function.arguments)

    # 这里才是真正的工具执行点。
    result = await replay.invoke_tool(
        name,
        arguments,
        lambda: tool_runner.run(name, arguments),
        namespace="my_agent",
        version="v1",
    )

    messages.append(
        {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "content": json.dumps(result, ensure_ascii=False),
        }
    )
```

只有这样，Replay 才能记录：

- LLM 输出了 tool call。
- agent 执行了哪个工具。
- 工具结果是什么。
- 后续 LLM 调用依赖这个工具结果。

## 文件工具怎么接

如果工具会修改本地文件，必须传 `fs_capture`。否则 replay 时只会恢复工具返回值，不会恢复文件变化。

推荐：

```python
with replay.managed_sandbox(
    base_root="agent/sandbox_base",
    work_root="agent/sandbox",
) as capture:
    adapter = replay.MethodToolAdapter(
        client,
        "call_tool",
        namespace="workspace",
        version="v1",
        fs_capture=capture,
    )
    adapter.install()

    with replay.record("run-A"):
        await main()
```

要求：

- 工具只写 `work_root` 下面的文本文件。
- replay 时也用同样的 sandbox/capture 配置。
- 不要写 sandbox 外的文件；Replay 不会记录。

## 一个最小测试

给 adapter 写一个计数器测试，确认 replay 时没有调用真实工具。

```python
counter = {"count": 0}


async def real_tool(args: dict) -> dict:
    counter["count"] += 1
    return {"echo": args["text"], "count": counter["count"]}


TOOLS = {"echo": real_tool}
adapter = replay.MappingToolAdapter(TOOLS, namespace="test")
adapter.install()

with replay.record("adapter-smoke"):
    assert await TOOLS["echo"]({"text": "hello"}) == {
        "echo": "hello",
        "count": 1,
    }

counter["count"] = 0
with replay.replay(base_run="adapter-smoke"):
    assert await TOOLS["echo"]({"text": "hello"}) == {
        "echo": "hello",
        "count": 1,
    }

assert counter["count"] == 0
```

这个测试过了，说明最基本的 record/replay 工具适配是对的。

## 输入输出必须长什么样

`arguments` 必须是 mapping，通常是 `dict[str, JSONValue]`。允许的值：

- `None`
- `str`
- `int`
- `bool`
- 有限 `float`
- `decimal.Decimal`
- `pathlib.Path`
- `list` / `tuple`
- `dict`
- dataclass
- 有 `model_dump(mode="json")` 的对象，例如 Pydantic model

输出也必须是 JSON-like。不要返回任意 Python 对象。如果第三方 SDK 返回复杂对象，在工具边界转成 dict：

```python
raw = sdk_result
return {
    "id": raw.id,
    "title": raw.title,
    "items": [item.to_dict() for item in raw.items],
}
```

不要记录这些东西：

- 文件句柄
- HTTP response 对象
- 数据库连接
- SDK client/session
- coroutine/task/future
- 函数对象
- 不稳定随机对象

## namespace 和 version 怎么取

推荐：

```python
namespace = "my_agent"
version = "v1"
```

如果同一个 agent 有多套工具系统：

```python
namespace = "mcp"
namespace = "local"
namespace = "browser"
namespace = "workspace"
```

当工具输入/输出语义变了，升版本：

```python
version = "v2"
```

工具名不要带随机数、时间戳、临时路径。工具名要表达“是哪一个工具”，不是“这次调用的实例 id”。

## record/replay/fork 行为

record 模式：

- Replay 调用真实工具。
- 记录工具输入、输出或异常。
- 记录可选文件系统副作用。
- 给工具返回值种 provenance。

replay 模式：

- 如果工具输入和路径匹配历史记录，Replay 不调用真实工具。
- 成功记录返回 `output.value`。
- 异常记录抛 `ReplayedToolError`。
- 有文件系统副作用时，先应用记录中的文件变化。

fork 模式：

- 如果上游 LLM 被 override，依赖新 LLM 输出的工具会 live 执行，并写入 fork trace。
- 如果 sandbox 被 fork/live 工具改脏，后续使用同一 capture root 的工具会 live 执行，避免错误应用旧文件补丁。

## 常见错误

### 1. 只 patch LLM，没有 patch 工具执行

Replay 会记录 LLM response，但不会自动执行或记录本地工具。必须在工具 runner/client 处接 adapter。

### 2. 工具参数不稳定

下面这种会导致 replay miss：

```python
arguments = {
    "query": query,
    "timestamp": time.time(),
}
```

如果 timestamp 不影响工具语义，不要记录它。如果影响语义，要确保 replay 时能传同一个值。

### 3. replay 时真实工具还在跑

通常原因：

- adapter 没有走 `invoke_tool` / `invoke_tool_sync`。
- 工具名变了。
- `arguments` 变了。
- 并发路径变了。
- fork 已经进入 live 分支。

### 4. adapter 破坏原对象

adapter 不应该替换 messages list、文件句柄、stream、cursor 等业务对象。只在工具调用边界构造 JSON snapshot。

### 5. 文件工具没传 `fs_capture`

如果工具会写文件，record/replay 都要传相同 capture 配置。

## 完整检查清单

新 adapter 至少确认这些点：

- record 后 JSONL 里有 `kind="tool"`。
- `input.tool_name` 是预期工具名。
- `input.arguments` 是稳定 JSON。
- `input.namespace` 和 `input.version` 符合预期。
- replay 同一调用时真实工具计数器不增长。
- 工具异常能 replay 为 `ReplayedToolError`。
- 文件工具 replay 时真实工具不执行，但文件变化被恢复。
- agent 会把工具结果追加回 messages。
- 后续 LLM record 的 input.messages 包含工具结果。
- trace 里有 tool -> LLM 因果边。

## 参考代码

- `replay/tools.py`：核心 `invoke_tool` / `invoke_tool_sync` 协议。
- `replay/tool_adapters.py`：内置 `MappingToolAdapter` 和 `MethodToolAdapter`。
- `replay/tests/tool_test.py`：工具 record/replay/fork/文件副作用测试。
- `test_agent/agent4/tools.py`: registry and method-shaped tool adapter examples.
- `integrations/my_agent/tool_adapter.py`：自定义 agent adapter 模板。
