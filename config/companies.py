"""
Target companies for direct-ATS job discovery.
Slugs are verified live during testing — expect to correct a few.
Grouped by theme for readability; the code treats them as one flat list.
"""

COMPANIES = [
    # ── AI labs & frontier model companies ──
    ("Anthropic",      "anthropic"),
    ("OpenAI",         "openai"),
    ("Cohere",         "cohere"),
    ("Mistral AI",     "mistral"),
    ("Hugging Face",   "huggingface"),
    ("Scale AI",       "scaleai"),
    ("Perplexity",     "perplexity"),
    ("Together AI",    "togetherai"),

    # ── AI infrastructure & tooling ──
    ("Pinecone",       "pinecone"),
    ("LangChain",      "langchain"),
    ("Weaviate",       "weaviate"),
    ("Weights & Biases","wandb"),
    ("Modal",          "modal"),
    ("Baseten",        "baseten"),

    # ── AI applications / agents ──
    ("ElevenLabs",     "elevenlabs"),
    ("Sierra",         "sierra"),
    ("Decagon",        "decagon"),
    ("Glean",          "glean"),
    ("Harvey",         "harvey"),
    ("Cresta",         "cresta"),

    # ── Dev tools & platforms (hire AI + data roles) ──
    ("Retool",         "retool"),
    ("Vercel",         "vercel"),
    ("Replit",         "replit"),
    ("Temporal",       "temporal"),

    # ── Data / analytics / BI platforms (your DA & BA targets) ──
    ("Databricks",     "databricks"),
    ("Snowflake",      "snowflake"),
    ("dbt Labs",       "dbtlabs"),
    ("Sigma Computing","sigmacomputing"),
    ("Hex",            "hex"),

    # ── Product & fintech (heavy Data/Business Analyst hiring) ──
    ("Notion",         "notion"),
    ("Airtable",       "airtable"),
    ("Ramp",           "ramp"),
    ("Brex",           "brex"),
    ("Mercury",        "mercury"),
    ("Plaid",          "plaid"),
    ("Rippling",       "rippling"),
]