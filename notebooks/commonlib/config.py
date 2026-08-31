
class Config:
    """Notebook path configuration from environment variables.

    TERRAFORM_LOG_PATH is the input capture file (read by all notebooks).
    NORMALIZED_* paths are optional write-only outputs; nothing reads them back in.
    """

    def __init__(self):
        import os

        self.TERRAFORM_LOG_PATH = os.getenv("TERRAFORM_LOG_PATH", "")
        # Written by normalize_records() in export/plan/apply prep modules; not read as input.
        self.NORMALIZED_TERRAFORM_LOG_PATH = os.getenv("NORMALIZED_TERRAFORM_LOG_PATH", "")
        # Written by normalize_records() in prep_sdk_data; not read as input.
        self.NORMALIZED_GENESYS_SDK_PATH = os.getenv("NORMALIZED_GENESYS_SDK_PATH", "")