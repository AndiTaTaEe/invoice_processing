# Invoice Processing

Python solution for processing invoices received as UBL XML or PDF documents.
The project extracts invoice data, validates business rules, records decisions,
sends notifications, and submits the final run report to the BPA hub.

## Dependencies

- Python 3.10 or newer
- `pypdf` from `requirements.txt`
- Access to the BPA hub for end-to-end runs

Install dependencies with:

```bash
python -m pip install -r requirements.txt
```

The repository includes a virtual environment under `phase1/`. Its interpreter
can be used as `phase1/bin/python`.

## Project layout

- `src/invoice_processing/` - models, extractors, normalization, rules, and HTTP client
- `invoice_processing_a.py` - BPA workflow entry point
- `bpa_runner.py` - run orchestration and grading helper
- `tests/` - unit tests and invoice fixtures
- `requirements.txt` - Python dependencies
- `run.sh` - convenience wrapper for BPA runs

## Assumptions

- The BPA hub supplies the business date and configuration. The computer clock
  is not used for business decisions.
- Invoice identifiers are normalized by the extractors before duplicate
  detection and history queries.
- Extraction failures produce `MANUAL_REVIEW` with
  `EXTRACTION_FAILED`.
- Documents are processed in inbox order.
- Documents with an `EARLIER_RUN` decision are not downloaded again.
- Documents with a `THIS_RUN` decision are not decided again; only missing
  notifications are retried.
- Decisions and notifications use deterministic idempotency keys.
- Monetary and quantity calculations use `Decimal`.
- Only `APPROVED` decisions consume purchase-order balances and goods-receipt
  quantities.
- Limits, VAT rates, recipients, and feature flags come from `GET /config`.
- CR-1 three-way matching is enabled only when
  `config["features"]["three_way_match"]` is true.

## Running tests

Run the complete test suite:

```bash
PYTHONPATH=src phase1/bin/python -m unittest discover -s tests -v
```

Run only the rule tests:

```bash
PYTHONPATH=src phase1/bin/python -m unittest tests.test_rules -v
```

Validate extractors against downloaded fixtures:

```bash
PYTHONPATH=src phase1/bin/python tests/validate_invoice_extractors.py
```

## Running the solution

Pass the run-specific base URL as the first argument:

```bash
PYTHONPATH=src phase1/bin/python invoice_processing_a.py <BASE_URL>
```

Alternatively, set `BPA_BASE_URL`:

```bash
export BPA_BASE_URL=<BASE_URL>
PYTHONPATH=src phase1/bin/python invoice_processing_a.py
```

The solution receives only the run-specific base URL. The runner keeps BPA
credentials out of the solution process.

## Running with the BPA runner

Keep hub credentials in a local `.env` file. Never commit or expose it.

Run the public suite:

```bash
PYTHONPATH=src ./run.sh --suite public
```

Run selected scenarios:

```bash
PYTHONPATH=src ./run.sh --suite public --scenarios A-P08
```

Test restart behavior:

```bash
PYTHONPATH=src ./run.sh \
  --suite public \
  --kill-after-writes 5 \
  --invocations 2
```

Run a sandbox suite with another seed:

```bash
PYTHONPATH=src ./run.sh --suite sandbox --seed 42
```

Run the Phase 1 change request:

```bash
PYTHONPATH=src ./run.sh \
  --suite change-request \
  --change-request CR-1
```

The runner completes the run and prints the hub report with expected and
actual values for failed checks.

## Security

- Keep `.env` out of version control.
- Rotate credentials if they have been exposed.
- Do not hardcode hub keys, tokens, or supplier credentials.
