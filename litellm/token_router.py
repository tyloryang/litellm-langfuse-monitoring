"""Choose the shortest context tier that can hold a request."""

TIERS = (
    ("qwen-32k", 32768),
    ("qwen-64k", 65536),
    ("qwen-128k", 131072),
    ("qwen-256k", 262144),
)
AUTO_MODEL = "qwen-auto"
DEFAULT_OUTPUT_RESERVE = 1024


class ContextTooLong(ValueError):
    def __init__(self, needed, limit):
        self.needed = needed
        self.limit = limit
        super().__init__("need %s tokens; largest tier is %s" % (needed, limit))


class Route:
    def __init__(self, model, limit, input_tokens, output_tokens):
        self.model = model
        self.limit = limit
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    @property
    def needed(self):
        return self.input_tokens + self.output_tokens


def select_tier(input_tokens, output_tokens=DEFAULT_OUTPUT_RESERVE):
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be non-negative")
    needed = input_tokens + output_tokens
    for name, limit in TIERS:
        if needed <= limit:
            return Route(name, limit, input_tokens, output_tokens)
    raise ContextTooLong(needed, TIERS[-1][1])
