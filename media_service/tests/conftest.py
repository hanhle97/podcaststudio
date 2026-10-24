import os
import tempfile

# Isolate runtime data before app modules read settings.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="podcast-test-")
