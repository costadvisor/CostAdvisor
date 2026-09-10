from app.services.providers.base import ProviderAdapter, ProviderCredentialError, ProviderPoint
from app.services.providers.fastmarkets import FastmarketsAdapter

PROVIDER_REGISTRY: dict[str, type[ProviderAdapter]] = {
    "fastmarkets": FastmarketsAdapter,
}

# Single source of truth for what a client can offer to configure — includes
# providers named in the ticket that have no adapter yet, so a picker can show
# them as "coming soon" instead of hiding them or lying about availability.
KNOWN_PROVIDERS = [
    {"key": "fastmarkets", "label": "Fastmarkets", "adapter_available": True},
    {"key": "argus", "label": "Argus", "adapter_available": False},
    {"key": "icis", "label": "ICIS", "adapter_available": False},
]


def get_adapter(provider: str) -> ProviderAdapter:
    provider = provider.strip().lower()
    adapter_cls = PROVIDER_REGISTRY.get(provider)
    if not adapter_cls:
        raise ProviderCredentialError("error", f"Provider '{provider}' is not yet supported")
    return adapter_cls()


__all__ = [
    "ProviderAdapter", "ProviderCredentialError", "ProviderPoint",
    "PROVIDER_REGISTRY", "KNOWN_PROVIDERS", "get_adapter",
]
