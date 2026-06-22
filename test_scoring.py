import httpx
import json

BASE = "http://localhost:8000"

GOOD_JOB = {
    "job_id": "test-001",
    "title": "AI Analyst",
    "company": "Palantir Technologies",
    "location": "New York, NY (Hybrid)",
    "source": "greenhouse",
    "description": """
    We're looking for an AI Analyst to join our Forward Deployed Engineering team.

    Responsibilities:
    - Analyze complex datasets using Python and SQL to surface actionable insights
    - Build and deploy ML models using scikit-learn and PyTorch
    - Work with LangChain and Claude API to build intelligent agents
    - Create dashboards in Tableau and Power BI for client stakeholders
    - Collaborate directly with clients to implement and iterate on AI solutions

    Requirements:
    - 0-2 years of experience in a data or AI role
    - Strong Python and SQL skills required
    - Experience with ML frameworks (scikit-learn, PyTorch, TensorFlow)
    - Familiarity with LLMs, prompt engineering, and agentic systems
    - Excellent communication skills — you will present to non-technical audiences
    - Bachelor's or Master's degree in a quantitative field

    Visa sponsorship available for qualified candidates.
    We are an equal opportunity employer committed to diversity and inclusion.
    Benefits include health, dental, vision, 401k, and unlimited PTO.
    """,
}

BAD_FIT = {
    "job_id": "test-002",
    "title": "Senior Java Backend Engineer",
    "company": "Legacy Corp",
    "location": "Dallas, TX (On-site)",
    "source": "lever",
    "description": """
    Senior Java Backend Engineer with 8+ years required.
    Spring Boot, Hibernate, Oracle DB, Kubernetes.
    Must be a US citizen or permanent resident. No visa sponsorship.
    """,
}


def show(title, resp):
    print("\n" + "=" * 55)
    print(title)
    print("=" * 55)
    try:
        print(json.dumps(resp.json(), indent=2))
    except Exception:
        print(f"Status {resp.status_code}: {resp.text}")


def run():
    # 1. health check first — is the service even up?
    try:
        h = httpx.get(f"{BASE}/health", timeout=5)
        print(f"Health check: {h.json()}")
    except Exception as e:
        print(f"❌  Service not reachable at {BASE}. Is `python run.py` running?\n{e}")
        return

    show("TEST 1: single score — strong fit (expect high score, open, apply true)",
         httpx.post(f"{BASE}/scoring/score", json=GOOD_JOB, timeout=30))

    show("TEST 2: single score — poor fit (expect low score, closed, apply false)",
         httpx.post(f"{BASE}/scoring/score", json=BAD_FIT, timeout=30))

    show("TEST 3: batch score — both (expect error_count 0, apply_count 1)",
         httpx.post(f"{BASE}/scoring/score-batch", json={"jobs": [GOOD_JOB, BAD_FIT]}, timeout=60))

    show("TEST 4: resume gap — strong fit (expect strengths, severity, recommendation)",
         httpx.post(f"{BASE}/scoring/resume-gap", json=GOOD_JOB, timeout=30))


if __name__ == "__main__":
    run()