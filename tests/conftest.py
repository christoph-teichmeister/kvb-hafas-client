import os

# Tests never fetch the real access ID from KVB.
os.environ.setdefault("KVB_HAFAS_AID", "test-aid")
