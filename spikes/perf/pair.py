"""Pair a device in the database under SENNET_HOME and print its cookie token.

Only for the throwaway instance: never run this against your real data directory.
"""
import os
import sys

from sennet import access
from sennet.config import Paths
from sennet.db import Database

if not os.environ.get("SENNET_HOME"):
    sys.exit("set SENNET_HOME to the throwaway instance's directory")
db = Database(Paths.default().database)
pairing = access.new_pairing(db, access.TERMINAL, 60)
_, token = access.redeem(db, pairing.link_code, "perf spike")
print(token)
