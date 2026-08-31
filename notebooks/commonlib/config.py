
class Config:
    """Notebook path configuration from environment variables.

    TERRAFORM_LOG_PATH is the raw capture file (required).
    Normalized JSON caches are derived automatically next to the capture file
    (see normalized_cache.terraform_cache_path / sdk_cache_path).
    Set DISABLE_NORMALIZED_CACHE=1 to skip reading and writing cache files.
    Set FORCE_RENORMALIZE=1 to ignore an existing cache file and rebuild it.
    """

    def __init__(self):
        import os

        self.TERRAFORM_LOG_PATH = os.getenv("TERRAFORM_LOG_PATH", "")
        self.DISABLE_NORMALIZED_CACHE = os.getenv("DISABLE_NORMALIZED_CACHE", "")
        self.FORCE_RENORMALIZE = os.getenv("FORCE_RENORMALIZE", "")
