#!/usr/bin/env python3
"""A real two-generation tool chain recorded in the existing Langfuse project.

Use the same project keys as the gateway. The application owns agent/tool
spans; the gateway owns generation spans and receives W3C traceparent.
No gateway can infer a client tool's execution timing from a tool proposal.
"""

import argparse
import json
import os
import uuid
from urllib.request import Request, urlopen

from langfuse import Langfuse, propagate_attributes


def chat(messages, trace_id, parent_id, *, tools=None, tool_choice=None):
    body = {"model": os.getenv("GATEWAY_MODEL", "qwen-strata"), "messages": messages, "max_tokens": 256,
            "temperature": 0, "metadata": {"session_id": SESSION, "trace_user_id": "monitoring-validation", "trace_name": "tool-chain-validation"}}
    if tools:
        body["tools"] = tools
    if tool_choice:
        body["tool_choice"] = tool_choice
    headers = {"Authorization": "Bearer " + os.environ["LITELLM_MASTER_KEY"], "Content-Type": "application/json",
               "traceparent": f"00-{trace_id}-{parent_id}-01", "x-session-id": SESSION}
    request = Request(os.getenv("GATEWAY_URL", "http://127.0.0.1:4000") + "/v1/chat/completions",
                      data=json.dumps(body).encode(), headers=headers)
    with urlopen(request, timeout=240) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help="Optional existing gateway dotenv file; credentials are never printed")
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file)
    client = Langfuse(public_key=os.environ["LANGFUSE_PUBLIC_KEY"], secret_key=os.environ["LANGFUSE_SECRET_KEY"],
                      base_url=os.getenv("LANGFUSE_HOST", "http://127.0.0.1:3001"))
    tools = [{"type": "function", "function": {"name": "add_numbers", "description": "Add two integer numbers.",
              "parameters": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                             "required": ["a", "b"], "additionalProperties": False}}}]
    messages = [{"role": "system", "content": "Use add_numbers to calculate. After the tool result, answer with the result only. Do not think."},
                {"role": "user", "content": "Calculate 17 + 25 using add_numbers."}]
    with propagate_attributes(session_id=SESSION, user_id="monitoring-validation", trace_name="tool-chain-validation",
                              tags=["monitoring-validation"], environment="wsl-local"), \
         client.start_as_current_observation(name="tool-chain-validation", as_type="agent", input=messages,
                                             metadata={"session_id": SESSION, "validation": True}) as agent:
        trace_id = client.get_current_trace_id()
        first = chat(messages, trace_id, client.get_current_observation_id(), tools=tools,
                     tool_choice={"type": "function", "function": {"name": "add_numbers"}})
        message = first["choices"][0]["message"]
        calls = message.get("tool_calls") or []
        if not calls:
            raise RuntimeError("The real model did not return a tool call; validation cannot be reported as passed")
        messages.append(message)
        for call in calls:
            if call["function"]["name"] != "add_numbers":
                raise RuntimeError("Unexpected tool returned by model")
            arguments = json.loads(call["function"]["arguments"])
            with client.start_as_current_observation(name="add_numbers", as_type="tool", input=arguments,
                    metadata={"tool_call_id": call["id"], "execution_source": "client"}) as tool:
                a, b = arguments["a"], arguments["b"]
                if type(a) is not int or type(b) is not int:
                    raise TypeError("add_numbers expects integers")
                result = a + b
                tool.update(output={"result": result})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"result": result})})
        final = chat(messages, trace_id, client.get_current_observation_id())
        answer = final["choices"][0]["message"].get("content")
        agent.update(output={"answer": answer})
    client.flush()
    print(json.dumps({"trace_id": trace_id, "session_id": SESSION, "tool_result": result, "answer": answer}, ensure_ascii=False))


SESSION = "monitoring-validation-" + uuid.uuid4().hex[:12]

if __name__ == "__main__":
    main()
