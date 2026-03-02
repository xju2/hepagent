"""Token cost tracking for different AI models.

This module provides token cost information and utilities for calculating
costs based on token usage from model API responses.
"""

from agents import Usage

# Token costs per million tokens (USD)
# Model names should match those used in CBORG.
TOKEN_COSTS_PER_MILLION = {
    # GPT-5 (estimated high-tier model costs)
    "openai/gpt-5": {
        "input": 1.75,  # Cost per million input tokens
        "output": 15.00,  # Cost per million output tokens
    },
    # Gemini 2.5 Pro Flash (estimated based on Gemini Pro patterns)
    "gemini-2.5-pro": {
        "input": 2.5,
        "output": 15.00,
    },
    "gemini-2.5-flash": {
        "input": 0.3,
        "output": 2.5,
    },
    "google/gemini-flash": {
        "input": 0.3,
        "output": 2.5,
    },
    # Grok-4 (estimated high-performance model)
    "xai/grok-4": {
        "input": 3.00,
        "output": 15.00,
    },
    # Opus 4.5 (estimated based on Claude Opus tier)
    "claude-opus-4-5": {
        "input": 5.00,
        "output": 25.00,
    },
}


def calculate_cost(usage: Usage, model_name: str = "") -> float:
    """Calculate the cost based on token usage and model name.

    Args:
        usage: Usage object from result.context_wrapper.usage
        model_name: Name of the model used (e.g., "openai/gpt-5", "gemini-2.5-pro", "xai/grok-4")

    Returns:
        Total cost in USD

    Example:
        >>> from agents import Usage
        >>> usage = Usage(input_tokens=1000, output_tokens=500, total_tokens=1500)
        >>> cost = calculate_cost(usage, "openai/gpt-5")
        >>> print(f"Cost: ${cost:.4f}")

    Note:
        The matching logic uses substring matching and prioritizes longer matches.
        This works well for model names from known providers but could produce
        false positives for unusual model names. Consider updating the dictionary
        with exact model names as they become available.
    """
    # Try to find the cost for the specific model (exact match first)
    cost_info = TOKEN_COSTS_PER_MILLION.get(model_name.lower())

    if cost_info is None:
        # Try to match partial model names, prioritize longer matches
        # Note: This uses substring matching which works for most cases but
        # could produce false positives. The longest match is selected to
        # minimize ambiguity.
        matches = []
        for key in TOKEN_COSTS_PER_MILLION:
            if key in model_name.lower() or model_name.lower() in key:
                matches.append(key)

        # If we have matches, use the longest one (most specific)
        if matches:
            best_match = max(matches, key=len)
            cost_info = TOKEN_COSTS_PER_MILLION[best_match]

    if cost_info is None:
        # Default to a conservative estimate if model not found
        # Use GPT-5 mini cost as a baseline
        cost_info = {"input": 0.250, "output": 2.00}

    # Calculate cost: (tokens / 1,000,000) * cost_per_million
    input_cost = (usage.input_tokens / 1_000_000) * cost_info["input"]
    output_cost = (usage.output_tokens / 1_000_000) * cost_info["output"]

    return input_cost + output_cost


def print_usage(usage: Usage, model_name: str = "") -> None:
    """Print usage statistics and cost information.

    Args:
        usage: Usage object from result.context_wrapper.usage
        model_name: Optional model name to calculate accurate costs
    """
    print("\n=== Usage ===")
    print(f"Input tokens: {usage.input_tokens}")
    print(f"Output tokens: {usage.output_tokens}")
    print(f"Total tokens: {usage.total_tokens}")
    print(f"Requests: {usage.requests}")
    for i, request in enumerate(usage.request_usage_entries):
        print(f"  {i + 1}: {request.input_tokens} input, {request.output_tokens} output")

    # Calculate and print cost
    if model_name:
        cost = calculate_cost(usage, model_name)
        print("\n=== Cost ===")
        print(f"Model: {model_name}")
        print(f"Total cost: ${cost:.4f}")
    else:
        print("\n(Provide model_name parameter to calculate cost)")
