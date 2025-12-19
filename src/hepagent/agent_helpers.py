
from agents import Usage


def print_usage(usage: Usage) -> None:
    print("\n=== Usage ===")
    print(f"Input tokens: {usage.input_tokens}")
    print(f"Output tokens: {usage.output_tokens}")
    print(f"Total tokens: {usage.total_tokens}")
    print(f"Requests: {usage.requests}")
    for i, request in enumerate(usage.request_usage_entries):
        print(f"  {i + 1}: {request.input_tokens} input, {request.output_tokens} output")