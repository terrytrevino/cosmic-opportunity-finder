# COSMIC Opportunity Finder

A Streamlit front end for searching SAM.gov space, ISAM, and engineering contract opportunities.

## Current v2 search

- Uses the selected Product and Service Codes and active response deadlines as hard search boundaries.
- Defaults to the baseline keywords `space` and `orbit`.
- Supports either strict keyword filtering or keyword-weighted ranking.
- Removes unselected PSC families and suppresses off-domain maritime results without a clear space signal.
- Provides a five-column Opportunity Marketplace CSV and a full diagnostic CSV.
- Keeps the legacy agency-and-NAICS search available for comparison.

## Local run

1. Install Python 3.11+.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`.
4. Add your SAM.gov API key.
5. Optionally add a Slack Workflow trigger URL.
6. Run:

```bash
streamlit run streamlit_app.py
```

## Streamlit Community Cloud deployment

1. Push this repository to GitHub.
2. In Streamlit Community Cloud, create a new app from this repository.
3. Set the entry point to `streamlit_app.py`.
4. Add the following secrets in the app's Secrets settings:

```toml
SAM_API_KEY = "SAM-..."
SLACK_WEBHOOK_URL = "https://hooks.slack.com/triggers/..."
```

5. Deploy.

## Security

Never commit `.streamlit/secrets.toml`, SAM API keys, or Slack webhook URLs to GitHub.
