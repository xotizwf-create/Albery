#!/usr/bin/env python3
import sys
sys.path.insert(0, "/var/www/albery")
from dotenv import load_dotenv
load_dotenv("/var/www/albery/.env")
import app  # noqa
import wb_cabinet as wc
c = wc.WBClient()
print("finance:", wc.sync_finance_backfill(c, 182), flush=True)
print("adv:", wc.sync_adv_backfill(c, 182), flush=True)
print("DONE", flush=True)
