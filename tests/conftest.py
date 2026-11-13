import os

os.environ.setdefault("GRIDMAP_NO_EXTRAS", "1")  # extras read the real data dir; unit tests use tmp dirs
