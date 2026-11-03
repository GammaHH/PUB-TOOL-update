class ProviderError(RuntimeError):
    """A provider could not return a trustworthy resolution."""


class MalformedProviderResponse(ProviderError):
    """A provider response was structurally invalid or incomplete."""


class ProviderTimeout(ProviderError):
    """A provider request timed out."""
