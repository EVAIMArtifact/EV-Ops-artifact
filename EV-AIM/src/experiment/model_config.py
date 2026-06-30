"""
Model configuration for LLM clients in AIM-EVM experiments.

This module provides a simple configuration class to pass LLM parameters
to the planner and executor modules.
"""

from typing import Optional


class ModelConfig:
    """
    Configuration for LLM model parameters.

    Attributes:
        client: LLM client type ("gpt", "claude", "gemini", "watsonx")
        model_id: Model identifier (e.g., "gpt-4o", "claude-sonnet-4-5")
        api_key: API key for authentication
        endpoint: API endpoint URL (optional, required for watsonx)
        temperature: Sampling temperature (0.0-1.0)
        max_tokens: Maximum tokens in response
    """

    def __init__(
        self,
        client: str,
        model_id: str,
        api_key: str,
        endpoint: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 2000
    ):
        self.client = client
        self.model_id = model_id
        self.api_key = api_key
        self.endpoint = endpoint
        self.temperature = temperature
        self.max_tokens = max_tokens

    def to_dict(self):
        """Convert configuration to dictionary format."""
        config = {
            "client": self.client,
            "model_id": self.model_id,
            "api_key": self.api_key,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens
        }

        if self.endpoint:
            config["endpoint"] = self.endpoint

        return config
