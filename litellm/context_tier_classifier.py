"""Official Auto Router classifier: map token count onto context tiers.

LiteLLM loads this as complexity_router_config.classifier_plugin. The router
then applies its own context-window escalation if the chosen tier cannot hold
the prompt.
"""

from token_router import DEFAULT_OUTPUT_RESERVE, ContextTooLong, select_tier

TIER_BY_MODEL = {
    "qwen-32k": "CONTEXT_32K",
    "qwen-64k": "CONTEXT_64K",
    "qwen-128k": "CONTEXT_128K",
    "qwen-256k": "CONTEXT_256K",
}


def _messages(context):
    messages = []
    for message in getattr(context, "raw_messages", ()) or ():
        if not isinstance(message, dict):
            continue
        messages.append({
            "role": message.get("role") or "user",
            "content": message.get("content") if message.get("content") is not None else "",
        })
    return messages


class ContextTierClassifier:
    async def classify(self, context):
        messages = _messages(context)
        if not messages:
            return "CONTEXT_32K"
        try:
            import litellm
            counted = int(litellm.token_counter(model="gpt-4o-mini", messages=messages))
        except Exception:
            text = "\n".join(str(message.get("content") or "") for message in messages)
            counted = max(1, (len(text) + 1) // 2)
        try:
            route = select_tier(counted, DEFAULT_OUTPUT_RESERVE)
        except ContextTooLong:
            return "CONTEXT_256K"
        return TIER_BY_MODEL[route.model]


classifier = ContextTierClassifier()
