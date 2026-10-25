import os
import tempfile

# Isolate runtime data before app modules read settings.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="podcast-test-")
# Never hit a real Voice Studio or LLM from unit tests.
os.environ["VOICE_STUDIO_URL"] = ""
os.environ["VOICE_STUDIO_VOICE_ID"] = "test-voice"
os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = ""
for _key in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REDIRECT_URI"):
    os.environ[_key] = ""
