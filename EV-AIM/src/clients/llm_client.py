"""
Unified LLM client implementations for IBM Watsonx and OpenAI GPT models.

This module provides a common interface for generating text completions
from different LLM providers with built-in retry logic and error handling.

Example:
    >>> model_config = {
    ...     "client": "gpt",
    ...     "model_id": "gpt-4",
    ...     "api_key": "sk-...",
    ...     "temperature": 0.2,
    ...     "max_tokens": 1024
    ... }
    >>> client = create_llm_client(model_config)
    >>> response = client.generate(
    ...     system_prompt="You are a helpful assistant.",
    ...     user_prompt="What is Kubernetes?"
    ... )
"""

import time
import requests
from typing import Dict, Any
from openai import OpenAI

class IBMLLMClient:
    """
    Client for IBM Watsonx LLM API.

    Provides chat completion with retry logic for IBM's RITS API endpoint.
    Automatically retries failed requests up to 3 times.

    Args:
        model_config: Configuration dictionary with keys:
            - model_id (str): IBM model identifier
            - api_key (str): RITS API key
            - endpoint (str): IBM API endpoint URL
            - temperature (float, optional): Sampling temperature (default: 0.2)
            - max_tokens (int, optional): Maximum tokens to generate (default: 1024)

    Raises:
        RuntimeError: If all 3 retry attempts fail

    Example:
        >>> config = {
        ...     "model_id": "ibm/granite-13b-chat",
        ...     "api_key": "your-api-key",
        ...     "endpoint": "https://your-endpoint/v1/chat/completions",
        ...     "temperature": 0.2
        ... }
        >>> client = IBMLLMClient(config)
        >>> response = client.generate("You are helpful.", "Hello!")
    """

    def __init__(self, model_config: Dict[str, Any]):
        self.model_id = model_config["model_id"]
        self.api_key = model_config["api_key"]
        self.endpoint = model_config["endpoint"]
        self.temperature = model_config.get("temperature", 0.2)
        self.max_tokens = model_config.get("max_tokens", 1024)

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """
        Generate completion from IBM Watsonx model.

        Args:
            system_prompt: System-level instruction for the model
            user_prompt: User message/query

        Returns:
            Generated text response from the model

        Raises:
            RuntimeError: If all retry attempts fail
        """
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "RITS_API_KEY": self.api_key
        }

        body = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": self.temperature,
            "seed": 10,
        }

        for attempt in range(3):
            try:
                response = requests.post(self.endpoint, headers=headers, json=body)
                if response.status_code == 200:
                    data = response.json()
                    if "choices" in data and len(data["choices"]) > 0:
                        content = data["choices"][0]["message"]["content"]
                        return content
                else:
                    print(f"⚠️ IBM LLM call failed with {response.status_code}: {response.text}")
            except Exception as e:
                print(f"⚠️ Exception calling IBM LLM: {e}")

        raise RuntimeError("IBM LLM call failed after 3 attempts")


class GPTLLMClient:
    """
    Client for OpenAI GPT models.

    Provides chat completion with retry logic and exponential backoff.
    Uses OpenAI's official Python client library.

    Args:
        model_config: Configuration dictionary with keys:
            - model_id (str): GPT model identifier (e.g., "gpt-4", "gpt-3.5-turbo")
            - api_key (str): OpenAI API key
            - temperature (float, optional): Sampling temperature (default: 0.2)
            - max_tokens (int, optional): Maximum tokens to generate (default: 1024)

    Raises:
        RuntimeError: If all 3 retry attempts fail

    Example:
        >>> config = {
        ...     "model_id": "gpt-4",
        ...     "api_key": "sk-...",
        ...     "temperature": 0.2,
        ...     "max_tokens": 2048
        ... }
        >>> client = GPTLLMClient(config)
        >>> response = client.generate("You are helpful.", "Explain Kubernetes.")
    """

    def __init__(self, model_config: Dict[str, Any]):
        self.model = model_config["model_id"]
        self.temperature = model_config.get("temperature", 0.2)
        self.max_tokens = model_config.get("max_tokens", 1024)
        self.client = OpenAI(api_key=model_config["api_key"])

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """
        Generate completion from OpenAI GPT model.

        Args:
            system_prompt: System-level instruction for the model
            user_prompt: User message/query

        Returns:
            Generated text response from the model

        Raises:
            RuntimeError: If all retry attempts fail
        """
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    max_completion_tokens=self.max_tokens,
                    messages=[  # type: ignore[arg-type]
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ]
                )
                return resp.choices[0].message.content

            except Exception as e:
                print(f"⚠️ GPT call failed (attempt {attempt + 1}): {e}")
                time.sleep(3)

        raise RuntimeError("GPT LLM call failed after 3 attempts")


