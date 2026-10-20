"""Builds the model object the agents use. Works with Azure OpenAI or the OpenAI platform."""

from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from openai import AsyncAzureOpenAI, AsyncOpenAI

from . import config


def build_model() -> OpenAIChatCompletionsModel:
    problems = config.llm_config_problems()
    if problems:
        raise RuntimeError("LLM is not configured. Fix these in your .env file: " + "; ".join(problems))

    if config.LLM_PROVIDER == "azure":
        # Traces normally go to the OpenAI platform. With Azure we keep everything inside Azure.
        set_tracing_disabled(True)
        endpoint = config.AZURE_OPENAI_ENDPOINT
        for suffix in ("/openai/v1", "/openai"):
            if endpoint.endswith(suffix):
                endpoint = endpoint[: -len(suffix)]
        if config.AZURE_OPENAI_API_VERSION:
            # Older style Azure endpoint that needs an api-version
            client = AsyncAzureOpenAI(azure_endpoint=endpoint, api_key=config.AZURE_OPENAI_API_KEY,
                                      api_version=config.AZURE_OPENAI_API_VERSION)
        else:
            # Azure OpenAI v1 endpoint: no api-version needed
            client = AsyncOpenAI(base_url=f"{endpoint}/openai/v1/", api_key=config.AZURE_OPENAI_API_KEY)
        return OpenAIChatCompletionsModel(model=config.AZURE_OPENAI_DEPLOYMENT, openai_client=client)

    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    return OpenAIChatCompletionsModel(model=config.OPENAI_MODEL, openai_client=client)
