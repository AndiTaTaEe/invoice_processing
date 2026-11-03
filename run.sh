#!/usr/bin/env bash
set -a; source .env; set +a
python bpa_runner.py --project A "$@" -- python invoice_processing_a.py {base_url}