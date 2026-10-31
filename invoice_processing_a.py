import logging, sys
import os
import json
from invoice_processing.http_client import HTTPClient, APIError, RunRequestLimit, path_segment

# set up logging to log to stderr with level INFO
logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")

def main():
    # read BASE_URL as first cli argument, otherwise read from BPA_BASE_URL
    if len(sys.argv) > 1:
        base_url  = sys.argv[1] # read base_url from first command line argument
    else:
        base_url = os.environ.get("BPA_BASE_URL") 
    if not base_url:
        logging.error("BASE_URL not provided. Please provide it as a command line argument or set BPA_BASE_URL environment variable.")
        sys.exit(2) 

    # create an HTTPClient with the base_url
    client = HTTPClient(base_url)

    # run a test request to /context, /config and /inbox endpoints to verify the client is working
    try:
        context = client.get("/context")
        config = client.get("/config")
        inbox = client.get("/inbox")
    except APIError as err:
        # if an APIError occurs, log the error details and exit with status code 1
        logging.error("APIError: %s", err)
        sys.exit(1)
    except RunRequestLimit as err:
        # if a RunRequestLimit error occurs, log the error details and exit with status code 1
        logging.error("RunRequestLimit: %s", err)
        sys.exit(1)
    # log the successful responses from the test requests
    logging.info("Context:\n%s", json.dumps(context, indent=2, ensure_ascii=False))
    logging.info("Config:\n%s", json.dumps(config, indent=2, ensure_ascii=False))
    logging.info("Inbox (%d documents):\n%s", len(inbox), json.dumps(inbox, indent=2, ensure_ascii=False))

    # if the test request is successful, exit with status code 0
    sys.exit(0)

if __name__ == "__main__":
    main()
    

