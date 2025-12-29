"""Token cost tracking for different AI models.

This module provides token cost information and utilities for calculating
costs based on token usage from model API responses.
"""

from agents import Usage

# Token costs per million tokens (USD)
# Note: These are placeholder costs and should be updated with actual pricing
# from the respective providers. The models mentioned (GPT-5.2, Gemini 2.5 Pro Flash,
# Grok-4, Opus 4.5) may be fictional or future models, so these are estimated costs
# based on typical pricing patterns for similar model tiers.
TOKEN_COSTS_PER_MILLION = {
    # GPT-5.2 (estimated high-tier model costs)
    "gpt-5.2": {
        "input": 10.00,  # Cost per million input tokens
        "output": 30.00,  # Cost per million output tokens
    },
    # Gemini 2.5 Pro Flash (estimated based on Gemini Pro patterns)
    "gemini-2.5-pro-flash": {
        "input": 1.25,
        "output": 5.00,
    },
    "google/gemini-flash": {
        "input": 0.075,
        "output": 0.30,
    },
    # Grok-4 (estimated high-performance model)
    "grok-4": {
        "input": 5.00,
        "output": 15.00,
    },
    # Opus 4.5 (estimated based on Claude Opus tier)
    "opus-4.5": {
        "input": 15.00,
        "output": 75.00,
    },
}


def calculate_cost(usage: Usage, model_name: str = "") -> float:
    """Calculate the cost based on token usage and model name.

    Args:
        usage: Usage object from result.context_wrapper.usage
        model_name: Name of the model used (e.g., "gpt-5.2", "gemini-2.5-pro-flash")

    Returns:
        Total cost in USD

    Example:
        >>> from agents import Usage
        >>> usage = Usage(input_tokens=1000, output_tokens=500, total_tokens=1500)
        >>> cost = calculate_cost(usage, "gpt-5.2")
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
        cost_info = {"input": 1.00, "output": 3.00}

    # Calculate cost: (tokens / 1,000,000) * cost_per_million
    input_cost = (usage.input_tokens / 1_000_000) * cost_info["input"]
    output_cost = (usage.output_tokens / 1_000_000) * cost_info["output"]

    return input_cost + output_cost