class GeminiLLMClient:
    """
    Client for Google Gemini models.

    Provides chat completion with retry logic using Google's Generative AI SDK.
    Optimized for Gemini 2.0 Flash with improved instruction adherence.

    Args:
        model_config: Configuration dictionary with keys:
            - model_id (str): Gemini model identifier (e.g., "gemini-2.0-flash-exp")
            - api_key (str): Google AI API key
            - temperature (float, optional): Sampling temperature (default: 0.1)
            - max_tokens (int, optional): Maximum tokens to generate (default: 8192)

    Raises:
        RuntimeError: If all 3 retry attempts fail

    Example:
        >>> config = {
        ...     "model_id": "gemini-2.0-flash-exp",
        ...     "api_key": "AIza...",
        ...     "temperature": 0.1,
        ...     "max_tokens": 8192
        ... }
        >>> client = GeminiLLMClient(config)
        >>> response = client.generate("You are helpful.", "Explain Kubernetes.")
    """

    def __init__(self, model_config: Dict[str, Any]):
        self.model_id = model_config["model_id"]
        self.temperature = model_config.get("temperature", 0.1)
        self.max_tokens = model_config.get("max_tokens", 8192)

        # Initialize GenAI client
        self.client = genai.Client(
            api_key=model_config["api_key"]
        )

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """
        Generate completion from Google Gemini model.
        """

        contents = [
            {"role": "system", "parts": [{"text": system_prompt}]},
            {"role": "user", "parts": [{"text": user_prompt}]},
        ]

        for attempt in range(3):
            try:
                response = self.client.models.generate_content(
                    model=self.model_id,
                    contents=contents,
                    config={
                        "temperature": self.temperature,
                        "top_p": 0.95,
                        "max_output_tokens": self.max_tokens,
                    },
                )

                # Standard text extraction
                if response.text:
                    return response.text

                raise ValueError("No text content in Gemini response")

            except Exception as e:
                print(f"⚠️ Gemini call failed (attempt {attempt + 1}): {e}")
                time.sleep(3)

        raise RuntimeError("Gemini LLM call failed after 3 attempts")

class ClaudeLLMClient:
    """
    Client for Anthropic Claude models.

    Provides chat completion with retry logic using Anthropic's official Python SDK.
    Optimized for Claude Sonnet 4.5 with superior instruction adherence and constraint-following.

    Args:
        model_config: Configuration dictionary with keys:
            - model_id (str): Claude model identifier (e.g., "claude-sonnet-4-5-20250929")
            - api_key (str): Anthropic API key
            - temperature (float, optional): Sampling temperature (default: 0.0)
            - max_tokens (int, optional): Maximum tokens to generate (default: 8192)

    Raises:
        RuntimeError: If all 3 retry attempts fail

    Example:
        >>> config = {
        ...     "model_id": "claude-sonnet-4-5-20250929",
        ...     "api_key": "sk-ant-...",
        ...     "temperature": 0.0,
        ...     "max_tokens": 8192
        ... }
        >>> client = ClaudeLLMClient(config)
        >>> response = client.generate("You are helpful.", "Explain Kubernetes.")
    """

    def __init__(self, model_config: Dict[str, Any]):
        self.model_id = model_config["model_id"]
        self.temperature = model_config.get("temperature", 0.0)
        self.max_tokens = model_config.get("max_tokens", 8192)
        self.client = Anthropic(api_key=model_config["api_key"])

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """
        Generate completion from Anthropic Claude model.

        Args:
            system_prompt: System-level instruction for the model
            user_prompt: User message/query

        Returns:
            Generated text response from the model

        Raises:
            RuntimeError: If all retry attempts fail
        """
        for attempt in range(3):
            try:
                response = self.client.messages.create(
                    model=self.model_id,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    system=system_prompt,
                    messages=[
                        {"role": "user", "content": user_prompt}
                    ]
                )

                # Extract text from response
                if response.content and len(response.content) > 0:
                    return response.content[0].text
                else:
                    raise ValueError("No text content in response")

            except Exception as e:
                print(f"⚠️ Claude call failed (attempt {attempt + 1}): {e}")
                time.sleep(3)

        raise RuntimeError("Claude LLM call failed after 3 attempts")

