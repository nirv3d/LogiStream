import math
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
import yaml

DSN = os.getenv(
    "LOGISTREAM_DSN",
    "host=localhost port=5432 dbname=logistics user=logistics password=logistics",
)