"""Rewrite qwen-auto to the shortest context tier before LiteLLM routes it."""

from litellm.integrations.custom_logger import CustomLogger

from token_router import AUTO_MODEL, DEFAULT_OUTPUT_RESERVE, ContextTooLong, select_tier


def _message_text(message):
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts)
    return "" if content is None else str(content)


def count_input_tokens(messages):
    try:
        import litellm
        return int(litellm.token_counter(model="gpt-4o-mini", messages=messages))
    except Exception:
        text = "\n".join(_message_text(message) for message in messages or [])
        return max(1, (len(text) + 1) // 2)


def _output_tokens(data):
    raw = data.get("max_completion_tokens", data.get("max_tokens", DEFAULT_OUTPUT_RESERVE))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_OUTPUT_RESERVE
    return value if value >= 0 else DEFAULT_OUTPUT_RESERVE


class TokenRouter(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if data.get("model") != AUTO_MODEL:
            return data
        try:
            route = select_tier(count_input_tokens(data.get("messages") or []), _output_tokens(data))
        except ContextTooLong as exc:
            return str(exc)
        data["model"] = route.model
        metadata = data.setdefault("metadata", {})
        metadata["context_tier"] = route.model
        metadata["context_limit"] = route.limit
        metadata["context_input_tokens"] = route.input_tokens
        metadata["context_output_tokens"] = route.output_tokens
        return data


handler = TokenRouter()