class GroqLLMClient:
    """
    Groq client using OpenAI-compatible Chat Completions API.
    """

    def __init__(self, model_config: Dict[str, Any]):
        self.model = model_config["model_id"]
        self.temperature = model_config.get("temperature", 0.0)
        self.max_tokens = model_config.get("max_tokens", 2048)
        self.client = OpenAI(
            api_key=model_config["api_key"],
            base_url=model_config.get(
                "endpoint",
                "https://api.groq.com/openai/v1",
            ),
        )

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    max_completion_tokens=self.max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
                return resp.choices[0].message.content

            except Exception as e:
                print(f"⚠️ Groq call failed (attempt {attempt + 1}): {e}")
                time.sleep(3)

        raise RuntimeError("Groq LLM call failed after 3 attempts")


def create_llm_client(model_config: Dict[str, Any]):
    """
    Factory function to create appropriate LLM client based on configuration.

    Args:
        model_config: Configuration dictionary with 'client' key specifying
            provider type ("gpt", "gemini", "claude", or "ibm"), plus provider-specific parameters.

    Returns:
        IBMLLMClient, GPTLLMClient, GeminiLLMClient, or ClaudeLLMClient instance

    Raises:
        ValueError: If unsupported client type is specified

    Example:
        >>> config = {"client": "gpt", "model_id": "gpt-4", "api_key": "sk-..."}
        >>> client = create_llm_client(config)
        >>> isinstance(client, GPTLLMClient)
        True
    """
    client_type = model_config.get("client", "gpt")

    if client_type == "gpt":
        print("[INFO] LLM client: GPT")
        return GPTLLMClient(model_config)

    elif client_type == "gemini":
        print("[INFO] LLM client: Google Gemini")
        return GeminiLLMClient(model_config)

    elif client_type == "claude":
        print("[INFO] LLM client: Anthropic Claude")
        return ClaudeLLMClient(model_config)

    elif client_type == "ibm":
        print("[INFO] LLM client: IBM Watsonx")
        return IBMLLMClient(model_config)
    elif client_type == "groq":
        print("[INFO] LLM client: Groq")
        return GroqLLMClient(model_config)

    else:
        raise ValueError(f"Unsupported LLM client type: {client_type}")

def preload_llm_dependencies(model_config: Dict[str, Any]) -> None:
    """
    Preload (eagerly import) LLM provider dependencies to:
    - avoid lazy-import latency during execution
    - make startup cost explicit and measurable
    - fail fast if a dependency is missing

    Call this ONCE at experiment start.
    """
    client_type = model_config.get("client", "gpt")

    if client_type == "gpt":
        print("[INFO] Preloading OpenAI SDK")
        from openai import OpenAI  # noqa: F401

    elif client_type == "gemini":
        print("[INFO] Preloading Google GenAI SDK")
        from google import genai  # noqa: F401

    elif client_type == "claude":
        print("[INFO] Preloading Anthropic SDK")
        from anthropic import Anthropic  # noqa: F401

    elif client_type == "ibm":
        print("[INFO] IBM Watsonx uses requests only (no SDK preload)")
    
    elif client_type == "groq":
        print("[INFO] Preloading OpenAI SDK for Groq")
        from openai import OpenAI  # noqa: F401

    else:
        raise ValueError(f"Unsupported LLM client type: {client_type}")
